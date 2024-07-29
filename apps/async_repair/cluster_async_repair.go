package main

import (
	"context"
	"log"
	"time"

	"github.com/weaviate/weaviate-go-client/v4/weaviate/data/replication"
	"github.com/weaviate/weaviate-go-client/v4/weaviate/filters"
	"github.com/weaviate/weaviate-go-client/v4/weaviate/graphql"
	"github.com/weaviate/weaviate/entities/models"
)

func main() {
	ctx, cancel := context.WithTimeout(context.Background(), time.Hour)
	defer cancel()

	log.Printf("Validate all newly added objects are available, with consistency level ALL...")

	for idx, client := range allClients() {
		node := idx + 1
		log.Printf("Reading from node %d", node)
		resp, err := client.Data().ObjectsGetter().
			WithClassName(class.Class).
			WithLimit(200).
			WithConsistencyLevel(replication.ConsistencyLevel.ALL).
			Do(ctx)
		if err != nil {
			log.Fatalf("failed to query all objects from node %d with consistency level ALL %s: %v", node, err)
		}
		if len(resp) == 0 || resp[0] == nil {
			log.Fatalf("objects not found in client %d", node)
		}
		if len(resp) != numObjects+newObjects {
			log.Fatalf("Node %d: expected %d objects, got %d", node, numObjects+newObjects, len(resp))
		}

	}

	log.Printf("Validate all newly added objecst are repaired, with consistency level ALL, when starting by the node which was down...")
	for idx, client := range allClientsReversed() {
		node := 3 - idx
		log.Printf("Querying for the newly added objects  in node %d with consistency level ALL", node)

		where := filters.Where().
			WithPath([]string{"index"}).
			WithOperator(filters.GreaterThanEqual).
			WithValueInt(100)

		query, err := client.GraphQL().Get().
			WithClassName(class.Class).
			WithLimit(20).
			WithConsistencyLevel(replication.ConsistencyLevel.ALL).
			WithWhere(where).
			WithFields(graphql.Field{Name: "name"}).
			Do(ctx)
		if err != nil {
			log.Fatalf("failed to query all new objects added when the node 3 was down from node %d with consistency level ALL %s: %v", node, err)
		}

		if query.Errors != nil {
			for _, e := range query.Errors {
				log.Printf("errors found in query for node %d: %s", node, e.Message)
			}
		}

		if len(query.Data) == 0 {
			log.Fatalf("objects not found in query from node %d", node)
		}
		var data models.JSONObject = query.Data["Get"]
		objects := data.(map[string]interface{})[class.Class].([]interface{})

		if len(objects) < newObjects {
			log.Fatalf("Node %d: expected %d objects, got %d", node, newObjects, len(objects))
		}

	}
}
