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

	log.Printf("Validate all tenant2 objects are missing from all nodes...")

	objects := readObjectsFile("data/data.json")

	for idx, client := range allClients() {
		for _, obj := range objects {
			resp, _ := client.Data().ObjectsGetter().
				WithClassName(class.Class).WithTenant(obj.Tenant).
				WithID(obj.ID.String()).
				WithConsistencyLevel(replication.ConsistencyLevel.ONE).
				Do(ctx)
			if len(resp) != 0 && obj.ID == resp[0].ID {
				log.Fatalf("object %s found in client Node %d when it should have not", obj.ID.String(), idx)
			}

		}
	}
}
