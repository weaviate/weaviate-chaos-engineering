package main

import (
	"context"
	"encoding/json"
	"fmt"
	"io"
	"log"
	"net/http"
	"regexp"
	"sort"
	"time"

	"github.com/docker/go-connections/nat"
	hashicorpversion "github.com/hashicorp/go-version"
	"github.com/testcontainers/testcontainers-go"
	"github.com/testcontainers/testcontainers-go/wait"
	"github.com/weaviate/weaviate-go-client/v5/weaviate"
)

func buildVersionList(ctx context.Context, min, target string) ([]string, error) {
	ghReleases, err := retrieveVersionListFromGH()
	if err != nil {
		return nil, err
	}

	versions := parseSemverList(ghReleases)
	versions = sortSemverAndTrimToMinimum(versions, min, target)
	list := versions.toStringList()

	return list, nil
}

func retrieveVersionListFromGH() ([]string, error) {
	// ignore pagination, for now we assume that the first page contains enough
	// versions. This might require changing in the future and we might have to
	// page through the API to get all desired version
	res, err := http.Get("https://api.github.com/repos/weaviate/weaviate/releases?per_page=100")
	if err != nil {
		return nil, err
	}

	defer res.Body.Close()

	type githubRelease struct {
		TagName string `json:"tag_name"`
	}

	resBytes, err := io.ReadAll(res.Body)
	if err != nil {
		return nil, err
	}

	var parsed []githubRelease
	if err := json.Unmarshal(resBytes, &parsed); err != nil {
		return nil, err
	}

	out := make([]string, len(parsed))
	for i := range parsed {
		out[i] = parsed[i].TagName
	}

	return out, nil
}

func getHighestLast2MinorVersions(versions []string, maxVersion string) ([]string, error) {
	// Parse versions
	parsedVersions := make([]*hashicorpversion.Version, 0, len(versions))
	for _, v := range versions {
		parsed, err := hashicorpversion.NewVersion(v)
		if err != nil {
			return nil, fmt.Errorf("failed to parse version %s: %w", v, err)
		}
		parsedVersions = append(parsedVersions, parsed)
	}
	parsedMaxVersion, err := hashicorpversion.NewVersion(maxVersion)
	if err != nil {
		return nil, fmt.Errorf("failed to parse version %s: %w", maxVersion, err)
	}
	parsedMaxMinor := parsedMaxVersion.Segments()[1]

	// Group by major version
	minorVersions := make(map[int]*hashicorpversion.Version)
	for _, v := range parsedVersions {
		minor := v.Segments()[1]
		if minor < parsedMaxMinor {
			if current, exists := minorVersions[minor]; !exists || v.GreaterThan(current) {
				minorVersions[minor] = v
			}
		}
	}

	// Collect highest versions
	result := make([]string, 0, len(minorVersions))
	for _, v := range minorVersions {
		result = append(result, v.Original())
	}

	// Sort in descending order
	sort.Slice(result, func(i, j int) bool {
		v1, _ := hashicorpversion.NewVersion(result[i])
		v2, _ := hashicorpversion.NewVersion(result[j])
		return v1.GreaterThan(v2)
	})

	return result[:2], nil
}

// trim anything that's not a vx.y.z release, such as pre- and rc-releases or
// other malformed tags
func parseSemverList(input []string) semverList {
	r := regexp.MustCompile(`^v([0-9]+)\.([0-9]+)\.([0-9]+)$`)

	out := make(semverList, len(input))
	i := 0
	for _, version := range input {
		if !r.MatchString(version) {
			continue
		}

		ver, err := hashicorpversion.NewSemver(version)
		if err != nil {
			panic(fmt.Errorf("cannot parse version %q: %w", version, err))
		}

		out[i] = semver{
			version: ver,
		}
		i++
	}

	return out[:i]
}

type semver struct {
	version *hashicorpversion.Version
}

type semverList []semver

func (self semver) largerOrEqual(other semver) bool {
	return self.version.GreaterThanOrEqual(other.version)
}

func (self semver) major() int64 {
	return self.version.Segments64()[0]
}

func (self semver) minor() int64 {
	return self.version.Segments64()[1]
}

func (self semver) patch() int64 {
	return self.version.Segments64()[2]
}

func sortSemverAndTrimToMinimum(versions semverList, min, max string) semverList {
	sort.Slice(versions, func(a, b int) bool {
		return versions[a].version.LessThan(versions[b].version)
	})

	minV := parseSingleSemverWithoutLeadingV(min)
	maxV := parseSingleSemverWithoutLeadingV(max)

	out := make(semverList, len(versions))

	i := 0
	for _, version := range versions {
		if !version.largerOrEqual(minV) {
			continue
		}
		if version.largerOrEqual(maxV) {
			break
		}

		out[i] = version
		i++
	}

	return out[:i]
}

func parseSingleSemverWithoutLeadingV(input string) semver {
	v, ok := maybeParseSingleSemverWithoutLeadingV(input)
	if !ok {
		panic("not an acceptable semver")
	}

	return v
}

func maybeParseSingleSemverWithoutLeadingV(input string) (semver, bool) {
	ver, err := hashicorpversion.NewSemver(input)
	if err != nil {
		return semver{version: nil}, false
	}

	return semver{
		version: ver,
	}, true
}

func (s semverList) toStringList() []string {
	out := make([]string, len(s))
	for i, ver := range s {
		out[i] = ver.version.String()
	}
	return out
}

// Spawn a Weaviate container to get the version as it is not possible to infer the version from the container image
func getTargetVersion(ctx context.Context, version string) (string, error) {
	weaviateImage := fmt.Sprintf("semitechnologies/weaviate:%s", version)
	env := map[string]string{
		"AUTHENTICATION_ANONYMOUS_ACCESS_ENABLED": "true",
		"LOG_LEVEL":                 "debug",
		"QUERY_DEFAULTS_LIMIT":      "20",
		"PERSISTENCE_DATA_PATH":     "./data",
		"DEFAULT_VECTORIZER_MODULE": "none",
		"CLUSTER_HOSTNAME":          "weaviate-test",
		"RAFT_JOIN":                 "weaviate-test:8300",
		"RAFT_BOOTSTRAP_EXPECT":     "1",
	}
	req := testcontainers.ContainerRequest{
		Image:        weaviateImage,
		ExposedPorts: []string{"8080/tcp"},
		Env:          env,
		WaitingFor: wait.
			ForHTTP("/v1/.well-known/ready").
			WithPort(nat.Port("8080")).
			WithStatusCodeMatcher(func(status int) bool {
				return status >= 200 && status <= 299
			}).
			WithStartupTimeout(30 * time.Second),
	}
	c, err := testcontainers.GenericContainer(ctx, testcontainers.GenericContainerRequest{
		ContainerRequest: req,
		Started:          true,
	})
	if err != nil {
		return "", err
	}
	defer func() {
		err := c.Terminate(ctx)
		if err != nil {
			log.Fatal(fmt.Errorf("cannot terminate Weaviate container that gets target version: %w", err))
		}
	}()
	httpUri, err := c.PortEndpoint(ctx, nat.Port("8080/tcp"), "")
	if err != nil {
		return "", err
	}

	cfg := weaviate.Config{
		Host:   httpUri,
		Scheme: "http",
	}
	client := weaviate.New(cfg)

	meta, err := client.Misc().MetaGetter().Do(ctx)
	if err != nil {
		return "", err
	}
	return meta.Version, nil
}
