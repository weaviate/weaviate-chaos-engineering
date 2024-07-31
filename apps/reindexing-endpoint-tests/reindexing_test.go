package main

import (
	"context"
	"fmt"
	"log"
	"math/rand/v2"
	"net/http"
	"sort"
	"testing"
	"time"

	"github.com/stretchr/testify/require"
	wvt "github.com/weaviate/weaviate-go-client/v4/weaviate"
	"github.com/weaviate/weaviate-go-client/v4/weaviate/graphql"
	"github.com/weaviate/weaviate-go-client/v4/weaviate/grpc"
	"github.com/weaviate/weaviate/entities/models"
	"github.com/weaviate/weaviate/entities/vectorindex/hnsw"
)

const (
	dims      = 128
	batchSize = 10_000
)

func TestReindexing_Test1(t *testing.T) {
	ctx := context.Background()
	config := wvt.Config{
		Scheme: "http", Host: "localhost:8080",
		GrpcConfig: &grpc.Config{Host: "localhost:50051", Secured: false},
	}
	client, err := wvt.NewClient(config)
	require.NoError(t, err)
	require.NotNil(t, client)

	// clean DB
	err = client.Schema().AllDeleter().Do(ctx)
	require.NoError(t, err)

	// create schema
	err = client.Schema().ClassCreator().WithClass(getClass()).Do(ctx)
	require.NoError(t, err)

	// create objects
	err = createObjects(ctx, client, 100_000)
	require.NoError(t, err)

	// wait till all objects are indexed and compressed
	err = waitForIndexing(ctx, client, "TestClass", 0)
	require.NoError(t, err)

	// generate a list of random vectors for querying
	queries := make([][]float32, 10)
	for i := range queries {
		queries[i] = randomVector(dims)
	}

	// query
	want, err := query(ctx, client, "TestClass", queries)
	require.NoError(t, err)

	// reindex
	err = reindex(ctx, client, "TestClass")
	require.NoError(t, err)

	// wait till all objects are indexed and compressed
	err = waitForIndexing(ctx, client, "TestClass", 0)
	require.NoError(t, err)

	// query again
	got, err := query(ctx, client, "TestClass", queries)
	require.NoError(t, err)

	// compare results. TODO: this doesn't work, perhaps we just need to measure the recall.
	for i := range want {
		for j := range want[i] {
			fmt.Printf("want: %s/%f, got: %s/%f\n", want[i][j].ID, want[i][j].Dist, got[i][j].ID, got[i][j].Dist)
		}

		require.Equal(t, want[i], got[i])
	}
}

func getClass() *models.Class {
	return &models.Class{
		Class:           "TestClass",
		Vectorizer:      "none",
		VectorIndexType: "hnsw",
		ShardingConfig: map[string]interface{}{
			"desiredCount": 5,
		},
		Properties: []*models.Property{
			{
				Name:     "item_id",
				DataType: []string{"int"},
			},
		},
		VectorIndexConfig: hnsw.UserConfig{
			MaxConnections: 16,
			EFConstruction: 64,
			EF:             32,
			PQ: hnsw.PQConfig{
				Enabled:       true,
				TrainingLimit: 10_000,
				Encoder: hnsw.PQEncoder{
					Type:         hnsw.PQEncoderTypeKMeans,
					Distribution: hnsw.PQEncoderDistributionLogNormal,
				},
				Centroids: 256,
			},
		},
	}
}

func randomVector(dim int) []float32 {
	out := make([]float32, dim)
	for i := range out {
		out[i] = rand.Float32()
	}
	return out
}

func createObjects(ctx context.Context, client *wvt.Client, size int) error {
	total := size

	var batch int
	for size > 0 {
		if size > batchSize {
			batch = batchSize
			size -= batchSize
		} else {
			batch = size
			size = 0
		}

		objs := make([]*models.Object, batch)
		for i := 0; i < batch; i++ {
			objs[i] = &models.Object{
				Class: "TestClass",
				Properties: map[string]interface{}{
					"item_id": i,
				},
				Vector: randomVector(dims),
			}
		}

		_, err := client.Batch().ObjectsBatcher().WithObjects(objs...).Do(ctx)
		if err != nil {
			return err
		}

		log.Printf("Created %d/%d objects\n", total-size, total)
	}

	return nil
}

func waitForIndexing(ctx context.Context, client *wvt.Client, className string, minimum int) error {
	log.Println("Waiting for indexing to finish")

	for {
		var allDone bool = true

		resp, err := client.Schema().ShardsGetter().WithClassName(className).Do(ctx)
		if err != nil {
			return err
		}

	LOOP:

		for _, shard := range resp {
			if int(shard.VectorQueueSize) > minimum {
				allDone = false
				break LOOP
			}
		}

		if allDone {
			break
		}

		select {
		case <-ctx.Done():
			return ctx.Err()
		case <-time.After(1 * time.Second):
		}
	}

	log.Println("Indexing finished")

	return nil
}

func getShardNames(ctx context.Context, client *wvt.Client, className string) ([]string, error) {
	resp, err := client.Schema().ShardsGetter().WithClassName(className).Do(ctx)
	if err != nil {
		return nil, err
	}

	names := make([]string, len(resp))
	for i, shard := range resp {
		names[i] = shard.Name
	}

	return names, nil
}

type result struct {
	ID   string
	Dist float64
}

type results []result

func (a results) Len() int           { return len(a) }
func (a results) Swap(i, j int)      { a[i], a[j] = a[j], a[i] }
func (a results) Less(i, j int) bool { return a[i].Dist < a[j].Dist }

func query(ctx context.Context, client *wvt.Client, className string, queries [][]float32) ([]results, error) {
	var r []results

	log.Printf("Running %d queries and collecting results\n", len(queries))

	fields := []graphql.Field{
		{Name: "_additional { id, distance }"},
	}

	for i, query := range queries {
		nearVector := client.GraphQL().NearVectorArgBuilder().
			WithVector(query)

		resp, err := client.GraphQL().Get().
			WithClassName(className).
			WithFields(fields...).
			WithNearVector(nearVector).WithClassName(className).
			WithLimit(100).
			Do(ctx)
		if err != nil {
			return nil, err
		}
		if resp.Errors != nil {
			return nil, fmt.Errorf("query %d failed: %v", i, resp.Errors)
		}

		list := resp.Data["Get"].(map[string]any)[className].([]any)
		var rr results
		for _, elem := range list {
			id := elem.(map[string]any)["_additional"].(map[string]any)["id"].(string)
			dist := elem.(map[string]any)["_additional"].(map[string]any)["distance"].(float64)
			rr = append(rr, result{ID: id, Dist: dist})
		}

		sort.Sort(rr)

		r = append(r, rr)
	}

	return r, nil
}

func reindex(ctx context.Context, client *wvt.Client, className string) error {
	shardNames, err := getShardNames(ctx, client, className)
	if err != nil {
		return err
	}

	httpClient := http.Client{
		Timeout: 10 * time.Second,
	}

	for _, shardName := range shardNames {
		log.Printf("Reindexing shard %s", shardName)

		resp, err := httpClient.Post(fmt.Sprintf("http://localhost:6060/debug/reindex/collection/%s/shards/%s", className, shardName), "application/json", nil)
		if err != nil {
			return err
		}
		defer resp.Body.Close()

		if resp.StatusCode != http.StatusAccepted {
			return fmt.Errorf("unexpected status code: %d", resp.StatusCode)
		}
	}

	return nil
}
