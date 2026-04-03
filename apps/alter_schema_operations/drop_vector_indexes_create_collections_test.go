package alter_schema_operations

import (
	"context"
	"math/rand"
	"testing"

	"github.com/go-faker/faker/v4"
	"github.com/stretchr/testify/require"
	wvt "github.com/weaviate/weaviate-go-client/v5/weaviate"
	"github.com/weaviate/weaviate-go-client/v5/weaviate/graphql"
	"github.com/weaviate/weaviate/entities/models"
)

const (
	moviesClass    = "Movies"
	multiVecDim    = 64
	multiVecTokens = 8
)

// Named vectors using text2vec-model2vec (vectorized automatically)
var vectorizerVectors = []string{
	"hnsw_plain",
	"flat_plain",
	"hnsw_pq",
	"hnsw_bq",
	"hnsw_sq",
	"hnsw_rq1",
	"hnsw_rq8",
	"flat_bq",
	"flat_rq1",
}

// Named vectors using "none" vectorizer (multi-vector, provided explicitly)
var multiVectors = []string{
	"hnsw_multivec",
	"hnsw_multivec_muvera",
	"hnsw_multivec_muvera_rq1",
	"hnsw_multivec_muvera_rq8",
}

func vectorizerConfig(properties []string) map[string]any {
	return map[string]any{
		"text2vec-model2vec": map[string]any{
			"properties": toAnySlice(properties),
		},
	}
}

func toAnySlice(ss []string) []any {
	out := make([]any, len(ss))
	for i, s := range ss {
		out[i] = s
	}
	return out
}

func randomMultiVector(tokens, dim int) [][]float32 {
	mv := make([][]float32, tokens)
	for i := range mv {
		v := make([]float32, dim)
		for j := range v {
			v[j] = rand.Float32()
		}
		mv[i] = v
	}
	return mv
}

func TestCreateMoviesCollectionAndSearch(t *testing.T) {
	ctx := context.Background()

	client, err := wvt.NewClient(wvt.Config{
		Scheme: "http",
		Host:   "localhost:8080",
	})
	require.NoError(t, err)
	require.NotNil(t, client)

	// Clean up any existing schema
	err = client.Schema().AllDeleter().Do(ctx)
	require.NoError(t, err)

	allProps := []string{"title", "director", "description"}

	// Create "Movies" collection with various vector index configurations
	moviesSchema := &models.Class{
		Class: moviesClass,
		Properties: []*models.Property{
			{Name: "title", DataType: []string{"text"}},
			{Name: "director", DataType: []string{"text"}},
			{Name: "description", DataType: []string{"text"}},
		},
		ReplicationConfig: &models.ReplicationConfig{
			Factor:       3,
			AsyncEnabled: true,
		},
		VectorConfig: map[string]models.VectorConfig{
			// a) Pure HNSW
			"hnsw_plain": {
				Vectorizer:      vectorizerConfig(allProps),
				VectorIndexType: "hnsw",
			},
			// a) Pure flat
			"flat_plain": {
				Vectorizer:      vectorizerConfig(allProps),
				VectorIndexType: "flat",
			},
			// b) HNSW with PQ
			"hnsw_pq": {
				Vectorizer:      vectorizerConfig(allProps),
				VectorIndexType: "hnsw",
				VectorIndexConfig: map[string]any{
					"pq": map[string]any{
						"enabled":       true,
						"trainingLimit": float64(1000),
					},
				},
			},
			// b) HNSW with BQ
			"hnsw_bq": {
				Vectorizer:      vectorizerConfig(allProps),
				VectorIndexType: "hnsw",
				VectorIndexConfig: map[string]any{
					"bq": map[string]any{
						"enabled": true,
					},
				},
			},
			// b) HNSW with SQ
			"hnsw_sq": {
				Vectorizer:      vectorizerConfig(allProps),
				VectorIndexType: "hnsw",
				VectorIndexConfig: map[string]any{
					"sq": map[string]any{
						"enabled":       true,
						"trainingLimit": float64(1000),
					},
				},
			},
			// b) HNSW with RQ (bits=1)
			"hnsw_rq1": {
				Vectorizer:      vectorizerConfig(allProps),
				VectorIndexType: "hnsw",
				VectorIndexConfig: map[string]any{
					"rq": map[string]any{
						"enabled": true,
						"bits":    float64(1),
					},
				},
			},
			// b) HNSW with RQ (bits=8)
			"hnsw_rq8": {
				Vectorizer:      vectorizerConfig(allProps),
				VectorIndexType: "hnsw",
				VectorIndexConfig: map[string]any{
					"rq": map[string]any{
						"enabled": true,
						"bits":    float64(8),
					},
				},
			},
			// c) Flat with BQ
			"flat_bq": {
				Vectorizer:      vectorizerConfig(allProps),
				VectorIndexType: "flat",
				VectorIndexConfig: map[string]any{
					"bq": map[string]any{
						"enabled": true,
					},
				},
			},
			// c) Flat with RQ (bits=1)
			"flat_rq1": {
				Vectorizer:      vectorizerConfig(allProps),
				VectorIndexType: "flat",
				VectorIndexConfig: map[string]any{
					"rq": map[string]any{
						"enabled": true,
						"bits":    float64(1),
					},
				},
			},
			// Multi-vector: pure HNSW
			"hnsw_multivec": {
				Vectorizer:      map[string]any{"none": nil},
				VectorIndexType: "hnsw",
				VectorIndexConfig: map[string]any{
					"multivector": map[string]any{
						"enabled": true,
					},
				},
			},
			// Multi-vector: HNSW with muvera
			"hnsw_multivec_muvera": {
				Vectorizer:      map[string]any{"none": nil},
				VectorIndexType: "hnsw",
				VectorIndexConfig: map[string]any{
					"multivector": map[string]any{
						"enabled": true,
						"muvera": map[string]any{
							"enabled": true,
						},
					},
				},
			},
			// Multi-vector: HNSW with muvera + RQ (bits=1)
			"hnsw_multivec_muvera_rq1": {
				Vectorizer:      map[string]any{"none": nil},
				VectorIndexType: "hnsw",
				VectorIndexConfig: map[string]any{
					"multivector": map[string]any{
						"enabled": true,
						"muvera": map[string]any{
							"enabled": true,
						},
					},
					"rq": map[string]any{
						"enabled": true,
						"bits":    float64(1),
					},
				},
			},
			// Multi-vector: HNSW with muvera + RQ (bits=8)
			"hnsw_multivec_muvera_rq8": {
				Vectorizer:      map[string]any{"none": nil},
				VectorIndexType: "hnsw",
				VectorIndexConfig: map[string]any{
					"multivector": map[string]any{
						"enabled": true,
						"muvera": map[string]any{
							"enabled": true,
						},
					},
					"rq": map[string]any{
						"enabled": true,
						"bits":    float64(8),
					},
				},
			},
		},
	}

	err = client.Schema().ClassCreator().WithClass(moviesSchema).Do(ctx)
	require.NoError(t, err)

	// Sample multi-vector for search verification (stored from first object)
	sampleMultiVec := randomMultiVector(multiVecTokens, multiVecDim)

	// Generate and batch import objects
	t.Logf("Importing %d objects in batches of %d...", numObjects, batchSize)
	for i := 0; i < numObjects; i += batchSize {
		batcher := client.Batch().ObjectsBatcher()
		end := i + batchSize
		if end > numObjects {
			end = numObjects
		}
		for j := i; j < end; j++ {
			vectors := make(models.Vectors)
			for _, name := range multiVectors {
				vectors[name] = randomMultiVector(multiVecTokens, multiVecDim)
			}
			if j == 0 {
				sampleMultiVec = vectors[multiVectors[0]].([][]float32)
			}

			batcher = batcher.WithObject(&models.Object{
				Class: moviesClass,
				Properties: map[string]any{
					"title":       faker.Sentence(),
					"director":    faker.Name(),
					"description": faker.Paragraph(),
				},
				Vectors: vectors,
			})
		}

		res, err := batcher.Do(ctx)
		require.NoError(t, err, "batch import failed at offset %d", i)
		for _, r := range res {
			if r.Result != nil && r.Result.Errors != nil && r.Result.Errors.Error != nil {
				t.Fatalf("failed to create object: %+v, status: %v",
					r.Result.Errors.Error[0], r.Result.Status)
			}
		}

		if (i+batchSize)%2000 == 0 {
			t.Logf("Imported %d/%d objects", i+batchSize, numObjects)
		}
	}

	// Verify object count via GraphQL aggregate
	t.Log("Verifying object count...")
	result, err := client.GraphQL().Aggregate().
		WithClassName(moviesClass).
		WithFields(graphql.Field{Name: "meta", Fields: []graphql.Field{{Name: "count"}}}).
		Do(ctx)
	require.NoError(t, err)
	require.Empty(t, result.Errors, "aggregate query returned errors: %v", result.Errors)

	aggregate := result.Data["Aggregate"].(map[string]interface{})[moviesClass].([]interface{})
	require.NotEmpty(t, aggregate, "no aggregate found for collection %q", moviesClass)
	actualCount := aggregate[0].(map[string]interface{})["meta"].(map[string]interface{})["count"].(float64)
	require.Equal(t, numObjects, int(actualCount), "expected %d objects, got %d", numObjects, int(actualCount))
	t.Logf("Verified object count: %d", int(actualCount))

	// Verify nearText search works for vectorizer-based named vectors
	for _, vectorName := range vectorizerVectors {
		t.Run("nearText_"+vectorName, func(t *testing.T) {
			t.Logf("Testing nearText search for %s...", vectorName)

			nearText := client.GraphQL().NearTextArgBuilder().
				WithConcepts([]string{"adventure movie"}).
				WithTargetVectors(vectorName)

			searchResult, err := client.GraphQL().Get().
				WithClassName(moviesClass).
				WithFields(graphql.Field{Name: "title"}).
				WithNearText(nearText).
				WithLimit(5).
				Do(ctx)
			require.NoError(t, err)
			require.Empty(t, searchResult.Errors, "nearText search returned errors for %s: %v", vectorName, searchResult.Errors)

			data := searchResult.Data["Get"].(map[string]interface{})[moviesClass].([]interface{})
			require.NotEmpty(t, data, "nearText search for %s returned no results", vectorName)
			t.Logf("nearText search for %s returned %d result(s)", vectorName, len(data))
		})
	}

	// Verify nearVector search works for multi-vector named vectors
	for _, vectorName := range multiVectors {
		t.Run("nearVector_"+vectorName, func(t *testing.T) {
			t.Logf("Testing nearVector search for %s...", vectorName)

			nearVec := client.GraphQL().NearVectorArgBuilder().
				WithVectorPerTarget(map[string]models.Vector{
					vectorName: sampleMultiVec,
				}).
				WithTargetVectors(vectorName)

			searchResult, err := client.GraphQL().Get().
				WithClassName(moviesClass).
				WithFields(graphql.Field{Name: "title"}).
				WithNearVector(nearVec).
				WithLimit(5).
				Do(ctx)
			require.NoError(t, err)
			require.Empty(t, searchResult.Errors, "nearVector search returned errors for %s: %v", vectorName, searchResult.Errors)

			data := searchResult.Data["Get"].(map[string]interface{})[moviesClass].([]interface{})
			require.NotEmpty(t, data, "nearVector search for %s returned no results", vectorName)
			t.Logf("nearVector search for %s returned %d result(s)", vectorName, len(data))
		})
	}
}
