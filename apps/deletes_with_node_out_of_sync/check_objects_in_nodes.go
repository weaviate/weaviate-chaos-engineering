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

	log.Printf("Validate all objects are missing from Node 3 but present in Node 1...")

	objects := readObjectsFile("data/data.json")

	for _, obj := range objects {
		resp1, err := node1Client.Data().ObjectsGetter().
			WithClassName(class.Class).WithTenant(obj.Tenant).
			WithID(obj.ID.String()).
			WithConsistencyLevel(replication.ConsistencyLevel.ONE).
			Do(ctx)
		if err != nil {
			log.Fatalf("failed to query id %s: %v", obj.ID.String(), err)
		}
		if len(resp1) == 0 || resp1[0] == nil {
			log.Fatalf("object %s not found", obj.ID.String())
		}
		if obj.ID != resp1[0].ID {
			log.Fatalf("expected object id %s, got %s", obj.ID.String(), resp1[0].ID)
		}
		resp3, err := node3Client.Data().ObjectsGetter().
			WithClassName(class.Class).WithTenant(obj.Tenant).
			WithID(obj.ID.String()).
			WithConsistencyLevel(replication.ConsistencyLevel.ONE).
			Do(ctx)

		if len(resp3) != 0 && obj.ID == resp3[0].ID {
			log.Fatalf("object %s found in Node 3 when it should have not", obj.ID.String())
		}

	}
}
