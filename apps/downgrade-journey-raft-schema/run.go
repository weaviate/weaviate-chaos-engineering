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
	"github.com/weaviate/weaviate-go-client/v5/weaviate"
	"github.com/weaviate/weaviate/entities/models"
)

var (
	versions       []string
	objectsCreated = 0
)

func main() {
	ctx := context.Background()
	startVersion, ok := os.LookupEnv("WEAVIATE_VERSION")
	if !ok {
		log.Fatal("missing WEAVIATE_VERSION")
	}

	minimumW, ok := os.LookupEnv("MINIMUM_WEAVIATE_VERSION")
	if !ok {
		minimumW = "v1.25.0"
		log.Printf("MINIMUM_WEAVIATE_VERSION not set, setting: %s", minimumW)
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

	max, err := getTargetVersion(ctx, startVersion)
	if err != nil {
		log.Fatal(fmt.Sprintf("get target version for %s: %v", startVersion, err))
	}
	allVersions, err := buildVersionList(ctx, minimumW, max)
	if err != nil {
		log.Fatal(fmt.Sprintf("build version list: %v", err))
	}
	highestLast2MinorVersions, err := getHighestLast2MinorVersions(allVersions, max)
	if err != nil {
		log.Fatal(fmt.Sprintf("get highest last 3 minor versions: %v", err))
	}
	versions = []string{startVersion}
	versions = append(versions, highestLast2MinorVersions...)

	log.Printf("configured minimum version is %s", minimumW)
	log.Printf("configured starting version is %s", startVersion)
	log.Printf("number of nodes is %d", numNodes)
	log.Printf("identified the following last 3 minor versions: %v", versions)

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
				log.Fatal(fmt.Sprintf("can't get container %s logs, err: %v", name, logErr))
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
			return c, fmt.Errorf("start or upgrade: %w", err)
		}

		if i == 0 {
			// create schema
			if err := createSchema(ctx, client); err != nil {
				return c, fmt.Errorf("create schema: %w", err)
			}
			log.Println("sleeping for 10s")
			time.Sleep(10 * time.Second)
			// add more tenants
			log.Println("adding 50 more tenants")
			err := backoff.Retry(
				func() error { return createTenants(ctx, client, "MTCollection", "newtenant") },
				backoff.WithMaxRetries(backoff.NewConstantBackOff(1*time.Second), 3),
			)
			if err != nil {
				return c, fmt.Errorf("create tenants: %s", err.Error())
			}
			log.Println("successfully added 50 more tenants")
		}

		log.Println("sleeping for 10s")
		time.Sleep(10 * time.Second)

		log.Println("verify schema")
		err := backoff.Retry(
			func() error { return verify(ctx, client, i) },
			backoff.WithMaxRetries(backoff.NewConstantBackOff(1*time.Second), 15),
		)
		if err != nil {
			log.Fatal(fmt.Sprintf("downgrade to %s version failed with error: %v", version, err))
		}
		log.Println("successfully verified")
	}

	return c, nil
}

func verify(ctx context.Context, client *weaviate.Client, i int) error {
	for _, className := range []string{"RefTarget", "Collection", "MTCollection"} {
		if err := checkClassExistence(ctx, client, className); err != nil {
			return fmt.Errorf("verify %s: %w", className, err)
		}
	}
	return nil
}

func checkClassExistence(ctx context.Context, client *weaviate.Client, className string) error {
	exists, err := client.Schema().ClassExistenceChecker().WithClassName(className).Do(ctx)
	if err != nil {
		return err
	}
	if !exists {
		return fmt.Errorf("class %s doesn't exist", className)
	}
	log.Printf("verify: class %s exists\n", className)
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

	err := client.Schema().ClassCreator().WithClass(refTarget).Do(ctx)
	if err != nil {
		return err
	}

	// There existed a bug in relation to snapshot restore on upgrade/restore where if a class was added, deleted and
	// re-added with might lose data. This is an additional step to trigger that case
	err = client.Schema().ClassDeleter().WithClassName(refTarget.Class).Do(ctx)
	if err != nil {
		return err
	}

	err = client.Schema().ClassCreator().WithClass(refTarget).Do(ctx)
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

	err = client.Schema().ClassCreator().WithClass(classObj).Do(ctx)
	if err != nil {
		return err
	}

	mtClassName := "MTCollection"
	mtClassObj := &models.Class{
		Class: mtClassName,
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
		MultiTenancyConfig: &models.MultiTenancyConfig{
			Enabled:              true,
			AutoTenantActivation: true,
		},
	}

	err = client.Schema().ClassCreator().WithClass(mtClassObj).Do(ctx)
	if err != nil {
		return err
	}

	numberOfTenants := 50
	tenants := make([]models.Tenant, numberOfTenants)
	for i := range numberOfTenants {
		tenants[i] = models.Tenant{
			Name:           fmt.Sprintf("tenant_%v", i),
			ActivityStatus: models.TenantActivityStatusACTIVE,
		}
	}

	err = createTenants(ctx, client, mtClassName, "tenant")
	if err != nil {
		return err
	}

	return nil
}

func createTenants(ctx context.Context, client *weaviate.Client, className, tenantPrefix string) error {
	numberOfTenants := 50
	tenants := make([]models.Tenant, numberOfTenants)
	for i := range numberOfTenants {
		tenants[i] = models.Tenant{
			Name:           fmt.Sprintf("%s_%v", tenantPrefix, i),
			ActivityStatus: models.TenantActivityStatusACTIVE,
		}
	}

	return client.Schema().TenantsCreator().
		WithClassName(className).WithTenants(tenants...).
		Do(ctx)
}

func startOrUpgrade(ctx context.Context, c *cluster, i int, version string) error {
	if i == 0 {
		return c.startAllNodes(ctx, version)
	}

	return c.rollingUpdate(ctx, version)
}
