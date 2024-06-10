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

	log.Printf("Validate all objects were updated, with consistency level ALL...")

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
			log.Fatalf("expected object id %s, got %s", obj.ID.String(), resp[0].ID)
		}

		respProps := resp[0].Properties.(map[string]interface{})
		objName := obj.Properties.(map[string]interface{})["name"]
		expectedName := fmt.Sprintf("updated!%s", objName)
		if respProps["name"] != expectedName {
			log.Fatalf("expected object %s name prop, to have value %s, got %s",
				resp[0].ID, expectedName, respProps["name"])
		}

		objIndex := obj.Properties.(map[string]interface{})["index"]
		if respProps["index"] != objIndex.(float64)+10_000 {
			log.Fatalf("expected object %s index prop, to have value %s, got %s",
				resp[0].ID, objIndex, respProps["index"])
		}
	}
}
