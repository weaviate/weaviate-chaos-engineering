package main

import (
	"context"
	"fmt"
	"io"
	"log"
	"math/rand"
	"os"
	"strconv"
	"time"

	"github.com/cenkalti/backoff/v4"
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
	ctx := context.Background()
	targetW, ok := os.LookupEnv("WEAVIATE_VERSION")
	if !ok {
		log.Fatal("missing WEAVIATE_VERSION")
	}

	minimumW, ok := os.LookupEnv("MINIMUM_WEAVIATE_VERSION")
	if !ok {
		log.Fatal("missing MINIMUM_WEAVIATE_VERSION")
	}

	nodes, ok := os.LookupEnv("NUM_NODES")
	if !ok {
		log.Fatal("missing NUM_NODES")
	}

	var err error
	numNodes, err := strconv.Atoi(nodes)
	if err != nil {
		log.Fatal(err)
	}

	versions, err = buildVersionList(ctx, minimumW, targetW)
	if err != nil {
		log.Fatal(err)
	}

	log.Printf("configured minimum version is %s", minimumW)
	log.Printf("configured target version is %s", targetW)
	log.Printf("number of nodes is %d", numNodes)
	log.Printf("identified the following versions: %v", versions)

	cfg := weaviate.Config{
		Host:   "localhost:8080",
		Scheme: "http",
	}
	client := weaviate.New(cfg)

	if cluster, err := do(ctx, client, numNodes); err != nil {
		log.Fatal(err)
		ctx := context.Background()
		for _, c := range cluster.containers {
			logReader, logErr := c.Logs(ctx)
			if logErr != nil {
				name, _ := c.Name(ctx)
				log.Fatal(fmt.Printf("can't get container %s logs, err: %w", name, logErr))
			}
			io.Copy(os.Stdout, logReader)
		}
	}
}

func do(ctx context.Context, client *weaviate.Client, numNodes int) (*cluster, error) {
	rand.Seed(time.Now().UnixNano())

	c := newCluster(numNodes)

	if err := c.startNetwork(ctx); err != nil {
		return c, err
	}

	for i, version := range versions {

		if err := startOrUpgrade(ctx, c, i, version); err != nil {
			return c, err
		}

		if i == 0 {
			if err := createSchema(ctx, client); err != nil {
				return c, err
			}
		}

		if err := importForVersion(ctx, client, version); err != nil {
			return c, err
		}

		backoff.Retry(
			func() error { return verify(ctx, client, i) },
			backoff.WithMaxRetries(backoff.NewConstantBackOff(1*time.Second), 15),
		)
	}

	return c, nil
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

	aggregate := result.Data["Aggregate"].(map[string]interface{})["Collection"].([]interface{})
	if len(aggregate) <= 0 {
		return fmt.Errorf("no aggregate found for collection 'Collection'")
	}
	actualCount := aggregate[0].(map[string]interface{})["meta"].(map[string]interface{})["count"].(float64)
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

		if err := findObjectUsingVersionString(ctx, client, version); err != nil {
			return fmt.Errorf("string filter: %w", err)
		}

		if err := findObjectUsingVersionInts(ctx, client, version); err != nil {
			return fmt.Errorf("and-ed int filter: %w", err)
		}

	}

	return nil
}

func findObjectUsingVersionString(ctx context.Context, client *weaviate.Client,
	version string,
) error {
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

	collection := result.Data["Get"].(map[string]interface{})["Collection"].([]interface{})
	if len(collection) <= 0 {
		return fmt.Errorf("no data returned for collection 'Collection'")
	}
	obj := collection[0].(map[string]interface{})
	actualVersion := obj["version"].(string)
	if version != actualVersion {
		return fmt.Errorf("root obj: wanted %s got %s", version, actualVersion)
	}

	refProp, exists := obj["ref_prop"]
	if !exists || refProp == nil {
		return fmt.Errorf("no ref found for 'ref_prop'")
	}

	ref := refProp.([]interface{})
	if len(ref) <= 0 {
		return fmt.Errorf("no ref found for 'ref_prop'")
	}
	v, exists := ref[0].(map[string]interface{})["version"]
	if !exists {
		return fmt.Errorf("no version found for 'ref'")
	}

	refVersion := v.(string)
	if refVersion != actualVersion {
		return fmt.Errorf("ref object: wanted %s got %s", version, actualVersion)
	}

	return nil
}

func findObjectUsingVersionInts(ctx context.Context, client *weaviate.Client,
	version string,
) error {
	parsed, ok := maybeParseSingleSemverWithoutLeadingV(version)
	if !ok {
		log.Printf("skipping int version test because %q is not a valid semver", version)
		return nil
	}
	fields := []graphql.Field{
		{Name: "_additional { id }"},
		{Name: "version"},
		{Name: "object_count"},
		{Name: "ref_prop { ... on RefTarget {version} }"},
	}

	where := filters.Where().
		WithOperator(filters.And).
		WithOperands([]*filters.WhereBuilder{
			filters.Where().
				WithOperator(filters.Equal).
				WithPath([]string{"major_version"}).
				WithValueInt(parsed.major()),
			filters.Where().
				WithOperator(filters.Equal).
				WithPath([]string{"minor_version"}).
				WithValueInt(parsed.minor()),
			filters.Where().
				WithOperator(filters.Equal).
				WithPath([]string{"patch_version"}).
				WithValueInt(parsed.patch()),
		},
		)

	result, err := client.GraphQL().Get().
		WithClassName("Collection").
		WithFields(fields...).
		WithWhere(where).
		Do(ctx)
	if err != nil {
		return err
	}
	if len(result.Errors) > 0 {
		return fmt.Errorf("%v", result.Errors[0])
	}

	collection := result.Data["Get"].(map[string]interface{})["Collection"].([]interface{})
	if len(result.Errors) > 0 {
		return fmt.Errorf("no data returned for collection 'Collection'")
	}
	obj := collection[0].(map[string]interface{})
	actualVersion := obj["version"].(string)
	if version != actualVersion {
		return fmt.Errorf("root obj: wanted %s got %s", version, actualVersion)
	}

	ref := obj["ref_prop"].([]interface{})
	if len(ref) <= 0 {
		return fmt.Errorf("no ref found for 'ref_prop'")
	}
	refVersion := ref[0].(map[string]interface{})["version"].(string)
	if refVersion != actualVersion {
		return fmt.Errorf("ref object: wanted %s got %s", version, actualVersion)
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

		collection := result.Data["Get"].(map[string]interface{})["Collection"].([]interface{})
		if len(collection) <= 0 {
			return fmt.Errorf("no data returned for collection 'Collection'")
		}
		actualVersion := collection[0].(map[string]interface{})["version"].(string)
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

	// There existed a bug in relation to snapshot restore on upgrade/restore where if a class was added, deleted and
	// re-added with might lose data. This is an additional step to trigger that case
	err = client.Schema().ClassDeleter().WithClassName(refTarget.Class).Do(context.Background())
	if err != nil {
		return err
	}

	err = client.Schema().ClassCreator().WithClass(refTarget).Do(context.Background())
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
			{
				DataType: []string{"int"},
				Name:     "major_version",
			},
			{
				DataType: []string{"int"},
				Name:     "minor_version",
			},
			{
				DataType: []string{"int"},
				Name:     "patch_version",
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
	var major, minor, patch int64
	semver, ok := maybeParseSingleSemverWithoutLeadingVForImport(version)
	if ok {
		major, minor, patch = semver.major(), semver.minor(), semver.patch()
	}
	props := map[string]interface{}{
		"version":       version,
		"object_count":  objectsCreated,
		"ref_prop":      []interface{}{map[string]interface{}{"beacon": fmt.Sprintf("weaviate://localhost/RefTarget/%s", targetID)}},
		"major_version": major,
		"minor_version": minor,
		"patch_version": patch,
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

	return c.rollingUpdate(ctx, version)
}
