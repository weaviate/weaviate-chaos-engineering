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

	log.Println("Deleting tenant2 objects with consistency level ONE...")

	objects := readObjectsFile("data.json")

	for _, obj := range objects {
		err := node3Client.Data().Deleter().
			WithClassName(class.Class).WithTenant(obj.Tenant).
			WithID(obj.ID.String()).
			WithConsistencyLevel(replication.ConsistencyLevel.ONE).
			Do(ctx)
		if err != nil {
			log.Fatalf("failed to delete object %s: %s", obj.ID, err.Error())
		}
	}
}
