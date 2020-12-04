#include <stdlib.h>
#include <iostream>
#include <vector>
#include <algorithm>

using namespace std;

// Unique combinations with repitition. 
// Given an integral shell set, we routinely find which shell component indices 
// (0-11 for eri, 0-5 for overlap,kinetic, and 0-5 and 0-ncart for potential)
// which have atom indices which match the desired atoms to be differentiated according to deriv_vec
// This function is used to find all unique combinations with repitition of those indices
// and these combinations are effectively the multi-dim index which finds the desired buffer
// index in the buffer index lookup arrays generated by generate_*_lookup.
// Practically, given a vector of indices 'inp', deriv_order 'n', 
// instantiated vector 'out' and vector of vectors 'result',
// call unique_cwr(inp, out, result, k, 0, n);
// to fill 'result'. Then loop over vectors in result
// and index buffer lookup array generated by generate_*_lookup
// It's never easy, is it?
void unique_cwr_recursion(std::vector<int> inp, 
                          std::vector<int> &out, 
                          std::vector<std::vector<int>> &result, 
                          int k, int i, int n)
{
	// base case: if combination size is k, add to result 
	if (out.size() == k)
	{
        result.push_back(out);
		return;
	}

	// start from previous element in the current combination til last element
	for (int j = i; j < n; j++)
	{
		// add current element arr[j] to the solution and recur with
		// same index j (as repeated elements are allowed in combinations)
		out.push_back(inp[j]);
		unique_cwr_recursion(inp, out, result, k, j, n);

		// backtrack - remove current element from solution
		out.pop_back();

		// code to handle duplicates - skip adjacent duplicate elements
		while (j < n - 1 && inp[j] == inp[j + 1])
			j++;
	}
}


int main()
{
    // If you have a vector of integers and want k combinations with replacement, no repeats
    std::vector<int> inp {2, 5, 8, 11};
    //std::vector<std::vector<int>> inp {{5, 8, 11}, {5, 8, 11}};
    //std::vector<std::vector<int>> inp {{5, 8, 11}, {5, 8, 11}, {5, 8, 11}};
    int k = 2;
    //int n = inp.size(); 
    int n = 3; //inp.size(); 
    // Need to initialize starting sizes for each vector in inp

    std::sort (inp.begin(), inp.end());
	// if array contains repeated elements, sort the array to handle duplicates combinations
    //for (int i=0;i<inp.size();i++){
    //    std::sort (inp[i].begin(), inp[i].end());
    //}
    
    vector<int> out;
    std::vector<std::vector<int>> result;
    recur(inp, out, result, k, 0, 0, n);

    //for (int i=0;i<inp.size();i++){
    //    printf(" \n ");
    //    for (int j=0; j<inp[i].size(); j++){
    //        printf("%d ", inp[i][j]);
    //    }
    //}
    // Test: print result
    //for (int i=0;i<result.size();i++){
    //    printf(" \n ");
    //    for (int j=0; j<result[i].size(); j++){
    //        printf("%d ", result[i][j]);
    //    }
    //}
    //printf(" \n ");
	return 0;
}


