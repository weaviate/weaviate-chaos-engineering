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

	log.Println("Patching objects with consistency level QUORUM...")

	objects := readObjectsFile("data.json")

	for i, obj := range objects {
		err := node1Client.Data().Updater().
			WithMerge().
			WithClassName(class.Class).
			WithID(obj.ID.String()).
			WithProperties(map[string]interface{}{
				"name": fmt.Sprintf("patched!obj#%d", i),
			}).
			WithConsistencyLevel(replication.ConsistencyLevel.QUORUM).
			Do(ctx)
		if err != nil {
			log.Fatalf("failed to patch object %s", obj.ID)
		}
	}
}
