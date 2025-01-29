package main

import (
	"encoding/json"
	"log"
	"os"

	client "github.com/weaviate/weaviate-go-client/v4/weaviate"
	"github.com/weaviate/weaviate/entities/models"
)

var (
	numObjects = 10000
	newObjects = 10
	batchSize  = 5

	node1Client = client.New(client.Config{Scheme: "http", Host: "localhost:8080"})

	class = models.Class{
		Class: "SingleNodeClass",
		Properties: []*models.Property{
			{
				Name:     "name",
				DataType: []string{"string"},
			},
			{
				Name:     "index",
				DataType: []string{"int"},
			},
		},
		ReplicationConfig: &models.ReplicationConfig{
			Factor: 1,
		},
	}
)

func readObjectsFile(filename string) []*models.Object {
	b, err := os.ReadFile(filename)
	if err != nil {
		log.Fatalf("failed to read objects file: %v", err)
	}

	var objects []*models.Object
	err = json.Unmarshal(b, &objects)
	if err != nil {
		log.Fatalf("failed to unmarshal objects file: %v", err)
	}

	return objects
}

func randClient() *client.Client {
	return node1Client
}

func allClients() []*client.Client {
	return []*client.Client{
		node1Client,
	}
}

func allClientsReversed() []*client.Client {
	return []*client.Client{
		node1Client,
	}
}

func checkBatchInsertResult(created []models.ObjectsGetResponse, err error) {
	if err != nil {
		log.Fatalf("batch insert failed: %v", err)
	}

	for _, c := range created {
		if c.Result != nil {
			if c.Result.Errors != nil && c.Result.Errors.Error != nil {
				log.Fatalf("failed to create obj: %+v, with status: %v",
					c.Result.Errors.Error[0], c.Result.Status)
			}
		}
	}
}
