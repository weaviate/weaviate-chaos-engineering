package main

import (
	"context"
	"log"
	"time"

	"github.com/weaviate/weaviate-go-client/v4/weaviate/data/replication"
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

		log.Println("Creating tenants...")
		err = randClient().Schema().TenantsCreator().WithClassName(class.Class).WithTenants(models.Tenant{Name: "tenant1"}, models.Tenant{Name: "tenant2"}).Do(ctx)
		if err != nil {
			log.Fatalf("failed to create tenants: %v", err)
		}
	}

	{
		log.Println("Importing teanant1 objects batch with consistency level ALL...")

		objects := readObjectsFile("data.json")

		for i := 0; i < len(objects); i += batchSize {
			batcher := randClient().Batch().ObjectsBatcher()
			for j := i; j < batchSize+i && j < len(objects); j++ {
				batcher.WithObjects(objects[j])
			}
			resp, err := batcher.WithConsistencyLevel(replication.ConsistencyLevel.ALL).Do(ctx)
			checkBatchInsertResult(resp, err)
		}
	}
}
