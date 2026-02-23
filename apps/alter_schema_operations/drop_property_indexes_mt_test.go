package alter_schema_operations

import (
	"context"
	"testing"
	"time"

	"github.com/stretchr/testify/require"
	wvt "github.com/weaviate/weaviate-go-client/v5/weaviate"
	"github.com/weaviate/weaviate-go-client/v5/weaviate/filters"
	"github.com/weaviate/weaviate-go-client/v5/weaviate/graphql"
	"github.com/weaviate/weaviate/entities/models"
)

func TestDropPropertyIndexesBooksMT(t *testing.T) {
	ctx := context.Background()

	client, err := wvt.NewClient(wvt.Config{
		Scheme: "http",
		Host:   "localhost:8080",
	})
	require.NoError(t, err)
	require.NotNil(t, client)

	// Deactivate tenant2
	t.Log("Deactivating tenant2...")
	err = client.Schema().TenantsUpdater().
		WithClassName(booksMTClass).
		WithTenants(models.Tenant{
			Name:           "tenant2",
			ActivityStatus: models.TenantActivityStatusINACTIVE,
		}).
		Do(ctx)
	require.NoError(t, err)

	// Delete filterable and searchable indexes for all properties
	properties := []string{"title", "author", "description"}
	for _, prop := range properties {
		t.Logf("Deleting filterable index for property %q...", prop)
		err = client.Schema().PropertyIndexDeleter().
			WithClassName(booksMTClass).
			WithPropertyName(prop).
			WithFilterable().
			Do(ctx)
		require.NoError(t, err, "failed to delete filterable index for %q", prop)

		t.Logf("Deleting searchable index for property %q...", prop)
		err = client.Schema().PropertyIndexDeleter().
			WithClassName(booksMTClass).
			WithPropertyName(prop).
			WithSearchable().
			Do(ctx)
		require.NoError(t, err, "failed to delete searchable index for %q", prop)
	}

	t.Log("All property indexes deleted. Verifying filtering no longer works on tenant1...")

	// Verify filtering by title no longer returns results on tenant1
	t.Log("Testing filter by title returns no results on tenant1...")
	titleWhere := filters.Where().
		WithPath([]string{"title"}).
		WithOperator(filters.Equal).
		WithValueText("anything")

	titleResult, err := client.GraphQL().Get().
		WithClassName(booksMTClass).
		WithTenant("tenant1").
		WithFields(graphql.Field{Name: "title"}).
		WithWhere(titleWhere).
		Do(ctx)
	require.NoError(t, err)
	require.NotEmpty(t, titleResult.Errors)

	t.Log("Filter by title correctly returns errors on tenant1")

	// Verify filtering by author no longer returns results on tenant1
	t.Log("Testing filter by author returns no results on tenant1...")
	authorWhere := filters.Where().
		WithPath([]string{"author"}).
		WithOperator(filters.Equal).
		WithValueText("anything")

	authorResult, err := client.GraphQL().Get().
		WithClassName(booksMTClass).
		WithTenant("tenant1").
		WithFields(graphql.Field{Name: "author"}).
		WithWhere(authorWhere).
		Do(ctx)
	require.NoError(t, err)
	require.NotEmpty(t, authorResult.Errors)

	t.Log("Filter by author correctly returns errors on tenant1")

	// Verify filtering by description no longer returns results on tenant1
	t.Log("Testing filter by description returns no results on tenant1...")
	descWhere := filters.Where().
		WithPath([]string{"description"}).
		WithOperator(filters.Like).
		WithValueText("anything*")

	require.Eventually(t, func() bool {
		descResult, err := client.GraphQL().Get().
			WithClassName(booksMTClass).
			WithTenant("tenant1").
			WithFields(graphql.Field{Name: "description"}).
			WithWhere(descWhere).
			Do(ctx)
		return err == nil && len(descResult.Errors) > 0
	}, 30*time.Second, 500*time.Millisecond, "expected filter by description to return errors on tenant1")

	t.Log("Filter by description correctly returns errors on tenant1")
}

func TestActivateTenant2AndVerifyNoFiltering(t *testing.T) {
	ctx := context.Background()

	client, err := wvt.NewClient(wvt.Config{
		Scheme: "http",
		Host:   "localhost:8080",
	})
	require.NoError(t, err)
	require.NotNil(t, client)

	// Activate tenant2
	t.Log("Activating tenant2...")
	err = client.Schema().TenantsUpdater().
		WithClassName(booksMTClass).
		WithTenants(models.Tenant{
			Name:           "tenant2",
			ActivityStatus: models.TenantActivityStatusACTIVE,
		}).
		Do(ctx)
	require.NoError(t, err)

	t.Log("Verifying filtering no longer works on tenant2...")

	// Verify filtering by title no longer returns results on tenant2
	t.Log("Testing filter by title returns no results on tenant2...")
	titleWhere := filters.Where().
		WithPath([]string{"title"}).
		WithOperator(filters.Equal).
		WithValueText("anything")

	titleResult, err := client.GraphQL().Get().
		WithClassName(booksMTClass).
		WithTenant("tenant2").
		WithFields(graphql.Field{Name: "title"}).
		WithWhere(titleWhere).
		Do(ctx)
	require.NoError(t, err)
	require.NotEmpty(t, titleResult.Errors)

	t.Log("Filter by title correctly returns errors on tenant2")

	// Verify filtering by author no longer returns results on tenant2
	t.Log("Testing filter by author returns no results on tenant2...")
	authorWhere := filters.Where().
		WithPath([]string{"author"}).
		WithOperator(filters.Equal).
		WithValueText("anything")

	authorResult, err := client.GraphQL().Get().
		WithClassName(booksMTClass).
		WithTenant("tenant2").
		WithFields(graphql.Field{Name: "author"}).
		WithWhere(authorWhere).
		Do(ctx)
	require.NoError(t, err)
	require.NotEmpty(t, authorResult.Errors)

	t.Log("Filter by author correctly returns errors on tenant2")

	// Verify filtering by description no longer returns results on tenant2
	t.Log("Testing filter by description returns no results on tenant2...")
	descWhere := filters.Where().
		WithPath([]string{"description"}).
		WithOperator(filters.Like).
		WithValueText("anything*")

	descResult, err := client.GraphQL().Get().
		WithClassName(booksMTClass).
		WithTenant("tenant2").
		WithFields(graphql.Field{Name: "description"}).
		WithWhere(descWhere).
		Do(ctx)
	require.NoError(t, err)
	require.NotEmpty(t, descResult.Errors)

	t.Log("Filter by description correctly returns errors on tenant2")
}
