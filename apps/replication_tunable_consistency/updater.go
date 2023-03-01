package main

import (
	"context"
	"fmt"
	"log"
	"time"

	"github.com/weaviate/weaviate-go-client/v4/weaviate/data/replication"
)

func main() {
	ctx, cancel := context.WithTimeout(context.Background(), time.Hour)
	defer cancel()

	log.Println("Updating objects with consistency level ONE...")

	objects := readObjectsFile("data.json")

	for i, obj := range objects {
		err := node1Client.Data().Updater().
			WithClassName(class.Class).
			WithID(obj.ID.String()).
			WithProperties(map[string]interface{}{
				"name":  fmt.Sprintf("updated!obj#%d", i),
				"index": i + 10_000,
			}).
			WithConsistencyLevel(replication.ConsistencyLevel.ONE).
			Do(ctx)
		if err != nil {
			log.Fatalf("failed to update object %s", obj.ID)
		}
	}
}
