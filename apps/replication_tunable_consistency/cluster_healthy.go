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

	log.Printf("Validate all objects were added, with consistency level ALL...")

	objects := readObjectsFile("data/data.json")

	for _, obj := range objects {
		resp, err := randClient().Data().ObjectsGetter().
			WithClassName(class.Class).
			WithID(obj.ID.String()).
			WithConsistencyLevel(replication.ConsistencyLevel.ALL).
			Do(ctx)
		if err != nil {
			log.Fatalf("failed to query id %s: %v", obj.ID.String(), err)
		}
		if len(resp) == 0 || resp[0] == nil {
			log.Fatalf("object %s not found", obj.ID.String())
		}
		if obj.ID != resp[0].ID {
			log.Fatalf("expected object %s, got %s", obj.ID.String(), resp[0].ID)
		}
	}
}
