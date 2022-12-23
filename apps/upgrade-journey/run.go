package main

import (
	"context"
	"fmt"
	"log"
	"math/rand"
	"os"
	"path"
	"strings"
	"time"

	"github.com/docker/go-connections/nat"
	"github.com/testcontainers/testcontainers-go"
	"github.com/testcontainers/testcontainers-go/wait"
)

type cluster struct {
	nodeCount   int
	networkName string
	rootDir     string
	containers  []testcontainers.Container
}

func newCluster(nodeCount int) *cluster {
	rootDir, err := os.Getwd()
	if err != nil {
		log.Fatal(err)
	}

	return &cluster{
		nodeCount:   nodeCount,
		networkName: fmt.Sprintf("weaviate-upgrade-journey-%d", rand.Int()),
		rootDir:     rootDir,
		containers:  make([]testcontainers.Container, nodeCount),
	}
}

func main() {
	rand.Seed(time.Now().UnixNano())
	ctx := context.Background()

	c := newCluster(3)

	if err := c.startNetwork(ctx); err != nil {
		log.Fatal(err)
	}

	if err := c.startAllNodes(ctx, "1.16.0"); err != nil {
		log.Fatal(err)
	}

	if err := c.rollingUpdate(ctx, "1.17.0"); err != nil {
		log.Fatal(err)
	}

	time.Sleep(120 * time.Second)
}

func (c *cluster) startAllNodes(ctx context.Context, version string) error {
	for i := 0; i < c.nodeCount; i++ {
		container, err := c.startWeaviateNode(ctx, i, version)
		if err != nil {
			return err
		}

		c.containers[i] = container
	}

	return nil
}

func (c *cluster) rollingUpdate(ctx context.Context, version string) error {
	for i := 0; i < c.nodeCount; i++ {
		if err := c.containers[i].Stop(ctx, nil); err != nil {
			return err
		}

		container, err := c.startWeaviateNode(ctx, i, version)
		if err != nil {
			return err
		}

		c.containers[i] = container
	}

	return nil
}

func (c *cluster) startNetwork(ctx context.Context) error {
	_, err := testcontainers.GenericNetwork(ctx, testcontainers.GenericNetworkRequest{
		NetworkRequest: testcontainers.NetworkRequest{
			Name:     c.networkName,
			Internal: false,
		},
	})
	if err != nil {
		return fmt.Errorf("network %s: %w", c.networkName, err)
	}

	return nil
}

func (c *cluster) volumePath(nodeId int) string {
	return path.Join(c.rootDir, "data/", c.hostname(nodeId))
}

func (c *cluster) startWeaviateNode(ctx context.Context, nodeId int, version string) (testcontainers.Container, error) {
	if err := os.MkdirAll(c.volumePath(nodeId), 0o777); err != nil {
		return nil, err
	}

	image := fmt.Sprintf("semitechnologies/weaviate:%s", version)
	container, err := testcontainers.GenericContainer(ctx, testcontainers.GenericContainerRequest{
		ContainerRequest: testcontainers.ContainerRequest{
			Name:         c.hostname(nodeId),
			Image:        image,
			Cmd:          []string{"--host", "0.0.0.0", "--port", "8080", "--scheme", "http"},
			Networks:     []string{c.networkName},
			ExposedPorts: []string{fmt.Sprintf("%d:8080", 8080+nodeId)},
			AutoRemove:   true,
			Env: map[string]string{
				"QUERY_DEFAULTS_LIMIT":                    "25",
				"AUTHENTICATION_ANONYMOUS_ACCESS_ENABLED": "true",
				"PERSISTENCE_DATA_PATH":                   "/var/lib/weaviate",
				"DEFAULT_VECTORIZER_MODULE":               "none",
				"ENABLE_MODULES":                          "",
				"CLUSTER_GOSSIP_BIND_PORT":                "7100",
				"CLUSTER_DATA_BIND_PORT":                  "7101",
				"CLUSTER_HOSTNAME":                        c.hostname(nodeId),
				"CLUSTER_JOIN":                            c.otherNodes(nodeId),
			},
			Mounts: testcontainers.Mounts(testcontainers.BindMount(
				c.volumePath(nodeId), "/var/lib/weaviate",
			)),
			WaitingFor: wait.
				ForHTTP("/v1/.well-known/ready").
				WithPort(nat.Port("8080")).
				WithStatusCodeMatcher(func(status int) bool {
					return status >= 200 && status <= 299
				}).
				WithStartupTimeout(240 * time.Second),
		},
		Started: true,
	})
	if err != nil {
		return nil, err
	}

	return container, nil
}

func (c *cluster) hostname(nodeId int) string {
	return fmt.Sprintf("weaviate-%d", nodeId)
}

func (c *cluster) otherNodes(nodeId int) string {
	hosts := []string{}
	for i := 0; i < c.nodeCount; i++ {
		if i == nodeId {
			continue
		}

		hosts = append(hosts, fmt.Sprintf("weaviate-%d:7100", i))
	}

	return strings.Join(hosts, ",")
}
