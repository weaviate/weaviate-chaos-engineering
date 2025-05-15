package main

import (
	"reflect"
	"testing"

	"github.com/stretchr/testify/assert"
	"github.com/stretchr/testify/require"
)

var githubReleases = []string{
	"v1.23.0", "v1.22.8", "v1.23.0-rc.1", "v1.22.7", "v1.23.0-rc.0", "v1.22.6", "v1.22.5", "v1.22.4", "v1.22.3",
	"v1.22.2", "v1.21.9", "v1.22.1", "v1.22.0", "v1.21.8", "v1.22.0-rc.0", "v1.21.7", "v1.21.6", "v1.21.5",
	"v1.21.4", "v1.21.3", "v1.21.2", "v1.21.1", "v1.20.6", "v1.19.13", "v1.18.6", "v1.21.0", "v1.21.0-rc.1",
	"v1.21.0-rc.0", "v1.20.5", "v1.20.4", "v1.20.3", "v1.20.2", "v1.20.1", "v1.20.0", "v1.19.12", "v1.19.11",
	"v1.19.10", "v1.19.9", "v1.19.8", "v1.19.7", "v1.19.6", "v1.19.5", "v1.19.4", "v1.18.5", "v1.19.3", "v1.19.2",
	"v1.19.1", "v1.19.0", "v1.18.4", "v1.19.0-beta.1", "v1.19.0-beta.0", "v1.18.3", "v1.18.2", "v1.18.1",
	"v1.18.0", "v1.17.6", "v1.18.0-rc.0", "v1.17.5", "v1.17.4", "v1.17.3", "v1.17.2", "v1.17.1", "v1.17.0",
	"v1.16.9", "v1.16.8", "v1.16.7", "v1.16.6", "v1.16.5", "v1.16.4", "v1.16.3", "v1.16.2", "v1.16.1", "v1.16.0",
	"v1.15.5", "v1.15.4", "v1.15.3", "v1.15.2", "v1.15.1", "v1.15.0", "v1.15.0-alpha1", "v1.14.1", "v1.14.0", "v1.13.2",
	"v1.13.1", "v1.13.0", "v1.12.2", "v1.12.1", "v1.12.0", "v1.11.0", "v1.10.1", "v1.10.0", "v1.9.1", "v1.9.1-rc.0",
	"v1.9.0", "v1.8.0", "v1.8.0-rc.3", "v1.8.0-rc.2", "v1.8.0-rc.1", "v1.8.0-rc.0", "v1.7.2",
}

func Test_sortSemverAndTrimToMinimum(t *testing.T) {
	versions := parseSemverList(githubReleases)
	tests := []struct {
		name     string
		versions semverList
		min      string
		max      string
		want     []string
	}{
		{
			name:     "from 1.22.7 to 1.22.9",
			versions: versions,
			min:      "1.22.7",
			max:      "1.22.9",
			want:     []string{"1.22.7", "1.22.8"},
		},
		{
			name:     "from 1.20.0 to 1.22.9",
			versions: versions,
			min:      "1.20.0",
			max:      "1.22.9",
			want: []string{
				"1.20.0", "1.20.1", "1.20.2", "1.20.3", "1.20.4", "1.20.5", "1.20.6", "1.21.0", "1.21.1", "1.21.2", "1.21.3", "1.21.4", "1.21.5",
				"1.21.6", "1.21.7", "1.21.8", "1.21.9", "1.22.0", "1.22.1", "1.22.2", "1.22.3", "1.22.4", "1.22.5", "1.22.6", "1.22.7", "1.22.8",
			},
		},
	}
	for _, tt := range tests {
		t.Run(tt.name, func(t *testing.T) {
			if got := sortSemverAndTrimToMinimum(tt.versions, tt.min, tt.max); !reflect.DeepEqual(got.toStringList(), tt.want) {
				t.Errorf("sortSemverAndTrimToMinimum() = %v, want %v", got.toStringList(), tt.want)
			}
		})
	}
}

func Test_getHighestMajorVersions(t *testing.T) {
	last3HighestMajorVersions, err := getHighestLast2MinorVersions(githubReleases, "v1.23.0")
	assert.NoError(t, err)
	require.Len(t, last3HighestMajorVersions, 2)
	assert.Equal(t, "v1.22.8", last3HighestMajorVersions[0])
	assert.Equal(t, "v1.21.9", last3HighestMajorVersions[1])
}
