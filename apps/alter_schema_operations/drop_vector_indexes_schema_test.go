package alter_schema_operations

import (
	"context"
	"testing"

	"github.com/stretchr/testify/require"
	wvt "github.com/weaviate/weaviate-go-client/v5/weaviate"
)

// TestVerifyVectorConfigsDroppedFromSchema is the final assertion of the
// drop-vector-index pipeline: once every vector index has been dropped from a
// collection, none of the originally-configured named vectors must remain in
// that collection's schema (vectorConfig).
//
// This runs at the very end, after all three collections (Movies, MVMovies,
// MoviesMT) have had every vector index dropped and their objects cleaned up,
// so the functional per-collection checks stay green even if this schema
// assertion regresses.
func TestVerifyVectorConfigsDroppedFromSchema(t *testing.T) {
	ctx := context.Background()

	client, err := wvt.NewClient(wvt.Config{
		Scheme: "http",
		Host:   "localhost:8080",
	})
	require.NoError(t, err)
	require.NotNil(t, client)

	collections := []struct {
		class   string
		vectors []string
	}{
		{moviesClass, vectorizerVectors},
		{mvMoviesClass, multiVectors},
		{moviesMTClass, moviesMTVectorizerVectors},
	}

	for _, c := range collections {
		for _, vectorName := range c.vectors {
			t.Run(c.class+"_"+vectorName, func(t *testing.T) {
				assertVectorDroppedFromSchema(ctx, t, client, c.class, vectorName)
			})
		}
	}
}
