package main

import (
	"bytes"
	"context"
	"fmt"
	"io"
	"log"
	"math/rand"
	"net/http"
	"net/url"
	"os"
	"strconv"
	"time"

	"github.com/pkg/errors"
	"github.com/weaviate/weaviate-go-client/v5/weaviate"
	"github.com/weaviate/weaviate/entities/models"

	_ "net/http/pprof"
)

func main() {
	go func() {
		log.Println(http.ListenAndServe("localhost:5050", nil))
	}()

	if err := do(context.Background()); err != nil {
		log.Fatal(err)
	}
}

func do(ctx context.Context) error {
	dims, err := getIntVar("DIMENSIONS")
	if err != nil {
		return err
	}

	shards, err := getIntVar("SHARDS")
	if err != nil {
		return err
	}

	size, err := getIntVar("SIZE")
	if err != nil {
		return err
	}

	batchSize, err := getIntVar("BATCH_SIZE")
	if err != nil {
		return err
	}

	origin, err := getStringVar("ORIGIN")
	if err != nil {
		return err
	}

	rqEnabled := os.Getenv("RQ_ENABLED") == "true"

	client, err := newClient(origin)
	if err != nil {
		return err
	}

	if err := client.Schema().AllDeleter().Do(ctx); err != nil {
		return err
	}

	if err := client.Schema().ClassCreator().WithClass(getClass(shards)).Do(ctx); err != nil {
		return err
	}

	httpClient := &http.Client{}

	rqAlreadyEnabled := false
	count := 0
	beforeAll := time.Now()
	for count < size {
		// Enable RQ at 50% progress
		if rqEnabled && !rqAlreadyEnabled && count >= size/2 {
			fmt.Println("Reached 50% of import, enabling RQ...")
			var rqErr error
			for attempt := 0; attempt < 20; attempt++ {
				if rqErr = enableRQ(ctx, client); rqErr == nil {
					break
				}
				fmt.Printf("Failed to enable RQ (attempt %d): %v. Retrying in 1s...\n", attempt, rqErr)
				time.Sleep(1 * time.Second)
			}
			if rqErr != nil {
				return fmt.Errorf("failed to enable RQ after 20 attempts: %w", rqErr)
			}
			fmt.Println("RQ enabled successfully")
			rqAlreadyEnabled = true
		}

		batcher := newBatch()
		for i := 0; i < batchSize; i++ {
			batcher.addObject(fmt.Sprintf(`{"itemId":%d}`, count+1), randomVector(dims))
		}

		before := time.Now()
		if err := batcher.send(httpClient, origin); err != nil {
			return err
		}
		fmt.Printf("%f%% complete - last batch took %s - total %s\n",
			float32(count)/float32(size)*100,
			time.Since(before), time.Since(beforeAll))
		batcher = newBatch()
		count += batchSize
	}

	return nil
}

func enableRQ(ctx context.Context, client *weaviate.Client) error {
	cls, err := client.Schema().ClassGetter().WithClassName("DemoClass").Do(ctx)
	if err != nil {
		return fmt.Errorf("get class: %w", err)
	}

	vic, ok := cls.VectorIndexConfig.(map[string]interface{})
	if !ok {
		return fmt.Errorf("unexpected vectorIndexConfig type: %T", cls.VectorIndexConfig)
	}

	vic["rq"] = map[string]interface{}{
		"enabled": true,
	}
	cls.VectorIndexConfig = vic

	if err := client.Schema().ClassUpdater().WithClass(cls).Do(ctx); err != nil {
		return fmt.Errorf("update class: %w", err)
	}

	return nil
}

type batch struct {
	bytes.Buffer
	hasElements bool
}

func newBatch() *batch {
	b := &batch{}

	b.WriteString(`{"objects":[`)
	return b
}

func (b *batch) addObject(propsString string, vec []float32) {
	if b.hasElements {
		b.WriteString(",")
	}
	b.WriteString(fmt.Sprintf(`{"class":"DemoClass","properties":%s, "vector":[`, propsString))
	for i, dim := range vec {
		if i != 0 {
			b.WriteString(",")
		}
		b.WriteString(fmt.Sprintf("%f", dim))
	}
	b.WriteString("]}")
	b.hasElements = true
}

func (b *batch) send(client *http.Client, origin string) error {
	b.WriteString("]}")

	body := b.Bytes()
	r := bytes.NewReader(body)

	req, err := http.NewRequest("POST", origin+"/v1/batch/objects", r)
	if err != nil {
		return err
	}

	req.Header.Add("content-type", "application/json")

	const maxRetries = 100
	const retryDelay = 1 * time.Second

	var res *http.Response

	for attempt := 0; attempt <= maxRetries; attempt++ {
		res, err = client.Do(req)

		if err == nil && res != nil && res.StatusCode == 200 {
			io.ReadAll(res.Body)
			res.Body.Close()
			return nil
		}

		if attempt < maxRetries {
			fmt.Printf("Attempt %d failed (error: %v). Retrying in 1s...\n", attempt, err)
			time.Sleep(retryDelay)

			r.Seek(0, 0)
		} else {
			fmt.Printf("Aborting after %d retries\n", maxRetries)
		}
	}

	if err != nil {
		return fmt.Errorf("request failed after %d retries: %v", maxRetries, err)
	}

	msg, _ := io.ReadAll(res.Body)
	res.Body.Close()
	return errors.Errorf("status %d: %s", res.StatusCode, string(msg))

}

func randomVector(dim int) []float32 {
	out := make([]float32, dim)
	for i := range out {
		out[i] = rand.Float32()
	}
	return out
}

func getIntVar(envName string) (int, error) {
	v := os.Getenv(envName)
	if v == "" {
		return 0, errors.Errorf("missing required variable %s", envName)
	}

	asInt, err := strconv.Atoi(v)
	if err != nil {
		return 0, err
	}

	return asInt, nil
}

func getStringVar(envName string) (string, error) {
	v := os.Getenv(envName)
	if v == "" {
		return v, errors.Errorf("missing required variable %s", envName)
	}

	return v, nil
}

func newClient(origin string) (*weaviate.Client, error) {
	parsed, err := url.Parse(origin)
	if err != nil {
		return nil, err
	}

	cfg := weaviate.Config{
		Host:   parsed.Host,
		Scheme: parsed.Scheme,
	}
	return weaviate.New(cfg), nil
}

func getClass(shards int) *models.Class {
	return &models.Class{
		Class:           "DemoClass",
		Vectorizer:      "none",
		VectorIndexType: "hnsw",
		ShardingConfig: map[string]interface{}{
			"desiredCount": shards,
		},
		VectorIndexConfig: map[string]interface{}{
			"vectorCacheMaxObjects":  10000000000,
			"cleanupIntervalSeconds": 30,
		},
		Properties: []*models.Property{
			{
				Name:     "itemId",
				DataType: []string{"int"},
			},
		},
	}
}
