package alter_schema_operations

import (
	"context"
	"testing"
	"time"

	"github.com/stretchr/testify/require"
	wvt "github.com/weaviate/weaviate-go-client/v5/weaviate"
	"github.com/weaviate/weaviate-go-client/v5/weaviate/graphql"
	"github.com/weaviate/weaviate/entities/models"
)

// TestDeactivateTenant3MoviesMT deactivates tenant3 before dropping vector indexes.
func TestDeactivateTenant3MoviesMT(t *testing.T) {
	ctx := context.Background()

	client, err := wvt.NewClient(wvt.Config{
		Scheme: "http",
		Host:   "localhost:8080",
	})
	require.NoError(t, err)

	t.Log("Deactivating tenant3...")
	err = client.Schema().TenantsUpdater().
		WithClassName(moviesMTClass).
		WithTenants(models.Tenant{
			Name:           "tenant3",
			ActivityStatus: models.TenantActivityStatusINACTIVE,
		}).
		Do(ctx)
	require.NoError(t, err)
	t.Log("tenant3 deactivated")
}

// TestDropVectorIndicesMoviesMT drops all vector indexes from MoviesMT and
// verifies nearText search fails for tenant1 and tenant2.
func TestDropVectorIndicesMoviesMT(t *testing.T) {
	ctx := context.Background()

	client, err := wvt.NewClient(wvt.Config{
		Scheme: "http",
		Host:   "localhost:8080",
	})
	require.NoError(t, err)

	for _, vectorName := range moviesMTVectorizerVectors {
		t.Run("drop_"+vectorName, func(t *testing.T) {
			t.Logf("Dropping vector index %q...", vectorName)
			require.Eventually(t, func() bool {
				err := client.Schema().VectorIndexDeleter().
					WithClassName(moviesMTClass).
					WithVectorIndexName(vectorName).
					Do(ctx)
				if err != nil {
					t.Logf("Retrying drop of %q: %v", vectorName, err)
					return false
				}
				return true
			}, 2*time.Minute, 2*time.Second, "failed to drop vector index %q", vectorName)
			t.Logf("Successfully dropped vector index %q", vectorName)
		})
	}

	// Verify nearText fails for tenant1 and tenant2
	for _, tenant := range []string{"tenant1", "tenant2"} {
		for _, vectorName := range moviesMTVectorizerVectors {
			t.Run("verify_nearText_fails_"+tenant+"_"+vectorName, func(t *testing.T) {
				require.Eventually(t, func() bool {
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
					return err == nil && len(searchResult.Errors) > 0
				}, 30*time.Second, 500*time.Millisecond,
					"expected nearText search to fail for %s on %s", vectorName, tenant)
				t.Logf("nearText search correctly fails for %s on %s", vectorName, tenant)
			})
		}
	}
}

// TestActivateTenant3MoviesMT activates tenant3 after vector indexes have been
// dropped, then verifies nearText search also fails for tenant3.
func TestActivateTenant3MoviesMT(t *testing.T) {
	ctx := context.Background()

	client, err := wvt.NewClient(wvt.Config{
		Scheme: "http",
		Host:   "localhost:8080",
	})
	require.NoError(t, err)

	t.Log("Activating tenant3...")
	err = client.Schema().TenantsUpdater().
		WithClassName(moviesMTClass).
		WithTenants(models.Tenant{
			Name:           "tenant3",
			ActivityStatus: models.TenantActivityStatusACTIVE,
		}).
		Do(ctx)
	require.NoError(t, err)
	t.Log("tenant3 activated")

	// Verify nearText fails for tenant3 as well
	for _, vectorName := range moviesMTVectorizerVectors {
		t.Run("verify_nearText_fails_tenant3_"+vectorName, func(t *testing.T) {
			require.Eventually(t, func() bool {
				nearText := client.GraphQL().NearTextArgBuilder().
					WithConcepts([]string{"adventure movie"}).
					WithTargetVectors(vectorName)

				searchResult, err := client.GraphQL().Get().
					WithClassName(moviesMTClass).
					WithTenant("tenant3").
					WithFields(graphql.Field{Name: "title"}).
					WithNearText(nearText).
					WithLimit(5).
					Do(ctx)
				return err == nil && len(searchResult.Errors) > 0
			}, 30*time.Second, 500*time.Millisecond,
				"expected nearText search to fail for %s on tenant3", vectorName)
			t.Logf("nearText search correctly fails for %s on tenant3", vectorName)
		})
	}
}
