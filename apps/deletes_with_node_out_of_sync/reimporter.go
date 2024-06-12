package main

import (
	"context"
	"log"
	"time"

	"github.com/weaviate/weaviate-go-client/v4/weaviate/data/replication"
)

func main() {
	ctx, cancel := context.WithTimeout(context.Background(), time.Hour)
	defer cancel()

	{
		log.Println("Importing tenant2 objects batch with consistency level QUORUM...")

		objects := readObjectsFile("data.json")

		for i := 0; i < len(objects); i += batchSize {
			batcher := node1Client.Batch().ObjectsBatcher()
			for j := i; j < batchSize+i && j < len(objects); j++ {
				batcher.WithObjects(objects[j])
			}
			resp, err := batcher.WithConsistencyLevel(replication.ConsistencyLevel.QUORUM).Do(ctx)
			checkBatchInsertResult(resp, err)
		}
	}
}
