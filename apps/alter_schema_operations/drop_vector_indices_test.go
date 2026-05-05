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

// TestDropVectorIndicesMovies drops all vectorizer-based vector indexes from
// the Movies collection one by one and verifies nearText search fails after each drop.
func TestDropVectorIndicesMovies(t *testing.T) {
	ctx := context.Background()

	client, err := wvt.NewClient(wvt.Config{
		Scheme: "http",
		Host:   "localhost:8080",
	})
	require.NoError(t, err)
	require.NotNil(t, client)

	for _, vectorName := range vectorizerVectors {
		t.Run("drop_"+vectorName, func(t *testing.T) {
			t.Logf("Dropping vector index %q...", vectorName)
			require.Eventually(t, func() bool {
				err := client.Schema().VectorIndexDeleter().
					WithClassName(moviesClass).
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

	// Verify nearText no longer works for vectorizer-based vectors
	for _, vectorName := range vectorizerVectors {
		t.Run("verify_nearText_fails_"+vectorName, func(t *testing.T) {
			require.Eventually(t, func() bool {
				nearText := client.GraphQL().NearTextArgBuilder().
					WithConcepts([]string{"adventure movie"}).
					WithTargetVectors(vectorName)

				searchResult, err := client.GraphQL().Get().
					WithClassName(moviesClass).
					WithFields(graphql.Field{Name: "title"}).
					WithNearText(nearText).
					WithLimit(5).
					Do(ctx)
				return err == nil && len(searchResult.Errors) > 0
			}, 30*time.Second, 500*time.Millisecond,
				"expected nearText search to fail for dropped vector index %q", vectorName)
			t.Logf("nearText search correctly fails for %s", vectorName)
		})
	}
}

// TestDropVectorIndicesMVMovies drops all multi-vector indexes from
// the MVMovies collection one by one and verifies nearVector search fails after each drop.
func TestDropVectorIndicesMVMovies(t *testing.T) {
	ctx := context.Background()

	client, err := wvt.NewClient(wvt.Config{
		Scheme: "http",
		Host:   "localhost:8080",
	})
	require.NoError(t, err)
	require.NotNil(t, client)

	for _, vectorName := range multiVectors {
		t.Run("drop_"+vectorName, func(t *testing.T) {
			t.Logf("Dropping vector index %q...", vectorName)
			require.Eventually(t, func() bool {
				err := client.Schema().VectorIndexDeleter().
					WithClassName(mvMoviesClass).
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

	// Verify nearVector no longer works for multi-vector vectors
	sampleMultiVec := randomMultiVector(multiVecTokens, multiVecDim)
	for _, vectorName := range multiVectors {
		t.Run("verify_nearVector_fails_"+vectorName, func(t *testing.T) {
			require.Eventually(t, func() bool {
				nearVec := client.GraphQL().NearVectorArgBuilder().
					WithVectorPerTarget(map[string]models.Vector{
						vectorName: sampleMultiVec,
					}).
					WithTargetVectors(vectorName)

				searchResult, err := client.GraphQL().Get().
					WithClassName(mvMoviesClass).
					WithFields(graphql.Field{Name: "title"}).
					WithNearVector(nearVec).
					WithLimit(5).
					Do(ctx)
				return err == nil && len(searchResult.Errors) > 0
			}, 30*time.Second, 500*time.Millisecond,
				"expected nearVector search to fail for dropped vector index %q", vectorName)
			t.Logf("nearVector search correctly fails for %s", vectorName)
		})
	}
}
