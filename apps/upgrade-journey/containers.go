package main

import (
	"context"
	"fmt"
	"io"
	"log"
	"os"
	"path"
	"strings"
	"time"

	"github.com/docker/go-connections/nat"
	"github.com/testcontainers/testcontainers-go"
	tescontainersnetwork "github.com/testcontainers/testcontainers-go/network"
	"github.com/testcontainers/testcontainers-go/wait"
)

var counter int

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
		nodeCount:  nodeCount,
		rootDir:    rootDir,
		containers: make([]testcontainers.Container, nodeCount),
	}
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
	log.Printf("starting rolling update to %s", version)
	for i := 0; i < c.nodeCount; i++ {
		if err := c.containers[i].Terminate(ctx); err != nil {
			return err
		}

		container, err := c.startWeaviateNode(ctx, i, version)
		if err != nil {
			log.Print(err)
			if container != nil {
				logReader, logErr := container.Logs(context.Background())
				if logErr != nil {
					log.Fatal(logErr)
				}

				io.Copy(os.Stdout, logReader)
			}
			return err
		}

		c.containers[i] = container
	}

	log.Printf("completed rolling update to %s", version)
	return nil
}

func (c *cluster) startNetwork(ctx context.Context) error {
	network, err := tescontainersnetwork.New(
		ctx,
		tescontainersnetwork.WithAttachable(),
	)
	if err != nil {
		return fmt.Errorf("network %s: %w", network.Name, err)
	}
	c.networkName = network.Name
	return nil
}

func (c *cluster) volumePath(nodeId int) string {
	return path.Join(c.rootDir, "data/", c.hostname(nodeId))
}

func (c *cluster) startWeaviateNode(ctx context.Context, nodeId int, version string) (testcontainers.Container, error) {
	if err := os.MkdirAll(c.volumePath(nodeId), 0o777); err != nil {
		log.Print(err)
		return nil, err
	}

	image := fmt.Sprintf("semitechnologies/weaviate:%s", version)
	container, err := testcontainers.GenericContainer(ctx, testcontainers.GenericContainerRequest{
		Logger: log.Default(),
		ContainerRequest: testcontainers.ContainerRequest{
			Name:         fmt.Sprintf("%s-%d", c.hostname(nodeId), counter),
			Hostname:     c.hostname(nodeId),
			Image:        image,
			Cmd:          []string{"--host", "0.0.0.0", "--port", "8080", "--scheme", "http"},
			Networks:     []string{c.networkName},
			ExposedPorts: []string{fmt.Sprintf("%d:8080", 8080+nodeId)},
			AutoRemove:   false,
			Env: map[string]string{
				"QUERY_DEFAULTS_LIMIT":                    "25",
				"AUTHENTICATION_ANONYMOUS_ACCESS_ENABLED": "true",
				"PERSISTENCE_DATA_PATH":                   "/var/lib/weaviate",
				"DEFAULT_VECTORIZER_MODULE":               "none",
				"ENABLE_MODULES":                          "",
				"CLUSTER_GOSSIP_BIND_PORT":                "7100",
				"CLUSTER_DATA_BIND_PORT":                  "7101",
				"CLUSTER_HOSTNAME":                        c.hostname(nodeId),
				"CLUSTER_JOIN":                            fmt.Sprintf("%s:7100", c.hostname(0)),
				"RAFT_JOIN":                               fmt.Sprintf("%s,%s,%s", c.hostname(0), c.hostname(1), c.hostname(2)),
				"RAFT_BOOTSTRAP_EXPECT":                   "1",
				"DISABLE_TELEMETRY":                       "true",
				"PERSISTENCE_LSM_ACCESS_STRATEGY":         os.Getenv("PERSISTENCE_LSM_ACCESS_STRATEGY"),
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
				WithStartupTimeout(c.getStartupTimeout()),
		},
		Started: true,
	})
	counter++
	if err != nil {
		return container, err
	}

	return container, nil
}

func (c *cluster) hostname(nodeId int) string {
	return fmt.Sprintf("weaviate-%d", nodeId)
}

func (c *cluster) allNodes() string {
	hosts := []string{}
	for i := 0; i < c.nodeCount; i++ {
		hosts = append(hosts, fmt.Sprintf("weaviate-%d:7100", i))
	}

	return strings.Join(hosts, ",")
}

func (c *cluster) getStartupTimeout() time.Duration {
	// Base timeout of 30 seconds for single node
	baseTimeout := 30 * time.Second

	// For multi-node clusters, increase timeout based on node count
	// Each additional node adds 30 seconds to account for cluster joining time
	if c.nodeCount > 1 {
		return baseTimeout + time.Duration(c.nodeCount)*30*time.Second
	}

	return baseTimeout
}
