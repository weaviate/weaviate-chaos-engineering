package main

import (
	"context"
	"fmt"
	"log"
	"math/rand/v2"
	"net/http"
	"testing"
	"time"

	"github.com/stretchr/testify/require"
	wvt "github.com/weaviate/weaviate-go-client/v4/weaviate"
	"github.com/weaviate/weaviate-go-client/v4/weaviate/graphql"
	"github.com/weaviate/weaviate-go-client/v4/weaviate/grpc"
	"github.com/weaviate/weaviate/entities/models"
	"github.com/weaviate/weaviate/entities/vectorindex/dynamic"
	"github.com/weaviate/weaviate/entities/vectorindex/flat"
	"github.com/weaviate/weaviate/entities/vectorindex/hnsw"
)

const (
	dims      = 128
	batchSize = 10_000
	k         = 100
	className = "TestClass"
	nbQueries = 5
)

type testConfig struct {
	name     string
	pq       bool
	index    string
	mustFail bool
}

func TestDebugReindexing(t *testing.T) {
	tests := []testConfig{
		{name: "flat", index: "flat", mustFail: true},
		{name: "hnsw:no-compression", index: "hnsw"},
		{name: "hnsw:pq", index: "hnsw", pq: true},
		// {name: "dynamic", index: "dynamic"},
		// {name: "dynamic:pq", index: "dynamic", pq: true},
	}

	for _, test := range tests {
		t.Run(test.name, func(t *testing.T) {
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
			err = client.Schema().ClassCreator().WithClass(getClass(test)).Do(ctx)
			require.NoError(t, err)

			// create objects
			vectors, err := createObjects(ctx, client, 100_000)
			require.NoError(t, err)

			// wait till all objects are indexed and compressed
			err = waitForIndexing(ctx, client, className)
			require.NoError(t, err)

			// generate a list of random vectors for querying
			queries := make([][]float32, nbQueries)
			for i := range queries {
				queries[i] = randomVector(dims)
			}

			// run a brute force query for each query vector
			// and store the ground truth
			log.Println("Running brute force search locally")
			gt := make([][]distanceIndex, len(queries))
			for i := range queries {
				gt[i] = bruteForceSearch(vectors, queries[i], k)
			}

			// query
			before, err := query(ctx, client, className, queries)
			require.NoError(t, err)

			// reindex
			err = reindex(ctx, client, className)
			if test.mustFail {
				require.Error(t, err)
				return
			}
			require.NoError(t, err)

			log.Println("Waiting 5s to let the node switch to INDEXING state")
			time.Sleep(5 * time.Second)

			// wait till all objects are indexed and compressed
			err = waitForIndexing(ctx, client, className)
			require.NoError(t, err)

			// query again
			after, err := query(ctx, client, className, queries)
			require.NoError(t, err)

			fmt.Printf("\n ---- Recall results\n")
			for i := range queries {
				recallBefore := calculateRecall(gt[i], before[i])
				recallAfter := calculateRecall(gt[i], after[i])
				fmt.Println("Query", i)
				fmt.Println("Before:", recallBefore)
				fmt.Println("After :", recallAfter)
				fmt.Println("---")

				if recallAfter < recallBefore {
					require.InDelta(t, recallBefore, recallAfter, 0.1)
				}
			}
		})
	}
}

func getClass(cfg testConfig) *models.Class {
	class := models.Class{
		Class:           className,
		Vectorizer:      "none",
		VectorIndexType: "hnsw",
		ShardingConfig: map[string]any{
			"desiredCount": 5,
		},
		Properties: []*models.Property{
			{
				Name:     "index",
				DataType: []string{"int"},
			},
		},
	}

	switch cfg.index {
	case "hnsw":
		var hnswConfig hnsw.UserConfig
		hnswConfig.SetDefaults()
		hnswConfig.Distance = "l2-squared"
		if cfg.pq {
			hnswConfig.PQ.Enabled = true
			hnswConfig.PQ.TrainingLimit = 10_000
		}
		class.VectorIndexConfig = hnswConfig
	case "dynamic":
		var dynamicConfig dynamic.UserConfig
		dynamicConfig.SetDefaults()
		dynamicConfig.Distance = "l2-squared"
		dynamicConfig.Threshold = 20_000
		if cfg.pq {
			dynamicConfig.HnswUC.PQ.Enabled = true
			dynamicConfig.HnswUC.PQ.TrainingLimit = 10_000
		}
		class.VectorIndexConfig = dynamicConfig
	case "flat":
		class.VectorIndexType = "flat"
		class.VectorIndexConfig = flat.UserConfig{}
	default:
		panic(fmt.Sprintf("unknown index type: %s", cfg.index))
	}

	return &class
}

func randomVector(dim int) []float32 {
	out := make([]float32, dim)
	for i := range out {
		out[i] = rand.Float32()
	}
	return out
}

func createObjects(ctx context.Context, client *wvt.Client, size int) ([][]float32, error) {
	total := size
	vectors := make([][]float32, size)
	for i := range vectors {
		vectors[i] = randomVector(dims)
	}

	var batch int
	var count int
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
				Class: className,
				Properties: map[string]any{
					"index": count,
				},
				Vector: vectors[count],
			}
			count++
		}

		_, err := client.Batch().ObjectsBatcher().WithObjects(objs...).Do(ctx)
		if err != nil {
			return nil, err
		}

		log.Printf("Created %d/%d objects\n", total-size, total)
	}

	return vectors, nil
}

func waitForIndexing(ctx context.Context, client *wvt.Client, className string) error {
	log.Println("Waiting for indexing to finish")

	for {
		var allDone bool = true

		resp, err := client.Schema().ShardsGetter().WithClassName(className).Do(ctx)
		if err != nil {
			return err
		}

	LOOP:
		for _, shard := range resp {
			if shard.Status != "READY" {
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

func query(ctx context.Context, client *wvt.Client, className string, queries [][]float32) ([][]distanceIndex, error) {
	var r [][]distanceIndex

	log.Printf("Running %d queries and collecting results\n", len(queries))

	fields := []graphql.Field{
		{Name: "_additional { id, distance }, index"},
	}

	for i, query := range queries {
		nearVector := client.GraphQL().NearVectorArgBuilder().
			WithVector(query)

		resp, err := client.GraphQL().Get().
			WithClassName(className).
			WithFields(fields...).
			WithNearVector(nearVector).WithClassName(className).
			WithLimit(k).
			Do(ctx)
		if err != nil {
			return nil, err
		}
		if resp.Errors != nil {
			for j, e := range resp.Errors {
				return nil, fmt.Errorf("query %d failed: [%d] %v", i, j, e.Message)
			}
		}

		list := resp.Data["Get"].(map[string]any)[className].([]any)
		var rr []distanceIndex
		for _, elem := range list {
			index := elem.(map[string]any)["index"].(float64)
			distance := elem.(map[string]any)["_additional"].(map[string]any)["distance"].(float64)
			rr = append(rr, distanceIndex{
				index:    int(index),
				distance: float32(distance),
			})
		}

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
		Timeout: 120 * time.Second,
	}

	ports := []int{6060, 6061, 6062}

	for _, shardName := range shardNames {
		log.Printf("Reindexing shard %s", shardName)

		var resp *http.Response
		for _, port := range ports {
			resp, err = httpClient.Post(fmt.Sprintf("http://localhost:%d/debug/index/rebuild/vector?collection=%s&shard=%s",
				port, className, shardName), "application/json", nil)
			if err != nil {
				log.Printf("Shard %s not found on port %d. err: %v. Trying a different port...", shardName, err, port)
				continue
			}
			if resp.StatusCode != http.StatusNotFound {
				break
			}

			log.Printf("Shard %s not found on port %d. Trying a different port...", shardName, port)
		}
		if resp == nil {
			return fmt.Errorf("failed to reindex shard %s on all ports", shardName)
		}
		resp.Body.Close()

		if resp.StatusCode != http.StatusAccepted {
			return fmt.Errorf("unexpected status code: %d", resp.StatusCode)
		}
	}

	return nil
}

func calculateRecall(groundTruth, annResults []distanceIndex) float64 {
	correct := 0
	groundTruthSet := make(map[int]bool)
	for _, v := range groundTruth {
		groundTruthSet[v.index] = true
	}
	for _, v := range annResults {
		if groundTruthSet[v.index] {
			correct++
		}
	}
	return float64(correct) / float64(len(groundTruth))
}
