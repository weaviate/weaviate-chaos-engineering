package main

import (
	"context"
	"encoding/json"
	"log"
	"os"
	"time"

	"github.com/weaviate/weaviate/entities/models"
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
	}

	{
		log.Println("Importing objects batch with random consistency level...")
		b, err := os.ReadFile("data.json")
		if err != nil {
			log.Fatalf("failed to read file: %v", err)
		}

		var objects []*models.Object
		err = json.Unmarshal(b, &objects)
		if err != nil {
			log.Fatalf("failed to unmarshal objects: %v", err)
		}

		for i := 0; i < len(objects); i += batchSize {
			batcher := randClient().Batch().ObjectsBatcher()
			for j := i; j < batchSize+i && j < len(objects); j++ {
				batcher.WithObjects(objects[j])
			}
			resp, err := batcher.WithConsistencyLevel(randCL()).Do(ctx)
			checkBatchInsertResult(resp, err)
		}
	}
}
