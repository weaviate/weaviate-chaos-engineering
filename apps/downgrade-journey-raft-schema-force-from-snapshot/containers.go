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
	hashicorpversion "github.com/hashicorp/go-version"
	"github.com/testcontainers/testcontainers-go"
	tescontainersnetwork "github.com/testcontainers/testcontainers-go/network"
	"github.com/testcontainers/testcontainers-go/wait"
)

var counter int

const (
	telemetrySinkHost = "telemetry-sink"
	telemetryURL      = "http://" + telemetrySinkHost + ":8080/weaviate-telemetry"

	// TELEMETRY_URL landed in this version; older nodes cannot be redirected
	telemetryMinMajor = 1
	telemetryMinMinor = 36
)

// telemetryFor keeps a node from reporting to the production endpoint: nodes new
// enough to honour TELEMETRY_URL push to the local sink, older ones (and any
// version that does not parse) run with telemetry off instead. Compared on
// major.minor, so a prerelease such as 1.36.0-rc.0 counts as 1.36 rather than
// sorting below it, matching apps/telemetry-sink/telemetry-config.sh.
func telemetryFor(version string) (disable, url string) {
	if v, err := hashicorpversion.NewVersion(version); err == nil {
		s := v.Segments()
		if s[0] > telemetryMinMajor || (s[0] == telemetryMinMajor && s[1] >= telemetryMinMinor) {
			return "", telemetryURL
		}
	}

	log.Printf("telemetry: disabled for %s, TELEMETRY_URL needs v%d.%d or newer",
		version, telemetryMinMajor, telemetryMinMinor)
	return "true", ""
}

type stdoutLogConsumer struct{}

func (lc *stdoutLogConsumer) Accept(l testcontainers.Log) {
	fmt.Print(string(l.Content))
}

type cluster struct {
	nodeCount     int
	networkName   string
	rootDir       string
	containers    []testcontainers.Container
	telemetrySink testcontainers.Container
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

// startTelemetrySink must run before any Weaviate container, which pushes an
// INIT payload as soon as it boots.
func (c *cluster) startTelemetrySink(ctx context.Context) error {
	container, err := testcontainers.GenericContainer(ctx, testcontainers.GenericContainerRequest{
		ContainerRequest: testcontainers.ContainerRequest{
			FromDockerfile: testcontainers.FromDockerfile{
				Context: path.Join(c.rootDir, "..", "telemetry-sink"),
			},
			Hostname:       telemetrySinkHost,
			Networks:       []string{c.networkName},
			NetworkAliases: map[string][]string{c.networkName: {telemetrySinkHost}},
			ExposedPorts:   []string{"8080/tcp"},
			WaitingFor: wait.
				ForHTTP("/").
				WithPort(nat.Port("8080")).
				WithStartupTimeout(2 * time.Minute),
		},
		Started: true,
	})
	if err != nil {
		return fmt.Errorf("start telemetry sink: %w", err)
	}

	c.telemetrySink = container
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

	containerLogger := stdoutLogConsumer{}
	image := fmt.Sprintf("semitechnologies/weaviate:%s", version)
	telemetryDisable, telemetryPush := telemetryFor(version)
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
				"CLUSTER_JOIN":                            c.allNodes(),
				"RAFT_JOIN":                               fmt.Sprintf("%s:8300", c.hostname(nodeId)),
				"RAFT_BOOTSTRAP_EXPECT":                   "1",
				"PERSISTENCE_LSM_ACCESS_STRATEGY":         os.Getenv("PERSISTENCE_LSM_ACCESS_STRATEGY"),
				"DISABLE_TELEMETRY":                       telemetryDisable,
				"TELEMETRY_URL":                           telemetryPush,
				// for raft snapshots
				"RAFT_SNAPSHOT_THRESHOLD": "1",
				"RAFT_SNAPSHOT_INTERVAL":  "1",
				"RAFT_TRAILING_LOGS":      "1",
			},
			Mounts: testcontainers.Mounts(testcontainers.BindMount(
				c.volumePath(nodeId), "/var/lib/weaviate",
			)),
			LogConsumerCfg: &testcontainers.LogConsumerConfig{
				Opts: []testcontainers.LogProductionOption{
					testcontainers.WithLogProductionTimeout(10 * time.Second),
				},
				Consumers: []testcontainers.LogConsumer{&containerLogger},
			},
			WaitingFor: wait.
				ForHTTP("/v1/.well-known/ready").
				WithPort(nat.Port("8080")).
				WithStatusCodeMatcher(func(status int) bool {
					return status >= 200 && status <= 299
				}).
				WithStartupTimeout(30 * time.Second),
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
