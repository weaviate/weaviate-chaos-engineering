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
		log.Println("Delete any existing data...")
		err := node1Client.Schema().AllDeleter().Do(ctx)
		if err != nil {
			log.Fatalf("failed to delete all: %v", err)
		}
	}

	{
		log.Println("Creating class...")
		err := randClient().Schema().ClassCreator().WithClass(&class).Do(ctx)
		if err != nil {
			log.Fatalf("failed to create class %s: %v", class.Class, err)
		}
		// There existed a bug in relation to snapshot restore (due to single node recovery mechanism) on
		// upgrade where if a class was added, deleted and re-added with might lose data. This is an additional
		// step to trigger that case
		err = randClient().Schema().ClassDeleter().WithClassName(class.Class).Do(ctx)
		if err != nil {
			log.Fatalf("failed to create class %s: %v", class.Class, err)
		}
		err = randClient().Schema().ClassCreator().WithClass(&class).Do(ctx)
		if err != nil {
			log.Fatalf("failed to create class %s: %v", class.Class, err)
		}
	}

	{
		log.Println("Importing objects batch with consistency level ONE...")

		objects := readObjectsFile("data.json")

		for i := 0; i < len(objects); i += batchSize {
			batcher := randClient().Batch().ObjectsBatcher()
			for j := i; j < batchSize+i && j < len(objects); j++ {
				batcher.WithObjects(objects[j])
			}
			resp, err := batcher.WithConsistencyLevel(replication.ConsistencyLevel.ONE).Do(ctx)
			checkBatchInsertResult(resp, err)
		}
	}
}
