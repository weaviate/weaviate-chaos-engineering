package tests

import (
	"context"
	"testing"

	"github.com/jaekwon/testify/assert"
	"github.com/stretchr/testify/require"
	wvt "github.com/weaviate/weaviate-go-client/v4/weaviate"
	"github.com/weaviate/weaviate/entities/models"
	"github.com/weaviate/weaviate/entities/schema"
)

const endpoint = "localhost:8080"

func TestCreateClass_v1_25(t *testing.T) {
	ctx := context.Background()
	client, err := wvt.NewClient(wvt.Config{Scheme: "http", Host: endpoint})
	require.NoError(t, err)
	t.Run("check v1.25 version", func(t *testing.T) {
		meta, err := client.Misc().MetaGetter().Do(ctx)
		require.NoError(t, err)
		assert.Equal(t, "1.25.24", meta.Version)
	})
	t.Run("remove all", func(t *testing.T) {
		require.NoError(t, client.Schema().AllDeleter().Do(ctx))
	})
	t.Run("create a class with Object type property and nested properties", func(t *testing.T) {
		require.NoError(t, client.Schema().ClassCreator().WithClass(AllPropertiesClass).Do(ctx))
	})
	t.Run("indexRangeFilters setting should not exist", testProperties(false))
}

func TestRangeFiltersExists(t *testing.T) {
	t.Run("indexRangeFilters setting should exist", testProperties(true))
}

func TestRangeFiltersDoesntExist(t *testing.T) {
	t.Run("indexRangeFilters setting should not exist", testProperties(false))
}

func testProperties(shouldExist bool) func(t *testing.T) {
	return func(t *testing.T) {
		client, err := wvt.NewClient(wvt.Config{Scheme: "http", Host: endpoint})
		require.NoError(t, err)
		res, err := client.Schema().ClassGetter().WithClassName(AllPropertiesClass.Class).Do(context.Background())
		require.NoError(t, err)
		for _, prop := range res.Properties {
			if prop.Name == "objectProperty" {
				for _, nestedProp := range prop.NestedProperties {
					assert.NotNil(t, nestedProp.IndexFilterable)
					assert.NotNil(t, nestedProp.IndexSearchable)
					if shouldExist {
						assert.NotNil(t, nestedProp.IndexRangeFilters)
					} else {
						assert.Nil(t, nestedProp.IndexRangeFilters)
					}
				}
			}
		}
	}
}

var (
	AllPropertiesClassName = "AllProperties"
	AllPropertiesID1       = "00000000-0000-0000-0000-000000000001"
)

var AllPropertiesClass = &models.Class{
	Class: AllPropertiesClassName,
	Properties: []*models.Property{
		{
			Name:         "objectProperty",
			DataType:     schema.DataTypeObject.PropString(),
			Tokenization: "",
			NestedProperties: []*models.NestedProperty{
				{
					Name:     "text",
					DataType: schema.DataTypeText.PropString(),
				},
				{
					Name:     "texts",
					DataType: schema.DataTypeTextArray.PropString(),
				},
				{
					Name:     "number",
					DataType: schema.DataTypeNumber.PropString(),
				},
				{
					Name:     "numbers",
					DataType: schema.DataTypeNumberArray.PropString(),
				},
				{
					Name:     "int",
					DataType: schema.DataTypeInt.PropString(),
				},
				{
					Name:     "ints",
					DataType: schema.DataTypeIntArray.PropString(),
				},
				{
					Name:     "date",
					DataType: schema.DataTypeDate.PropString(),
				},
				{
					Name:     "dates",
					DataType: schema.DataTypeDateArray.PropString(),
				},
				{
					Name:     "bool",
					DataType: schema.DataTypeBoolean.PropString(),
				},
				{
					Name:     "bools",
					DataType: schema.DataTypeBooleanArray.PropString(),
				},
				{
					Name:     "uuid",
					DataType: schema.DataTypeUUID.PropString(),
				},
				{
					Name:     "uuids",
					DataType: schema.DataTypeUUIDArray.PropString(),
				},
				{
					Name:         "nested_int",
					DataType:     schema.DataTypeInt.PropString(),
					Tokenization: "",
				},
				{
					Name:         "nested_number",
					DataType:     schema.DataTypeNumber.PropString(),
					Tokenization: "",
				},
				{
					Name:         "nested_text",
					DataType:     schema.DataTypeText.PropString(),
					Tokenization: models.PropertyTokenizationWord,
				},
				{
					Name:         "nested_objects",
					DataType:     schema.DataTypeObject.PropString(),
					Tokenization: "",
					NestedProperties: []*models.NestedProperty{
						{
							Name:         "nested_bool_lvl2",
							DataType:     schema.DataTypeBoolean.PropString(),
							Tokenization: "",
						},
						{
							Name:         "nested_numbers_lvl2",
							DataType:     schema.DataTypeNumberArray.PropString(),
							Tokenization: "",
						},
					},
				},
				{
					Name:         "nested_array_objects",
					DataType:     schema.DataTypeObjectArray.PropString(),
					Tokenization: "",
					NestedProperties: []*models.NestedProperty{
						{
							Name:         "nested_bool_lvl2",
							DataType:     schema.DataTypeBoolean.PropString(),
							Tokenization: "",
						},
						{
							Name:         "nested_numbers_lvl2",
							DataType:     schema.DataTypeNumberArray.PropString(),
							Tokenization: "",
						},
					},
				},
			},
		},
	},
}
