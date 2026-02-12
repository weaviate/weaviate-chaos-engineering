package alter_schema_operations

import (
	"context"
	"testing"
	"time"

	"github.com/stretchr/testify/require"
	wvt "github.com/weaviate/weaviate-go-client/v5/weaviate"
	"github.com/weaviate/weaviate-go-client/v5/weaviate/filters"
	"github.com/weaviate/weaviate-go-client/v5/weaviate/graphql"
)

func TestDropPropertyIndexesBooks(t *testing.T) {
	ctx := context.Background()

	client, err := wvt.NewClient(wvt.Config{
		Scheme: "http",
		Host:   "localhost:8080",
	})
	require.NoError(t, err)
	require.NotNil(t, client)

	properties := []string{"title", "author", "description"}

	// Delete filterable and searchable indexes for all properties
	for _, prop := range properties {
		t.Logf("Deleting filterable index for property %q...", prop)
		err = client.Schema().PropertyIndexDeleter().
			WithClassName(booksClass).
			WithPropertyName(prop).
			WithFilterable().
			Do(ctx)
		require.NoError(t, err, "failed to delete filterable index for %q", prop)

		t.Logf("Deleting searchable index for property %q...", prop)
		err = client.Schema().PropertyIndexDeleter().
			WithClassName(booksClass).
			WithPropertyName(prop).
			WithSearchable().
			Do(ctx)
		require.NoError(t, err, "failed to delete searchable index for %q", prop)
	}

	t.Log("All property indexes deleted. Verifying filtering no longer works...")

	// Verify filtering by title no longer returns results
	t.Log("Testing filter by title returns no results...")
	titleWhere := filters.Where().
		WithPath([]string{"title"}).
		WithOperator(filters.Equal).
		WithValueText("anything")

	titleResult, err := client.GraphQL().Get().
		WithClassName(booksClass).
		WithFields(graphql.Field{Name: "title"}).
		WithWhere(titleWhere).
		Do(ctx)
	require.NoError(t, err)
	require.NotEmpty(t, titleResult.Errors)

	t.Log("Filter by title correctly returns errors")

	// Verify filtering by author no longer returns results
	t.Log("Testing filter by author returns no results...")
	authorWhere := filters.Where().
		WithPath([]string{"author"}).
		WithOperator(filters.Equal).
		WithValueText("anything")

	authorResult, err := client.GraphQL().Get().
		WithClassName(booksClass).
		WithFields(graphql.Field{Name: "author"}).
		WithWhere(authorWhere).
		Do(ctx)
	require.NoError(t, err)
	require.NotEmpty(t, authorResult.Errors)

	t.Log("Filter by author correctly returns errors")

	// Verify filtering by description no longer returns results
	t.Log("Testing filter by description returns no results...")
	descWhere := filters.Where().
		WithPath([]string{"description"}).
		WithOperator(filters.Like).
		WithValueText("anything*")

	require.Eventually(t, func() bool {
		descResult, err := client.GraphQL().Get().
			WithClassName(booksClass).
			WithFields(graphql.Field{Name: "description"}).
			WithWhere(descWhere).
			Do(ctx)
		return err == nil && len(descResult.Errors) > 0
	}, 30*time.Second, 500*time.Millisecond, "expected filter by description to return errors")

	t.Log("Filter by description correctly errors")
}
