package main

import (
	"log"
	"math/rand"

	client "github.com/weaviate/weaviate-go-client/v4/weaviate"
	"github.com/weaviate/weaviate/entities/models"
)

var (
	numObjects = 100_000

	batchSize = 500

	node1Client = client.New(client.Config{Scheme: "http", Host: "localhost:8080"})
	node2Client = client.New(client.Config{Scheme: "http", Host: "localhost:8081"})
	node3Client = client.New(client.Config{Scheme: "http", Host: "localhost:8082"})

	class = models.Class{
		Class: "TunableConsistency",
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
			Factor: 3,
		},
	}
)

func randClient() *client.Client {
	clients := []*client.Client{
		node1Client,
		node2Client,
		node3Client,
	}

	return clients[rand.Intn(len(clients))]
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
