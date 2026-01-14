package modulesettings

import (
	"context"
	"testing"

	"github.com/stretchr/testify/require"
	wvt "github.com/weaviate/weaviate-go-client/v5/weaviate"
	"github.com/weaviate/weaviate/entities/models"
)

const (
	TestOpenAI       = "TestOpenAI"
	TestCohere       = "TestCohere"
	TestHF           = "TestHF"
	TestOpenAI_nv    = "TestOpenAI_nv"
	TestOpenAI_nv2   = "TestOpenAI_nv2"
	TestCohere_nv    = "TestCohere_nv"
	TestCohere_nv2   = "TestCohere_nv2"
	TestHF_nv        = "TestHF_nv"
	TestHF_nv2       = "TestHF_nv2"
	TestWeaviate_nv  = "TestWeaviate_nv"
	TestWeaviate_nv2 = "TestWeaviate_nv2"
	TestJinaAI       = "TestJinaAI"
	TestM2V_Aws      = "TestM2V_Aws"
	TestMV_Cohere    = "TestMV_Cohere"
)

var all_collections = []string{
	TestOpenAI, TestCohere, TestHF, TestOpenAI_nv, TestOpenAI_nv2, TestCohere_nv,
	TestCohere_nv2, TestHF_nv, TestHF_nv2, TestWeaviate_nv,
	TestWeaviate_nv2, TestJinaAI, TestM2V_Aws, TestMV_Cohere,
}

func getClientConfig() wvt.Config {
	return wvt.Config{
		Scheme: "http", Host: "localhost:8080",
	}
}

func TestDeleteAllClasses(t *testing.T) {
	ctx := context.Background()
	client, err := wvt.NewClient(getClientConfig())
	require.NoError(t, err)
	require.NotNil(t, client)

	err = client.Schema().AllDeleter().Do(ctx)
	require.NoError(t, err)
}

func TestModuleSettings_v1_20(t *testing.T) {
	ctx := context.Background()
	client, err := wvt.NewClient(getClientConfig())
	require.NoError(t, err)
	require.NotNil(t, client)

	testSchema := &models.Class{
		Class: TestOpenAI,
		ModuleConfig: map[string]any{
			"text2vec-openai": map[string]any{
				"vectorizePropertyName": false,
				"vectorizeClassName":    false,
			},
		},
		Vectorizer: "text2vec-openai",
		Properties: []*models.Property{
			{
				Name:        "property1",
				DataType:    []string{"text"},
				Description: "First test property",
			},
		},
	}

	err = client.Schema().ClassCreator().WithClass(testSchema).Do(ctx)
	require.NoError(t, err)

	testSchema = &models.Class{
		Class: TestCohere,
		ModuleConfig: map[string]any{
			"text2vec-cohere": map[string]any{
				"vectorizePropertyName": false,
				"vectorizeClassName":    false,
			},
		},
		Vectorizer: "text2vec-cohere",
		Properties: []*models.Property{
			{
				Name:        "property1",
				DataType:    []string{"text"},
				Description: "First test property",
			},
		},
	}

	err = client.Schema().ClassCreator().WithClass(testSchema).Do(ctx)
	require.NoError(t, err)

	testSchema = &models.Class{
		Class: TestHF,
		ModuleConfig: map[string]any{
			"text2vec-huggingface": map[string]any{
				"vectorizePropertyName": false,
				"vectorizeClassName":    false,
			},
		},
		Vectorizer: "text2vec-huggingface",
		Properties: []*models.Property{
			{
				Name:        "property1",
				DataType:    []string{"text"},
				Description: "First test property",
			},
		},
	}

	err = client.Schema().ClassCreator().WithClass(testSchema).Do(ctx)
	require.NoError(t, err)
}

func TestModuleNamedVectorsSettings_v1_24(t *testing.T) {
	ctx := context.Background()
	client, err := wvt.NewClient(getClientConfig())
	require.NoError(t, err)
	require.NotNil(t, client)

	testSchema := &models.Class{
		Class: TestOpenAI_nv,
		VectorConfig: map[string]models.VectorConfig{
			"openai": {
				Vectorizer: map[string]any{
					"text2vec-openai": map[string]any{
						"vectorizePropertyName": false,
						"vectorizeClassName":    false,
						"properties":            []any{"property1"},
					},
				},
				VectorIndexType: "hnsw",
			},
		},
		Properties: []*models.Property{
			{
				Name:        "property1",
				DataType:    []string{"text"},
				Description: "First test property",
			},
		},
	}

	err = client.Schema().ClassCreator().WithClass(testSchema).Do(ctx)
	require.NoError(t, err)

	testSchema = &models.Class{
		Class: TestOpenAI_nv2,
		VectorConfig: map[string]models.VectorConfig{
			"openai": {
				Vectorizer: map[string]any{
					"text2vec-openai": map[string]any{
						"vectorizePropertyName": false,
						"vectorizeClassName":    false,
						"properties":            []any{"property1"},
						"model":                 "text-embedding-3-large",
					},
				},
				VectorIndexType: "hnsw",
			},
		},
		Properties: []*models.Property{
			{
				Name:        "property1",
				DataType:    []string{"text"},
				Description: "First test property",
			},
		},
	}

	err = client.Schema().ClassCreator().WithClass(testSchema).Do(ctx)
	require.NoError(t, err)

	testSchema = &models.Class{
		Class: TestCohere_nv,
		VectorConfig: map[string]models.VectorConfig{
			"cohere": {
				Vectorizer: map[string]any{
					"text2vec-cohere": map[string]any{
						"vectorizePropertyName": false,
						"vectorizeClassName":    false,
						"properties":            []any{"property1"},
					},
				},
				VectorIndexType: "hnsw",
			},
		},
		Properties: []*models.Property{
			{
				Name:        "property1",
				DataType:    []string{"text"},
				Description: "First test property",
			},
		},
	}

	err = client.Schema().ClassCreator().WithClass(testSchema).Do(ctx)
	require.NoError(t, err)

	testSchema = &models.Class{
		Class: TestCohere_nv2,
		VectorConfig: map[string]models.VectorConfig{
			"cohere": {
				Vectorizer: map[string]any{
					"text2vec-cohere": map[string]any{
						"vectorizePropertyName": false,
						"vectorizeClassName":    false,
						"properties":            []any{"property1"},
						"model":                 "embed-multilingual-v3.0",
					},
				},
				VectorIndexType: "hnsw",
			},
		},
		Properties: []*models.Property{
			{
				Name:        "property1",
				DataType:    []string{"text"},
				Description: "First test property",
			},
		},
	}

	err = client.Schema().ClassCreator().WithClass(testSchema).Do(ctx)
	require.NoError(t, err)

	testSchema = &models.Class{
		Class: TestHF_nv,
		VectorConfig: map[string]models.VectorConfig{
			"hf": {
				Vectorizer: map[string]any{
					"text2vec-huggingface": map[string]any{
						"vectorizePropertyName": false,
						"vectorizeClassName":    false,
						"properties":            []any{"property1"},
					},
				},
				VectorIndexType: "hnsw",
			},
		},
		Properties: []*models.Property{
			{
				Name:        "property1",
				DataType:    []string{"text"},
				Description: "First test property",
			},
		},
	}

	err = client.Schema().ClassCreator().WithClass(testSchema).Do(ctx)
	require.NoError(t, err)

	testSchema = &models.Class{
		Class: TestHF_nv2,
		VectorConfig: map[string]models.VectorConfig{
			"hf": {
				Vectorizer: map[string]any{
					"text2vec-huggingface": map[string]any{
						"vectorizePropertyName": false,
						"vectorizeClassName":    false,
						"properties":            []any{"property1"},
						"model":                 "Qwen/Qwen-Image-Layered",
					},
				},
				VectorIndexType: "hnsw",
			},
		},
		Properties: []*models.Property{
			{
				Name:        "property1",
				DataType:    []string{"text"},
				Description: "First test property",
			},
		},
	}

	err = client.Schema().ClassCreator().WithClass(testSchema).Do(ctx)
	require.NoError(t, err)
}

func TestModuleNamedVectorsSettings_v1_27(t *testing.T) {
	ctx := context.Background()
	client, err := wvt.NewClient(getClientConfig())
	require.NoError(t, err)
	require.NotNil(t, client)

	testSchema := &models.Class{
		Class: TestWeaviate_nv,
		VectorConfig: map[string]models.VectorConfig{
			"weaviate": {
				Vectorizer: map[string]any{
					"text2vec-weaviate": map[string]any{
						"vectorizePropertyName": false,
						"vectorizeClassName":    false,
						"properties":            []any{"property1"},
					},
				},
				VectorIndexType: "hnsw",
			},
		},
		Properties: []*models.Property{
			{
				Name:        "property1",
				DataType:    []string{"text"},
				Description: "First test property",
			},
		},
	}

	err = client.Schema().ClassCreator().WithClass(testSchema).Do(ctx)
	require.NoError(t, err)

	testSchema = &models.Class{
		Class: TestWeaviate_nv2,
		VectorConfig: map[string]models.VectorConfig{
			"weaviate": {
				Vectorizer: map[string]any{
					"text2vec-weaviate": map[string]any{
						"vectorizePropertyName": false,
						"vectorizeClassName":    false,
						"properties":            []any{"property1"},
						"model":                 "THIS_IS_FAKE_MODEL/text-embedding-3-large",
						"baseUrl":               "htts://this-needs-to-be-migrated-to-baseURL.com",
					},
				},
				VectorIndexType: "hnsw",
			},
		},
		Properties: []*models.Property{
			{
				Name:        "property1",
				DataType:    []string{"text"},
				Description: "First test property",
			},
		},
	}

	err = client.Schema().ClassCreator().WithClass(testSchema).Do(ctx)
	require.NoError(t, err)

	testSchema = &models.Class{
		Class: TestJinaAI,
		VectorConfig: map[string]models.VectorConfig{
			"jinaai": {
				Vectorizer: map[string]any{
					"text2vec-jinaai": map[string]any{
						"vectorizePropertyName": false,
						"vectorizeClassName":    false,
						"properties":            []any{"property1"},
					},
				},
				VectorIndexType: "hnsw",
			},
		},
		Properties: []*models.Property{
			{
				Name:        "property1",
				DataType:    []string{"text"},
				Description: "First test property",
			},
		},
	}

	err = client.Schema().ClassCreator().WithClass(testSchema).Do(ctx)
	require.NoError(t, err)
}

func TestModuleNamedVectorsSettings_v1_32(t *testing.T) {
	ctx := context.Background()
	client, err := wvt.NewClient(getClientConfig())
	require.NoError(t, err)
	require.NotNil(t, client)

	testSchema := &models.Class{
		Class: TestM2V_Aws,
		VectorConfig: map[string]models.VectorConfig{
			"weaviate": {
				Vectorizer: map[string]any{
					"multi2vec-aws": map[string]any{
						"model":       "some-custom-model",
						"textFields":  []any{"property1"},
						"imageFields": []any{"property2"},
						"weights": map[string]any{
							"textFields":  []any{0.1},
							"imageFields": []any{0.9},
						},
						"vectorizeClassName": false,
						"region":             "",
					},
				},
				VectorIndexType: "hnsw",
			},
		},
		Properties: []*models.Property{
			{
				Name:        "property1",
				DataType:    []string{"text"},
				Description: "First test property",
			},
			{
				Name:     "property2",
				DataType: []string{"blob"},
			},
		},
	}

	err = client.Schema().ClassCreator().WithClass(testSchema).Do(ctx)
	require.NoError(t, err)

	testSchema = &models.Class{
		Class: TestMV_Cohere,
		VectorConfig: map[string]models.VectorConfig{
			"cohere": {
				Vectorizer: map[string]any{
					"multi2vec-cohere": map[string]any{
						"textFields":  []any{"property1"},
						"imageFields": []any{"property2"},
						"weights": map[string]any{
							"textFields":  []any{0.1},
							"imageFields": []any{0.9},
						},
					},
				},
				VectorIndexType: "hnsw",
			},
		},
		Properties: []*models.Property{
			{
				Name:        "property1",
				DataType:    []string{"text"},
				Description: "First test property",
			},
			{
				Name:     "property2",
				DataType: []string{"blob"},
			},
		},
	}

	err = client.Schema().ClassCreator().WithClass(testSchema).Do(ctx)
	require.NoError(t, err)
}

func TestUpdateCollection(t *testing.T) {
	ctx := context.Background()
	client, err := wvt.NewClient(getClientConfig())
	require.NoError(t, err)
	require.NotNil(t, client)

	for _, className := range all_collections {
		t.Logf("Trying to update collection: %s", className)
		class, err := client.Schema().ClassGetter().WithClassName(className).Do(ctx)
		require.NoError(t, err)
		require.NotNil(t, class)

		class.ReplicationConfig = &models.ReplicationConfig{
			DeletionStrategy: models.ReplicationConfigDeletionStrategyTimeBasedResolution,
			AsyncEnabled:     true,
		}

		err = client.Schema().ClassUpdater().WithClass(class).Do(ctx)
		require.NoError(t, err)

		class, err = client.Schema().ClassGetter().WithClassName(className).Do(ctx)
		require.NoError(t, err)
		require.NotNil(t, class)
		require.NotNil(t, class.ReplicationConfig)
		require.Equal(t, true, class.ReplicationConfig.AsyncEnabled)
		require.Equal(t, models.ReplicationConfigDeletionStrategyTimeBasedResolution, class.ReplicationConfig.DeletionStrategy)
	}
}
