package main

import (
	"context"
	"testing"

	"github.com/stretchr/testify/require"
	wvt "github.com/weaviate/weaviate-go-client/v4/weaviate"
	"github.com/weaviate/weaviate-go-client/v4/weaviate/grpc"
)

func TestReindexing_Test1(t *testing.T) {
	ctx := context.Background()
	config := wvt.Config{
		Scheme: "http", Host: "localhost:8080",
		GrpcConfig: &grpc.Config{Host: "localhost:50051", Secured: false},
	}
	client, err := wvt.NewClient(config)
	require.NoError(t, err)
	require.NotNil(t, client)
	// clean DB
	err = client.Schema().AllDeleter().Do(ctx)
	require.NoError(t, err)
}
