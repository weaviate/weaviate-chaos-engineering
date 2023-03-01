package main

import (
	"context"
	"encoding/json"
	"fmt"
	"log"
	"os"
	"time"

	"github.com/weaviate/weaviate-go-client/v4/weaviate/data/replication"
	"github.com/weaviate/weaviate/entities/models"
)

func main() {
	ctx, cancel := context.WithTimeout(context.Background(), time.Hour)
	defer cancel()

	log.Printf("Validate all objects were patched, with consistency level ALL...")

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
			WithConsistencyLevel(replication.ConsistencyLevel.ALL).
			Do(ctx)
		if err != nil {
			log.Fatalf("failed to query id %s: %v", obj.ID.String(), err)
		}
		if len(resp) == 0 || resp[0] == nil {
			log.Fatalf("object %s not found", obj.ID.String())
		}
		if obj.ID != resp[0].ID {
			log.Fatalf("expected object id %s, got %s", obj.ID.String(), resp[0].ID)
		}

		respProps := resp[0].Properties.(map[string]interface{})
		objName := obj.Properties.(map[string]interface{})["name"]
		expectedName := fmt.Sprintf("patched!%s", objName)
		if respProps["name"] != expectedName {
			log.Fatalf("expected object %s name prop, to have value %s, got %s",
				resp[0].ID, expectedName, respProps["name"])
		}

		objIndex := obj.Properties.(map[string]interface{})["index"]
		if respProps["index"] != objIndex {
			log.Fatalf("expected object %s index prop, to have value %s, got %s",
				resp[0].ID, objIndex, respProps["index"])
		}
	}
}
