package main

import (
	"encoding/json"
	"fmt"
	"io"
	"net/http"
	"regexp"
	"sort"
	"strconv"
)

func buildVersionList(min, target string) ([]string, error) {
	ghReleases, err := retrieveVersionListFromGH()
	if err != nil {
		return nil, err
	}

	versions := parseSemverList(ghReleases)
	versions = sortSemverAndTrimToMinimum(versions, min)
	list := versions.toStringList()

	return append(list, target), nil
}

func retrieveVersionListFromGH() ([]string, error) {
	// ignore pagination, for now we assume that the first page contains enough
	// versions. This might require changing in the future and we might have to
	// page through the API to get all desired version
	res, err := http.Get("https://api.github.com/repos/weaviate/weaviate/releases")
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

		sm := r.FindStringSubmatch(version)

		out[i] = semver{
			major: mustParseInt(sm[1]),
			minor: mustParseInt(sm[2]),
			patch: mustParseInt(sm[3]),
		}
		i++

	}

	return out[:i]
}

type semver struct {
	major int
	minor int
	patch int
}

type semverList []semver

func (self semver) largerOrEqual(other semver) bool {
	if self.major < other.major {
		return false
	}

	if self.minor < other.minor {
		return false
	}

	return self.patch >= other.patch
}

func mustParseInt(in string) int {
	res, err := strconv.Atoi(in)
	if err != nil {
		panic(err)
	}
	return res
}

func sortSemverAndTrimToMinimum(versions semverList, min string) semverList {
	sort.Slice(versions, func(a, b int) bool {
		if versions[a].major != versions[b].major {
			return versions[a].major < versions[b].major
		}
		if versions[a].minor != versions[b].minor {
			return versions[a].minor < versions[b].minor
		}
		return versions[a].patch < versions[b].patch
	})

	minV := parseSingleSemverWithoutLeadingV(min)

	out := make(semverList, len(versions))

	i := 0
	for _, version := range versions {
		if !version.largerOrEqual(minV) {
			continue
		}

		out[i] = version
		i++
	}

	return out[:i]
}

func parseSingleSemverWithoutLeadingV(input string) semver {
	r := regexp.MustCompile(`^([0-9]+)\.([0-9]+)\.([0-9]+)$`)
	if !r.MatchString(input) {
		panic("not an acceptable semver")
	}

	sm := r.FindStringSubmatch(input)

	return semver{
		major: mustParseInt(sm[1]),
		minor: mustParseInt(sm[2]),
		patch: mustParseInt(sm[3]),
	}
}

func (s semverList) toStringList() []string {
	out := make([]string, len(s))
	for i, ver := range s {
		out[i] = fmt.Sprintf("%d.%d.%d", ver.major, ver.minor, ver.patch)
	}
	return out
}
