package alter_schema_operations

import (
	"context"
	"testing"

	"github.com/go-faker/faker/v4"
	"github.com/stretchr/testify/require"
	wvt "github.com/weaviate/weaviate-go-client/v5/weaviate"
	"github.com/weaviate/weaviate-go-client/v5/weaviate/graphql"
	"github.com/weaviate/weaviate/entities/models"
)

const moviesMTClass = "MoviesMT"

var moviesMTVectorizerVectors = []string{"hnsw_plain", "flat_plain", "hnsw_rq8", "flat_rq1"}

// TestCreateMoviesMTCollectionAndSearch creates a multi-tenant MoviesMT collection
// with 4 named vectors (hnsw_plain, flat_plain, hnsw_rq8, flat_rq1),
// creates 3 tenants, imports data, and verifies nearText search for each.
func TestCreateMoviesMTCollectionAndSearch(t *testing.T) {
	ctx := context.Background()

	client, err := wvt.NewClient(wvt.Config{
		Scheme: "http",
		Host:   "localhost:8080",
	})
	require.NoError(t, err)
	require.NotNil(t, client)

	// Clean up any existing collection
	client.Schema().ClassDeleter().WithClassName(moviesMTClass).Do(ctx)

	allProps := []string{"title", "director", "description"}

	schema := &models.Class{
		Class: moviesMTClass,
		Properties: []*models.Property{
			{Name: "title", DataType: []string{"text"}},
			{Name: "director", DataType: []string{"text"}},
			{Name: "description", DataType: []string{"text"}},
		},
		ReplicationConfig: &models.ReplicationConfig{
			Factor:       3,
			AsyncEnabled: true,
		},
		MultiTenancyConfig: &models.MultiTenancyConfig{
			Enabled: true,
		},
		VectorConfig: map[string]models.VectorConfig{
			"hnsw_plain": {
				Vectorizer:      vectorizerConfig(allProps),
				VectorIndexType: "hnsw",
			},
			"flat_plain": {
				Vectorizer:      vectorizerConfig(allProps),
				VectorIndexType: "flat",
			},
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
		},
	}

	err = client.Schema().ClassCreator().WithClass(schema).Do(ctx)
	require.NoError(t, err)

	// Create tenants
	tenants := []string{"tenant1", "tenant2", "tenant3"}
	err = client.Schema().TenantsCreator().
		WithClassName(moviesMTClass).
		WithTenants(
			models.Tenant{Name: tenants[0]},
			models.Tenant{Name: tenants[1]},
			models.Tenant{Name: tenants[2]},
		).
		Do(ctx)
	require.NoError(t, err)

	for _, tenant := range tenants {
		t.Run(tenant, func(t *testing.T) {
			t.Logf("Importing %d objects for tenant %s in batches of %d...", numObjects, tenant, batchSize)
			for i := 0; i < numObjects; i += batchSize {
				batcher := client.Batch().ObjectsBatcher()
				end := i + batchSize
				if end > numObjects {
					end = numObjects
				}
				for j := i; j < end; j++ {
					batcher = batcher.WithObject(&models.Object{
						Class:  moviesMTClass,
						Tenant: tenant,
						Properties: map[string]any{
							"title":       faker.Sentence(),
							"director":    faker.Name(),
							"description": faker.Sentence(),
						},
					})
				}

				res, err := batcher.Do(ctx)
				require.NoError(t, err, "batch import failed at offset %d for tenant %s", i, tenant)
				for _, r := range res {
					if r.Result != nil && r.Result.Errors != nil && r.Result.Errors.Error != nil {
						t.Fatalf("failed to create object: %+v, status: %v",
							r.Result.Errors.Error[0], r.Result.Status)
					}
				}

				if (i+batchSize)%2000 == 0 {
					t.Logf("[%s] Imported %d/%d objects", tenant, i+batchSize, numObjects)
				}
			}

			// Verify object count
			t.Logf("[%s] Verifying object count...", tenant)
			result, err := client.GraphQL().Aggregate().
				WithClassName(moviesMTClass).
				WithTenant(tenant).
				WithFields(graphql.Field{Name: "meta", Fields: []graphql.Field{{Name: "count"}}}).
				Do(ctx)
			require.NoError(t, err)
			require.Empty(t, result.Errors, "aggregate query returned errors: %v", result.Errors)

			aggregate := result.Data["Aggregate"].(map[string]interface{})[moviesMTClass].([]interface{})
			require.NotEmpty(t, aggregate)
			actualCount := aggregate[0].(map[string]interface{})["meta"].(map[string]interface{})["count"].(float64)
			require.Equal(t, numObjects, int(actualCount), "expected %d objects, got %d", numObjects, int(actualCount))
			t.Logf("[%s] Verified object count: %d", tenant, int(actualCount))

			// Verify nearText search works for all named vectors
			for _, vectorName := range moviesMTVectorizerVectors {
				t.Run("nearText_"+vectorName, func(t *testing.T) {
					nearText := client.GraphQL().NearTextArgBuilder().
						WithConcepts([]string{"adventure movie"}).
						WithTargetVectors(vectorName)

					searchResult, err := client.GraphQL().Get().
						WithClassName(moviesMTClass).
						WithTenant(tenant).
						WithFields(graphql.Field{Name: "title"}).
						WithNearText(nearText).
						WithLimit(5).
						Do(ctx)
					require.NoError(t, err)
					require.Empty(t, searchResult.Errors, "nearText search returned errors for %s: %v", vectorName, searchResult.Errors)

					data := searchResult.Data["Get"].(map[string]interface{})[moviesMTClass].([]interface{})
					require.NotEmpty(t, data, "nearText search for %s returned no results", vectorName)
					t.Logf("[%s] nearText search for %s returned %d result(s)", tenant, vectorName, len(data))
				})
			}
		})
	}
}
