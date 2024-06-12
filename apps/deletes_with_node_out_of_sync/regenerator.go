package main

import (
	"encoding/json"
	"fmt"
	"log"
	"os"

	"github.com/go-openapi/strfmt"
	"github.com/google/uuid"
	"github.com/weaviate/weaviate/entities/models"
)

func main() {
	log.Println("Generating objects file...")
	objects := make([]*models.Object, numObjects)
	for i := 0; i < numObjects; i++ {
		randID, _ := uuid.NewRandom()
		objects[i] = &models.Object{
			ID:     strfmt.UUID(randID.String()),
			Class:  class.Class,
			Tenant: "tenant2",
			Properties: map[string]interface{}{
				"name":  fmt.Sprintf("obj#%d", i+numObjects),
				"index": i + numObjects,
			},
		}
	}
	b, err := json.Marshal(objects)
	if err != nil {
		log.Fatalf("failed to marshal objects: %v", err)
	}

	err = os.WriteFile("data.json", b, os.ModePerm)
	if err != nil {
		log.Fatalf("failed to write objects file: %v", err)
	}
}
