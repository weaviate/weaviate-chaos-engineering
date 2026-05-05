package alter_schema_operations

import (
	"context"
	"fmt"
	"testing"

	"github.com/go-faker/faker/v4"
	"github.com/stretchr/testify/require"
	wvt "github.com/weaviate/weaviate-go-client/v5/weaviate"
	"github.com/weaviate/weaviate-go-client/v5/weaviate/filters"
	"github.com/weaviate/weaviate-go-client/v5/weaviate/graphql"
	"github.com/weaviate/weaviate/entities/models"
)

const (
	booksClass   = "Books"
	booksMTClass = "BooksMT"
	numObjects   = 10000
	batchSize    = 100
)

func TestCreateBooksCollectionAndFilter(t *testing.T) {
	ctx := context.Background()

	// Create client
	client, err := wvt.NewClient(wvt.Config{
		Scheme: "http",
		Host:   "localhost:8080",
	})
	require.NoError(t, err)
	require.NotNil(t, client)

	// Clean up any existing schema
	err = client.Schema().AllDeleter().Do(ctx)
	require.NoError(t, err)

	// Create "Books" collection
	vTrue := true
	booksSchema := &models.Class{
		Class: booksClass,
		Properties: []*models.Property{
			{
				Name:            "title",
				DataType:        []string{"text"},
				IndexSearchable: &vTrue,
				IndexFilterable: &vTrue,
			},
			{
				Name:            "author",
				DataType:        []string{"text"},
				IndexSearchable: &vTrue,
				IndexFilterable: &vTrue,
			},
			{
				Name:            "description",
				DataType:        []string{"text"},
				IndexSearchable: &vTrue,
				IndexFilterable: &vTrue,
			},
		},
		ReplicationConfig: &models.ReplicationConfig{
			Factor:       3,
			AsyncEnabled: true,
		},
		VectorConfig: map[string]models.VectorConfig{
			"model2vec": {
				Vectorizer: map[string]any{
					"text2vec-model2vec": map[string]any{
						"properties": []any{"title", "author", "description"},
					},
				},
				VectorIndexType: "hnsw",
			},
		},
	}

	err = client.Schema().ClassCreator().WithClass(booksSchema).Do(ctx)
	require.NoError(t, err)

	// Store a sample object for filter verification
	var sampleTitle, sampleAuthor, sampleDescription string

	// Generate and batch import 10k objects
	t.Logf("Importing %d objects in batches of %d...", numObjects, batchSize)
	for i := 0; i < numObjects; i += batchSize {
		batcher := client.Batch().ObjectsBatcher()
		end := i + batchSize
		if end > numObjects {
			end = numObjects
		}
		for j := i; j < end; j++ {
			title := faker.Sentence()
			author := faker.Name()
			description := faker.Paragraph()

			// Store the first object's values for filter testing
			if j == 0 {
				sampleTitle = title
				sampleAuthor = author
				sampleDescription = description
			}

			batcher = batcher.WithObject(&models.Object{
				Class: booksClass,
				Properties: map[string]interface{}{
					"title":       title,
					"author":      author,
					"description": description,
				},
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
		WithClassName(booksClass).
		WithFields(graphql.Field{Name: "meta", Fields: []graphql.Field{{Name: "count"}}}).
		Do(ctx)
	require.NoError(t, err)
	require.Empty(t, result.Errors, "aggregate query returned errors: %v", result.Errors)

	aggregate := result.Data["Aggregate"].(map[string]interface{})[booksClass].([]interface{})
	require.NotEmpty(t, aggregate, "no aggregate found for collection %q", booksClass)
	actualCount := aggregate[0].(map[string]interface{})["meta"].(map[string]interface{})["count"].(float64)
	require.Equal(t, numObjects, int(actualCount), "expected %d objects, got %d", numObjects, int(actualCount))
	t.Logf("Verified object count: %d", int(actualCount))

	// Test filtering by title (Equal operator)
	t.Log("Testing filter by title...")
	titleWhere := filters.Where().
		WithPath([]string{"title"}).
		WithOperator(filters.Equal).
		WithValueText(sampleTitle)

	titleResult, err := client.GraphQL().Get().
		WithClassName(booksClass).
		WithFields(graphql.Field{Name: "title"}, graphql.Field{Name: "author"}, graphql.Field{Name: "description"}).
		WithWhere(titleWhere).
		Do(ctx)
	require.NoError(t, err)
	require.Empty(t, titleResult.Errors, "title filter query returned errors: %v", titleResult.Errors)

	titleData := titleResult.Data["Get"].(map[string]interface{})[booksClass].([]interface{})
	require.NotEmpty(t, titleData, "filter by title returned no results for %q", sampleTitle)
	t.Logf("Filter by title returned %d result(s)", len(titleData))

	// Test filtering by author (Equal operator)
	t.Log("Testing filter by author...")
	authorWhere := filters.Where().
		WithPath([]string{"author"}).
		WithOperator(filters.Equal).
		WithValueText(sampleAuthor)

	authorResult, err := client.GraphQL().Get().
		WithClassName(booksClass).
		WithFields(graphql.Field{Name: "title"}, graphql.Field{Name: "author"}, graphql.Field{Name: "description"}).
		WithWhere(authorWhere).
		Do(ctx)
	require.NoError(t, err)
	require.Empty(t, authorResult.Errors, "author filter query returned errors: %v", authorResult.Errors)

	authorData := authorResult.Data["Get"].(map[string]interface{})[booksClass].([]interface{})
	require.NotEmpty(t, authorData, "filter by author returned no results for %q", sampleAuthor)
	t.Logf("Filter by author returned %d result(s)", len(authorData))

	// Test filtering by description (Like operator)
	t.Log("Testing filter by description...")
	// Extract first few words for the Like pattern
	descPattern := fmt.Sprintf("%s*", sampleDescription[:min(50, len(sampleDescription))])
	descWhere := filters.Where().
		WithPath([]string{"description"}).
		WithOperator(filters.Like).
		WithValueText(descPattern)

	descResult, err := client.GraphQL().Get().
		WithClassName(booksClass).
		WithFields(graphql.Field{Name: "title"}, graphql.Field{Name: "author"}, graphql.Field{Name: "description"}).
		WithWhere(descWhere).
		Do(ctx)
	require.NoError(t, err)
	require.Empty(t, descResult.Errors, "description filter query returned errors: %v", descResult.Errors)

	descData := descResult.Data["Get"].(map[string]interface{})[booksClass].([]interface{})
	require.NotEmpty(t, descData, "filter by description returned no results for pattern %q", descPattern)
	t.Logf("Filter by description returned %d result(s)", len(descData))
}

func TestCreateBooksMTCollectionAndFilter(t *testing.T) {
	ctx := context.Background()

	client, err := wvt.NewClient(wvt.Config{
		Scheme: "http",
		Host:   "localhost:8080",
	})
	require.NoError(t, err)
	require.NotNil(t, client)

	// Create "BooksMT" multi-tenant collection
	vTrue := true
	booksMTSchema := &models.Class{
		Class: booksMTClass,
		Properties: []*models.Property{
			{
				Name:            "title",
				DataType:        []string{"text"},
				IndexSearchable: &vTrue,
				IndexFilterable: &vTrue,
			},
			{
				Name:            "author",
				DataType:        []string{"text"},
				IndexSearchable: &vTrue,
				IndexFilterable: &vTrue,
			},
			{
				Name:            "description",
				DataType:        []string{"text"},
				IndexSearchable: &vTrue,
				IndexFilterable: &vTrue,
			},
		},
		ReplicationConfig: &models.ReplicationConfig{
			Factor:       3,
			AsyncEnabled: true,
		},
		MultiTenancyConfig: &models.MultiTenancyConfig{
			Enabled: true,
		},
		VectorConfig: map[string]models.VectorConfig{
			"model2vec": {
				Vectorizer: map[string]any{
					"text2vec-model2vec": map[string]any{
						"properties": []any{"title", "author", "description"},
					},
				},
				VectorIndexType: "hnsw",
			},
		},
	}

	err = client.Schema().ClassCreator().WithClass(booksMTSchema).Do(ctx)
	require.NoError(t, err)

	// Create tenants
	tenants := []string{"tenant1", "tenant2"}
	err = client.Schema().TenantsCreator().
		WithClassName(booksMTClass).
		WithTenants(
			models.Tenant{Name: tenants[0]},
			models.Tenant{Name: tenants[1]},
		).
		Do(ctx)
	require.NoError(t, err)

	// For each tenant: import data and verify
	for _, tenant := range tenants {
		t.Run(tenant, func(t *testing.T) {
			var sampleTitle, sampleAuthor, sampleDescription string

			// Generate and batch import 10k objects for this tenant
			t.Logf("Importing %d objects for tenant %s in batches of %d...", numObjects, tenant, batchSize)
			for i := 0; i < numObjects; i += batchSize {
				batcher := client.Batch().ObjectsBatcher()
				end := i + batchSize
				if end > numObjects {
					end = numObjects
				}
				for j := i; j < end; j++ {
					title := faker.Sentence()
					author := faker.Name()
					description := faker.Paragraph()

					if j == 0 {
						sampleTitle = title
						sampleAuthor = author
						sampleDescription = description
					}

					batcher = batcher.WithObject(&models.Object{
						Class:  booksMTClass,
						Tenant: tenant,
						Properties: map[string]interface{}{
							"title":       title,
							"author":      author,
							"description": description,
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

			// Verify object count via GraphQL aggregate
			t.Logf("[%s] Verifying object count...", tenant)
			result, err := client.GraphQL().Aggregate().
				WithClassName(booksMTClass).
				WithTenant(tenant).
				WithFields(graphql.Field{Name: "meta", Fields: []graphql.Field{{Name: "count"}}}).
				Do(ctx)
			require.NoError(t, err)
			require.Empty(t, result.Errors, "aggregate query returned errors: %v", result.Errors)

			aggregate := result.Data["Aggregate"].(map[string]interface{})[booksMTClass].([]interface{})
			require.NotEmpty(t, aggregate, "no aggregate found for collection %q tenant %s", booksMTClass, tenant)
			actualCount := aggregate[0].(map[string]interface{})["meta"].(map[string]interface{})["count"].(float64)
			require.Equal(t, numObjects, int(actualCount), "expected %d objects, got %d", numObjects, int(actualCount))
			t.Logf("[%s] Verified object count: %d", tenant, int(actualCount))

			// Test filtering by title (Equal operator)
			t.Logf("[%s] Testing filter by title...", tenant)
			titleWhere := filters.Where().
				WithPath([]string{"title"}).
				WithOperator(filters.Equal).
				WithValueText(sampleTitle)

			titleResult, err := client.GraphQL().Get().
				WithClassName(booksMTClass).
				WithTenant(tenant).
				WithFields(graphql.Field{Name: "title"}, graphql.Field{Name: "author"}, graphql.Field{Name: "description"}).
				WithWhere(titleWhere).
				Do(ctx)
			require.NoError(t, err)
			require.Empty(t, titleResult.Errors, "title filter query returned errors: %v", titleResult.Errors)

			titleData := titleResult.Data["Get"].(map[string]interface{})[booksMTClass].([]interface{})
			require.NotEmpty(t, titleData, "filter by title returned no results for %q", sampleTitle)
			t.Logf("[%s] Filter by title returned %d result(s)", tenant, len(titleData))

			// Test filtering by author (Equal operator)
			t.Logf("[%s] Testing filter by author...", tenant)
			authorWhere := filters.Where().
				WithPath([]string{"author"}).
				WithOperator(filters.Equal).
				WithValueText(sampleAuthor)

			authorResult, err := client.GraphQL().Get().
				WithClassName(booksMTClass).
				WithTenant(tenant).
				WithFields(graphql.Field{Name: "title"}, graphql.Field{Name: "author"}, graphql.Field{Name: "description"}).
				WithWhere(authorWhere).
				Do(ctx)
			require.NoError(t, err)
			require.Empty(t, authorResult.Errors, "author filter query returned errors: %v", authorResult.Errors)

			authorData := authorResult.Data["Get"].(map[string]interface{})[booksMTClass].([]interface{})
			require.NotEmpty(t, authorData, "filter by author returned no results for %q", sampleAuthor)
			t.Logf("[%s] Filter by author returned %d result(s)", tenant, len(authorData))

			// Test filtering by description (Like operator)
			t.Logf("[%s] Testing filter by description...", tenant)
			descPattern := fmt.Sprintf("%s*", sampleDescription[:min(50, len(sampleDescription))])
			descWhere := filters.Where().
				WithPath([]string{"description"}).
				WithOperator(filters.Like).
				WithValueText(descPattern)

			descResult, err := client.GraphQL().Get().
				WithClassName(booksMTClass).
				WithTenant(tenant).
				WithFields(graphql.Field{Name: "title"}, graphql.Field{Name: "author"}, graphql.Field{Name: "description"}).
				WithWhere(descWhere).
				Do(ctx)
			require.NoError(t, err)
			require.Empty(t, descResult.Errors, "description filter query returned errors: %v", descResult.Errors)

			descData := descResult.Data["Get"].(map[string]interface{})[booksMTClass].([]interface{})
			require.NotEmpty(t, descData, "filter by description returned no results for pattern %q", descPattern)
			t.Logf("[%s] Filter by description returned %d result(s)", tenant, len(descData))
		})
	}
}
