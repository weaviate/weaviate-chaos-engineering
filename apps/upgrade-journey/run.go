package main

import (
	"context"
	"fmt"
	"log"
	"math/rand"
	"os"
	"time"

	"github.com/google/uuid"
	"github.com/weaviate/weaviate-go-client/v4/weaviate"
	"github.com/weaviate/weaviate-go-client/v4/weaviate/filters"
	"github.com/weaviate/weaviate-go-client/v4/weaviate/graphql"
	"github.com/weaviate/weaviate/entities/models"
)

var (
	versions       []string
	objectsCreated = 0
)

func main() {
	targetW, ok := os.LookupEnv("WEAVIATE_VERSION")
	if !ok {
		log.Fatal("missing WEAVIATE_VERSION")
	}

	minimumW, ok := os.LookupEnv("MINIMUM_WEAVIATE_VERSION")
	if !ok {
		log.Fatal("missing MINIMUM_WEAVIATE_VERSION")
	}

	var err error
	versions, err = buildVersionList(minimumW, targetW)
	if err != nil {
		log.Fatal(err)
	}

	log.Printf("configured minimum version is %s", minimumW)
	log.Printf("configured target version is %s", targetW)
	log.Printf("identified the following versions: %v", versions)

	cfg := weaviate.Config{
		Host:   "localhost:8080",
		Scheme: "http",
	}
	client := weaviate.New(cfg)

	err = do(client)
	if err != nil {
		log.Fatal(err)
	}
}

func do(client *weaviate.Client) error {
	rand.Seed(time.Now().UnixNano())
	ctx := context.Background()

	c := newCluster(3)

	if err := c.startNetwork(ctx); err != nil {
		return err
	}

	for i, version := range versions {

		if err := startOrUpgrade(ctx, c, i, version); err != nil {
			return err
		}

		if i == 0 {
			if err := createSchema(ctx, client); err != nil {
				return err
			}
		}

		if err := importForVersion(ctx, client, version); err != nil {
			return err
		}

		if err := verify(ctx, client, i); err != nil {
			return err
		}
	}

	return nil
}

func verify(ctx context.Context, client *weaviate.Client, i int) error {
	if err := findEachImportedObject(ctx, client, i); err != nil {
		return err
	}

	if err := aggregateObjects(ctx, client, i); err != nil {
		return err
	}

	if err := vectorSearch(ctx, client, i); err != nil {
		return err
	}

	return nil
}

func aggregateObjects(ctx context.Context, client *weaviate.Client,
	count int,
) error {
	result, err := client.GraphQL().Aggregate().
		WithClassName("Collection").
		WithFields(graphql.Field{Name: "meta", Fields: []graphql.Field{{Name: "count"}}}).
		Do(ctx)
	if err != nil {
		return err
	}

	if len(result.Errors) > 0 {
		return fmt.Errorf("%v", result.Errors)
	}

	actualCount := result.Data["Aggregate"].(map[string]interface{})["Collection"].([]interface{})[0].(map[string]interface{})["meta"].(map[string]interface{})["count"].(float64)
	if int(actualCount) != objectsCreated {
		return fmt.Errorf("aggregation: wanted %d, got %d", objectsCreated, int(actualCount))
	}

	return nil
}

func findEachImportedObject(ctx context.Context, client *weaviate.Client,
	posOfMaxVersion int,
) error {
	for i := 0; i <= posOfMaxVersion; i++ {
		version := versions[i]

		fields := []graphql.Field{
			{Name: "_additional { id }"},
			{Name: "version"},
			{Name: "object_count"},
			{Name: "ref_prop { ... on RefTarget {version} }"},
		}
		where := filters.Where().
			WithPath([]string{"version"}).
			WithOperator(filters.Equal).
			WithValueString(version)

		result, err := client.GraphQL().Get().
			WithClassName("Collection").
			WithFields(fields...).
			WithWhere(where).
			Do(ctx)
		if err != nil {
			return err
		}
		if len(result.Errors) > 0 {
			return fmt.Errorf("%v", result.Errors)
		}

		obj := result.Data["Get"].(map[string]interface{})["Collection"].([]interface{})[0].(map[string]interface{})
		actualVersion := obj["version"].(string)
		if version != actualVersion {
			return fmt.Errorf("root obj: wanted %s got %s", version, actualVersion)
		}

		refVersion := obj["ref_prop"].([]interface{})[0].(map[string]interface{})["version"].(string)
		if refVersion != actualVersion {
			return fmt.Errorf("ref object: wanted %s got %s", version, actualVersion)
		}

	}

	return nil
}

// vectorSearch runs one unfiltered vector search (HNSW), as well as one
// filtered vector search. Based on the small number of data objects in this
// test, we can assume that this always uses flat search, so we have two ways
// tested
//
// no recall assumptions are tested, just that the search works
func vectorSearch(ctx context.Context, client *weaviate.Client,
	posOfMaxVersion int,
) error {
	// unfiltered
	if err := unfilteredVectorSearch(ctx, client, posOfMaxVersion); err != nil {
		return fmt.Errorf("unfiltered vec: %w", err)
	}

	if err := filteredVectorSearch(ctx, client, posOfMaxVersion); err != nil {
		return fmt.Errorf("filtered vec: %w", err)
	}

	return nil
}

func unfilteredVectorSearch(ctx context.Context, client *weaviate.Client,
	posOfMaxVersion int,
) error {
	searchVec := make([]float32, 32)
	for i := range searchVec {
		searchVec[i] = rand.Float32()
	}

	fields := []graphql.Field{
		{Name: "_additional { id }"},
		{Name: "version"},
		{Name: "object_count"},
	}

	nearVector := client.GraphQL().NearVectorArgBuilder().
		WithVector(searchVec)

	result, err := client.GraphQL().Get().
		WithClassName("Collection").
		WithFields(fields...).
		WithNearVector(nearVector).
		WithLimit(10000).
		Do(ctx)
	if err != nil {
		return err
	}
	if len(result.Errors) > 0 {
		return fmt.Errorf("%v", result.Errors)
	}

	results := result.Data["Get"].(map[string]interface{})["Collection"].([]interface{})
	if len(results) != posOfMaxVersion+1 {
		return fmt.Errorf("not all objects returned in vector search")
	}

	return nil
}

// filteredVectorSearch applies a filter that always matches exactly one
// element, so the search result always has to match exactly what was in the
// filter
func filteredVectorSearch(ctx context.Context, client *weaviate.Client,
	posOfMaxVersion int,
) error {
	for i := 0; i <= posOfMaxVersion; i++ {
		version := versions[i]
		searchVec := make([]float32, 32)
		for i := range searchVec {
			searchVec[i] = rand.Float32()
		}

		fields := []graphql.Field{
			{Name: "_additional { id }"},
			{Name: "version"},
			{Name: "object_count"},
		}
		where := filters.Where().
			WithPath([]string{"version"}).
			WithOperator(filters.Equal).
			WithValueString(version)

		nearVector := client.GraphQL().NearVectorArgBuilder().
			WithVector(searchVec)

		result, err := client.GraphQL().Get().
			WithClassName("Collection").
			WithFields(fields...).
			WithWhere(where).
			WithNearVector(nearVector).
			Do(ctx)
		if err != nil {
			return err
		}
		if len(result.Errors) > 0 {
			return fmt.Errorf("%v", result.Errors)
		}

		actualVersion := result.Data["Get"].(map[string]interface{})["Collection"].([]interface{})[0].(map[string]interface{})["version"].(string)
		if version != actualVersion {
			return fmt.Errorf("wanted %s got %s", version, actualVersion)
		}

	}

	return nil
}

func createSchema(ctx context.Context, client *weaviate.Client) error {
	refTarget := &models.Class{
		Class: "RefTarget",
		Properties: []*models.Property{
			{
				DataType: []string{"string"},
				Name:     "version",
			},
			{
				DataType: []string{"int"},
				Name:     "object_count",
			},
		},
	}

	err := client.Schema().ClassCreator().WithClass(refTarget).Do(context.Background())
	if err != nil {
		return err
	}

	classObj := &models.Class{
		Class: "Collection",
		Properties: []*models.Property{
			{
				DataType: []string{"string"},
				Name:     "version",
			},
			{
				DataType: []string{"int"},
				Name:     "object_count",
			},
			{
				DataType: []string{"RefTarget"},
				Name:     "ref_prop",
			},
		},
	}

	err = client.Schema().ClassCreator().WithClass(classObj).Do(context.Background())
	if err != nil {
		return err
	}

	return nil
}

func importForVersion(ctx context.Context, client *weaviate.Client,
	version string,
) error {
	targetID := uuid.New().String()
	if err := importTargetObject(ctx, client, version, targetID); err != nil {
		return fmt.Errorf("source object: %w", err)
	}

	if err := importSourceObject(ctx, client, version, targetID); err != nil {
		return fmt.Errorf("source object: %w", err)
	}

	objectsCreated++

	return nil
}

func importTargetObject(ctx context.Context, client *weaviate.Client,
	version, id string,
) error {
	props := map[string]interface{}{
		"version":      version,
		"object_count": objectsCreated,
	}

	_, err := client.Data().Creator().
		WithClassName("RefTarget").
		WithID(id).
		WithProperties(props).
		Do(context.Background())
	if err != nil {
		return err
	}

	return nil
}

func importSourceObject(ctx context.Context, client *weaviate.Client,
	version, targetID string,
) error {
	props := map[string]interface{}{
		"version":      version,
		"object_count": objectsCreated,
		"ref_prop":     []interface{}{map[string]interface{}{"beacon": fmt.Sprintf("weaviate://localhost/RefTarget/%s", targetID)}},
	}

	vec := make([]float32, 32)
	for i := range vec {
		vec[i] = rand.Float32()
	}

	_, err := client.Data().Creator().
		WithClassName("Collection").
		WithVector(vec).
		WithProperties(props).
		Do(context.Background())
	if err != nil {
		return err
	}

	return nil
}

func startOrUpgrade(ctx context.Context, c *cluster, i int, version string) error {
	if i == 0 {
		return c.startAllNodes(ctx, version)
	}

	return c.rollingUpdate(ctx, versions[i%len(versions)])
}
