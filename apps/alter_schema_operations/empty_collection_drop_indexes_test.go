package alter_schema_operations

import (
	"context"
	"testing"

	"github.com/stretchr/testify/require"
	wvt "github.com/weaviate/weaviate-go-client/v5/weaviate"
	"github.com/weaviate/weaviate/entities/models"
)

const bookEmptyClass = "BookEmpty"

func TestDropPropertyIndexesEmptyCollection(t *testing.T) {
	ctx := context.Background()

	client, err := wvt.NewClient(wvt.Config{
		Scheme: "http",
		Host:   "localhost:8080",
	})
	require.NoError(t, err)
	require.NotNil(t, client)

	// Create "BookEmpty" collection with title (text) and size (number)
	vTrue := true
	bookEmptySchema := &models.Class{
		Class: bookEmptyClass,
		Properties: []*models.Property{
			{
				Name:            "title",
				DataType:        []string{"text"},
				IndexFilterable: &vTrue,
				IndexSearchable: &vTrue,
			},
			{
				Name:              "size",
				DataType:          []string{"number"},
				IndexRangeFilters: &vTrue,
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
						"properties": []any{"title"},
					},
				},
				VectorIndexType: "hnsw",
			},
		},
	}

	err = client.Schema().ClassCreator().WithClass(bookEmptySchema).Do(ctx)
	require.NoError(t, err)

	// Verify initial schema state
	class := getClass(t, ctx, client, bookEmptyClass)
	titleProp := findProperty(t, class, "title")
	require.True(t, *titleProp.IndexFilterable, "title.IndexFilterable should be true initially")
	require.True(t, *titleProp.IndexSearchable, "title.IndexSearchable should be true initially")
	sizeProp := findProperty(t, class, "size")
	require.True(t, *sizeProp.IndexRangeFilters, "size.IndexRangeFilters should be true initially")

	// 1. Delete title filterable index
	t.Log("Deleting title filterable index...")
	err = client.Schema().PropertyIndexDeleter().
		WithClassName(bookEmptyClass).
		WithPropertyName("title").
		WithFilterable().
		Do(ctx)
	require.NoError(t, err)

	class = getClass(t, ctx, client, bookEmptyClass)
	titleProp = findProperty(t, class, "title")
	require.False(t, *titleProp.IndexFilterable, "title.IndexFilterable should be false after deletion")
	require.True(t, *titleProp.IndexSearchable, "title.IndexSearchable should still be true")
	t.Log("title filterable index removed, schema verified")

	// 2. Delete title searchable index
	t.Log("Deleting title searchable index...")
	err = client.Schema().PropertyIndexDeleter().
		WithClassName(bookEmptyClass).
		WithPropertyName("title").
		WithSearchable().
		Do(ctx)
	require.NoError(t, err)

	class = getClass(t, ctx, client, bookEmptyClass)
	titleProp = findProperty(t, class, "title")
	require.False(t, *titleProp.IndexFilterable, "title.IndexFilterable should still be false")
	require.False(t, *titleProp.IndexSearchable, "title.IndexSearchable should be false after deletion")
	t.Log("title searchable index removed, schema verified")

	// 3. Delete size filterable index
	t.Log("Deleting size filterable index...")
	err = client.Schema().PropertyIndexDeleter().
		WithClassName(bookEmptyClass).
		WithPropertyName("size").
		WithFilterable().
		Do(ctx)
	require.NoError(t, err)

	class = getClass(t, ctx, client, bookEmptyClass)
	sizeProp = findProperty(t, class, "size")
	require.False(t, *sizeProp.IndexFilterable, "size.IndexFilterable should be false after deletion")
	require.True(t, *sizeProp.IndexRangeFilters, "size.IndexRangeFilters should still be true")
	t.Log("size filterable index removed, schema verified")

	// 4. Delete size range filters index
	t.Log("Deleting size range filters index...")
	err = client.Schema().PropertyIndexDeleter().
		WithClassName(bookEmptyClass).
		WithPropertyName("size").
		WithRangeFilters().
		Do(ctx)
	require.NoError(t, err)

	class = getClass(t, ctx, client, bookEmptyClass)
	sizeProp = findProperty(t, class, "size")
	require.False(t, *sizeProp.IndexFilterable, "size.IndexFilterable should still be false")
	require.False(t, *sizeProp.IndexRangeFilters, "size.IndexRangeFilters should be false after deletion")
	t.Log("size range filters index removed, schema verified")

	t.Log("All property indexes successfully deleted and schema verified")
}

func getClass(t *testing.T, ctx context.Context, client *wvt.Client, className string) *models.Class {
	t.Helper()
	class, err := client.Schema().ClassGetter().WithClassName(className).Do(ctx)
	require.NoError(t, err)
	require.NotNil(t, class)
	return class
}

func findProperty(t *testing.T, class *models.Class, name string) *models.Property {
	t.Helper()
	for _, p := range class.Properties {
		if p.Name == name {
			return p
		}
	}
	t.Fatalf("property %q not found in class %q", name, class.Class)
	return nil
}
