package main

import (
	"context"
	"fmt"
	"log"
	"time"

	"github.com/go-openapi/strfmt"
	"github.com/google/uuid"
	"github.com/weaviate/weaviate-go-client/v4/weaviate/data/replication"
	"github.com/weaviate/weaviate/entities/models"
)

func main() {
	ctx, cancel := context.WithTimeout(context.Background(), time.Hour)
	defer cancel()

	log.Println("Adding objects with consistency level ONE...")

	object := &models.Object{}

	batcher := node1Client.Batch().ObjectsBatcher()
	for i := 0; i < newObjects; i++ {
		randID, _ := uuid.NewRandom()
		object = &models.Object{
			ID:    strfmt.UUID(randID.String()),
			Class: class.Class,
			Properties: map[string]interface{}{
				"name":  fmt.Sprintf("add#%d", i),
				"index": numObjects + i,
			},
		}
		batcher.WithObjects(object)
	}
	resp, err := batcher.WithConsistencyLevel(replication.ConsistencyLevel.ONE).Do(ctx)
	checkBatchInsertResult(resp, err)
}
