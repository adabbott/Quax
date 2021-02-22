import torch
import numpy as np
import math
torch.set_printoptions(precision=5,linewidth=300)
torch.set_default_dtype(torch.float64)
ang2bohr = 1 / 0.52917720859

@torch.jit.script
def vectorized_oei(basis, geom, nbf_per_atom, charge_per_atom):
    '''Computes overlap, kinetic, and potential energy integrals over s orbital basis functions
       Parameters
       ----------
       basis : torch.tensor() of shape (n,), where n is number of basis functions
            A vector of orbital exponents in the same order as the atom order in 'geom'.
            That is, you must concatentate the basis functions of each atom together before passing to this function.
       geom : torch.tensor() of shape (N x 3), where N is number of atoms
            The cartesian coordinates 
       nbf_per_atom: torch.tensor() of shape (N,)
            The number of basis functions for each atom (so we know which center (cartesian coordinate) goes with which basis function)
       charge_per_atom: torch.tensor() of shape (N,)
            The charge of the nucleus for each atom.
       NOTE: In the future you should just make a single argument which contains all orbital exponents and their corresponding centers pre-prepared
    '''
    # SETUP AND OVERLAP INTEGRALS
    nbf = torch.numel(basis)
    # 'centers' are the cartesian centers ((nbf,3) array) corresponding to each basis function, in the same order as the 'basis' vector
    centers = geom.repeat_interleave(nbf_per_atom, dim=0).reshape(-1,3)
    # Construct Normalization constant product array, Na * Nb component
    norm = (2 * basis / math.pi)**(3/4)
    normtensor = torch.ger(norm,norm) # outer product => every possible combination of Na * Nb
    # Construct pi / aa + bb ** 3/2 term
    aa_times_bb = torch.ger(basis,basis)
    aa_plus_bb = basis.expand(nbf,-1) + torch.transpose(basis.expand(nbf,-1),0,1) # doesnt copy data, unlike repeat(). may not work, but very efficient
    term = (math.pi / aa_plus_bb) ** (3/2)
    # Construct gaussian product coefficient array, c = exp(A-B dot A-B) * ((-aa * bb) / (aa + bb))
    tmpA = centers.expand(nbf,nbf,3)
    AminusB = tmpA - torch.transpose(tmpA, 0,1) #caution: tranpose shares memory with original array. changing one changes the other
    AmBAmB = torch.einsum('ijk,ijk->ij', AminusB, AminusB)
    coeff = torch.exp(AmBAmB * (-aa_times_bb / aa_plus_bb))
    S = normtensor * coeff * term
    # KINETIC INTEGRALS
    P = aa_times_bb / aa_plus_bb
    T = S * (3 * P + 2 * P * P * -AmBAmB)
    # Construct gaussian product center array, R = (aa * A + bb * B) / (aa + bb)
    # First construct every possible sum of exponential-weighted cartesian centers, aa*A + bb*B 
    aatimesA = torch.einsum('i,ij->ij', basis,centers)
    # This is a 3D tensor (nbf,nbf,3), where each row is a unique sum of two exponent-weighted cartesian centers
    numerator = aatimesA[:,None,:] + aatimesA[None,:,:]
    R = torch.einsum('ijk,ij->ijk', numerator, 1/aa_plus_bb)
    # Now we must subtract off the atomic coordinates, for each atom, introducing yet another dimension, where we expand according to number of atoms
    R_per_atom = R.expand(geom.size()[0],-1,-1,-1)
    expanded_geom = torch.transpose(geom.expand(nbf,nbf,-1,-1), 0,2)
    # Subtract off atom coordinates
    Rminusgeom = R_per_atom - expanded_geom
    # Now contract along the coordinate dimension, and weight by aa_plus_bb. This is the boys function argument.
    # arg = (aa+bb) * torch.dot(R - atom, R - atom)
    contracted = torch.einsum('ijkl,ijkl->ijk', Rminusgeom,Rminusgeom)
    boys_arg = torch.einsum('ijk,jk->ijk', contracted, aa_plus_bb)
    # Now evaluate the boys function on all elements, multiply by charges, and then sum the atom dimension
    # it is safe to sum here, since every other operation in the integral expression is linear
    #F = boys(torch.tensor(0.0), boys_arg)
    F = torch.zeros_like(boys_arg)
    mask1 = boys_arg <= 1e-8
    mask2 = boys_arg > 1e-8
    F[mask1] = 1 - boys_arg[mask1] / 3 
    F[mask2] = torch.erf(torch.sqrt(boys_arg[mask2])) * math.sqrt(math.pi) / (2 * torch.sqrt(boys_arg[mask2]))
    Fcharge = -charge_per_atom[:,None,None] * F[:,...]
    Ffinal = torch.sum(Fcharge, dim=0)
    V = Ffinal * normtensor * coeff * 2 * math.pi / aa_plus_bb
    return S, T, V

@torch.jit.script
def vectorized_tei(basis, geom, nbf_per_atom):
    '''Computes two electron integrals over s orbital basis functions
       Parameters
       ----------
       basis : torch.tensor() of shape (n,), where n is number of basis functions
            A vector of orbital exponents in the same order as the atom order in 'geom'.
            That is, you must concatentate the basis functions of each atom together before passing to this function.
       geom : torch.tensor() of shape (N x 3), where N is number of atoms
            The cartesian coordinates in the same order as the concatenated basis functions 
       nbf_per_atom: torch.tensor() of shape (N,)
            The number of basis functions for each atom (so we know which center (cartesian coordinate) goes with which basis function)
    '''
    # Faster and more memory efficient approach: Pre-allocate space for ERIs and perform in-place ops (mostly elementwise multiplications)
    # The operations required for G: F * c1 * c2 * normtensor * 2 * math.pi**2 / (aa_plus_bb * cc_plus_dd) * torch.sqrt(math.pi / (aa_plus_bb + cc_plus_dd))
    # where the variables are the boys function term, gaussian product coefficients for AB and CD gaussians, normalization constants, and simpler terms.
    nbf = torch.numel(basis)
    G = torch.ones(nbf,nbf,nbf,nbf)
    # 'centers' are the cartesian centers ((nbf,3) array) corresponding to each basis function, in the same order as the 'basis' vector
    centers = geom.repeat_interleave(nbf_per_atom, dim=0).reshape(-1,3)
    # Construct every combination of normalization constants 
    norm = (2 * basis / math.pi)**(3/4)
    G.mul_(torch.einsum('i,j,k,l',norm,norm,norm,norm))
    G.mul_(2 * math.pi**2)
    # Obtain miscellaneous terms 
    # (i,l,j,k) + (l,i,j,k) ---> (i+l,i+l,j+j,k+k) ---> (A+D,D+A,C+C,B+B) which is just (A+D,A+D,C+C,B+B)
    tmp1 = basis.expand(nbf,nbf,nbf,-1)
    aa_plus_bb = tmp1.permute(0,3,1,2) + tmp1.permute(3,0,1,2)
    #cc_plus_dd = aa_plus_bb.permute(2,3,0,1) # this need not be saved.
    G.mul_(1 / (aa_plus_bb * aa_plus_bb.permute(3,2,0,1)))
    G.mul_(torch.sqrt(math.pi / (aa_plus_bb + aa_plus_bb.permute(3,2,0,1))))
    aa_times_bb = tmp1.permute(0,3,1,2) * tmp1.permute(3,0,1,2)
    #cc_times_dd = aa_times_bb.permute(2,3,0,1) # this need not be saved 
    # Obtain gaussian product coefficients
    tmp2 = centers.expand(nbf,nbf,nbf,nbf,3)
    AminusB = tmp2.permute(0,3,1,2,4) - tmp2.permute(3,0,1,2,4)
    # 'dot' the cartesian dimension
    contract_AminusB = torch.einsum('ijklm,ijklm->ijkl', AminusB,AminusB)
    c1 = torch.exp(contract_AminusB * -aa_times_bb / aa_plus_bb)
    G.mul_(c1)
    G.mul_(c1.permute(2,3,0,1)) # this is c2

   # Obtain gaussian product centers Rp = (aa * A + bb * B) / (aa + bb);  Rq = (cc * C + dd * D) / (cc + dd)
    weighted_centers = torch.einsum('ijkl,ijklm->ijklm', tmp1, tmp2)
    tmpAB = weighted_centers.permute(0,3,1,2,4) + weighted_centers.permute(3,0,1,2,4)
    tmpCD = tmpAB.permute(2,3,0,1,4)
    Rp_minus_Rq = torch.einsum('ijklm,ijkl->ijklm', tmpAB, 1/aa_plus_bb) -\
                  torch.einsum('ijklm,ijkl->ijklm', tmpCD, 1/aa_plus_bb.permute(3,2,0,1))
    boys_arg = torch.einsum('ijklm,ijklm->ijkl', Rp_minus_Rq, Rp_minus_Rq)
    boys_arg.div_(1 / (aa_plus_bb) + 1 / (aa_plus_bb.permute(3,2,0,1)))
    # If small, set to be 1.0, else evaluate F0 boys function
    boys_arg.clamp_(min=1e-12)
    boys_arg.sqrt_()
    G.mul_(0.8862269254527580)
    G.div_(boys_arg)
    G.mul_(torch.erf(boys_arg))
    #F = torch.zeros_like(boys_arg)
    #mask1 = boys_arg <= 1e-8
    #mask2 = boys_arg > 1e-8
    #F[mask1] = 1 - boys_arg[mask1] / 3 
    #F[mask2] = torch.erf(torch.sqrt(boys_arg[mask2])) * math.sqrt(math.pi) / (2 * torch.sqrt(boys_arg[mask2]))
    #G.mul_(F)
    return G

@torch.jit.script
def gp(aa,bb,A,B):
    '''Gaussian product theorem. Returns center and coefficient of product'''
    R = (aa * A + bb * B) / (aa + bb)
    c = torch.exp(torch.dot(A-B,A-B) * (-aa * bb / (aa + bb)))
    return R,c

@torch.jit.script
def normalize(aa):
    '''Normalization constant for s primitive basis functions. Argument is orbital exponent coefficient'''
    N = (2*aa/math.pi)**(3/4)
    return N

@torch.jit.script
def torchboys(nu, arg):
    '''Pytorch can only exactly compute F0 boys function using the error function relation, the rest would have to
    be determined recursively'''
    if arg < torch.tensor(1e-8):
        boys =  1 / (2 * nu + 1) - arg / (2 * nu + 3)
    else:
        boys = torch.erf(torch.sqrt(arg)) * math.sqrt(math.pi) / (2 * torch.sqrt(arg))
    return boys

@torch.jit.script
def boys(nu, arg):
    '''Alternative boys function expansion. Not exact.'''
    boys = 0.5 * torch.exp(-arg) * (1 / (nu + 0.5)) * (1 + (arg / (nu+1.5)) *\
                                                          (1 + (arg / (nu+2.5)) *\
                                                          (1 + (arg / (nu+3.5)) *\
                                                          (1 + (arg / (nu+4.5)) *\
                                                          (1 + (arg / (nu+5.5)) *\
                                                          (1 + (arg / (nu+6.5)) *\
                                                          (1 + (arg / (nu+7.5)) *\
                                                          (1 + (arg / (nu+8.5)) *\
                                                          (1 + (arg / (nu+9.5)) *\
                                                          (1 + (arg / (nu+10.5))*\
                                                          (1 + (arg / (nu+11.5)))))))))))))
    return boys

# Naive (non-vectorized) implementation
def overlap(aa, bb, A, B):
    '''Computes a single overlap integral over two primitive s-orbital basis functions'''
    Na = normalize(aa)
    Nb = normalize(bb)
    R,c = gp(aa,bb,A,B)
    S = Na * Nb * c * (math.pi / (aa + bb)) ** (3/2)
    return S

def kinetic(aa,bb,A,B):
    '''Computes a single kinetic energy integral over two primitive s-orbital basis functions'''
    P = (aa * bb) / (aa + bb)
    ab = -1.0 * torch.dot(A-B, A-B)
    K = overlap(aa,bb,A,B) * (3 * P + 2 * P * P * ab)
    return K

def potential(aa,bb,A,B,atom,charge):
    '''Computes a single electron-nuclear potential energy integral over two primitive s-orbital basis functions'''
    g = aa + bb
    eps = 1 / (4 * g)
    P, c = gp(aa,bb,A,B)
    arg = g * torch.dot(P - atom, P - atom)
    Na = normalize(aa)
    Nb = normalize(bb)
    F = torchboys(torch.tensor(0.0), arg)
    V = -charge * F * Na * Nb * c * 2 * math.pi / g
    return V

def eri(aa,bb,cc,dd,A,B,C,D):
    '''Computes a single two electron integral over 4 s-orbital basis functions on 4 centers'''
    g1 = aa + bb
    g2 = cc + dd
    Rp = (aa * A + bb * B) / (aa + bb)
    tmpc1 = torch.dot(A-B, A-B) * ((-aa * bb) / (aa + bb))
    c1 = torch.exp(tmpc1)
    Rq = (cc * C + dd * D) / (cc + dd)
    tmpc2 = torch.dot(C-D, C-D) * ((-cc * dd) / (cc + dd))
    c2 = torch.exp(tmpc2)

    Na, Nb, Nc, Nd = normalize(aa), normalize(bb), normalize(cc), normalize(dd)
    delta = 1 / (4 * g1) + 1 / (4 * g2)
    arg = torch.dot(Rp - Rq, Rp - Rq) / (4 * delta)
    F = torchboys(torch.tensor(0.0), arg)
    G = F * Na * Nb * Nc * Nd * c1 * c2 * 2 * math.pi**2 / (g1 * g2) * torch.sqrt(math.pi / (g1 + g2))
    return G


@torch.jit.script
def nuclear_repulsion(atom1, atom2):
    ''' warning : hard coded for H2'''
    Za = 1.0
    Zb = 1.0
    return Za*Zb / torch.norm(atom1-atom2) 

@torch.jit.script
def orthogonalizer(S):
    '''Compute overlap to the negative 1/2 power'''
    #eigval, eigvec = torch.symeig(A1, eigenvectors=True)
    #d12 = torch.diag(torch.sqrt(eigval))
    #A = torch.chain_matmul(eigvec, d12, torch.t(eigvec))
    # More stable for small eigenvalues:
    eigval, eigvec = torch.symeig(S, eigenvectors=True)
    cutoff = 1.0e-12
    above_cutoff = (abs(eigval) > cutoff * torch.max(abs(eigval)))
    val = torch.rsqrt(eigval[above_cutoff])
    vec = eigvec[:, above_cutoff]
    A = torch.chain_matmul(vec, torch.diag(val), vec.t())
    return A

# Vectorized tei's vs naive tei's
def benchmark_tei(basis,geom):
    full_basis = torch.cat((basis,basis))
    nbf_per_atom = torch.tensor([basis.size()[0],basis.size()[0]])
    G = vectorized_tei(full_basis,geom,nbf_per_atom)
    return G

# Vectorized oei's vs naive oei's
def benchmark_oei(basis,geom):
    full_basis = torch.cat((basis,basis))
    nbf_per_atom = torch.tensor([basis.size()[0],basis.size()[0]])
    charge_per_atom = torch.tensor([1.0,1.0])
    S, T, V = vectorized_oei(full_basis, geom, nbf_per_atom, charge_per_atom)
    H = T + V
    result = torch.einsum('ij,jk,kl,lm,im->', H,H,H,H,H)
    grad = torch.autograd.grad(result, geom)
    return S, T, V
