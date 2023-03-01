package main

import (
	"context"
	"encoding/json"
	"log"
	"os"
	"time"

	"github.com/weaviate/weaviate-go-client/v4/weaviate/data/replication"
	"github.com/weaviate/weaviate/entities/models"
)

func main() {
	ctx, cancel := context.WithTimeout(context.Background(), time.Hour)
	defer cancel()

	log.Printf("Validate all objects were added, with consistency level ONE...")

	b, err := os.ReadFile("data/data.json")
	if err != nil {
		log.Fatalf("failed to read objects file: %v", err)
	}

	var objects []*models.Object
	err = json.Unmarshal(b, &objects)
	if err != nil {
		log.Fatalf("failed to unmarshal objects file: %v", err)
	}

	for _, obj := range objects {
		resp, err := randClient().Data().ObjectsGetter().
			WithClassName(class.Class).
			WithID(obj.ID.String()).
			WithConsistencyLevel(replication.ConsistencyLevel.ONE).
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
