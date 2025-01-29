package main

import "container/heap"

func l2SquaredDistance(a, b []float32) float32 {
	var sum float32

	for i := range a {
		diff := a[i] - b[i]
		sum += diff * diff
	}

	return sum
}

type MaxHeap []distanceIndex

func (h MaxHeap) Len() int           { return len(h) }
func (h MaxHeap) Less(i, j int) bool { return h[i].distance > h[j].distance }
func (h MaxHeap) Swap(i, j int)      { h[i], h[j] = h[j], h[i] }

func (h *MaxHeap) Push(x any) {
	*h = append(*h, x.(distanceIndex))
}

func (h *MaxHeap) Pop() any {
	old := *h
	n := len(old)
	x := old[n-1]
	*h = old[0 : n-1]
	return x
}

type distanceIndex struct {
	distance float32
	index    int
}

func bruteForceSearch(vectors [][]float32, query []float32, k int) []distanceIndex {
	var h MaxHeap
	heap.Init(&h)

	for i, v := range vectors {
		dist := l2SquaredDistance(v, query)
		if h.Len() < k {
			heap.Push(&h, distanceIndex{distance: dist, index: i})
		} else if dist < h[0].distance {
			heap.Pop(&h)
			heap.Push(&h, distanceIndex{distance: dist, index: i})
		}
	}

	result := make([]distanceIndex, k)
	for i := k - 1; i >= 0; i-- {
		result[i] = heap.Pop(&h).(distanceIndex)
	}
	return result
}
