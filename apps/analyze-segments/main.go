package main

import (
	"encoding/binary"
	"errors"
	"flag"
	"fmt"
	"io"
	"io/fs"
	"log"
	"os"
	"path/filepath"
	"regexp"
)

// Matches segment-<ids>[.l<level>.s<strategy>].db: flushes use one ID, compaction joins IDs with "_", and the suffix can appear on both.
var segmentRegex = regexp.MustCompile(`^segment-[0-9]+(_[0-9]+)*(\.l[0-9]+\.s[0-9]+)?\.db$`)

func main() {
	var segmentsPath string
	flag.StringVar(&segmentsPath, "path", "", "path to lsm bucket")
	flag.Parse()

	if err := analyzeSegments(segmentsPath, os.Stdout); err != nil {
		log.Fatal(err)
	}
}

func analyzeSegments(segmentsPath string, out io.Writer) error {
	files, err := os.ReadDir(segmentsPath)
	if err != nil {
		return err
	}

	fmt.Fprintf(out, "path: %s\n\n", segmentsPath)
	fmt.Fprintf(out, "%-64s%16s%16s\n", "SEGMENT", "SIZE", "LEVEL")

	for _, entry := range files {
		if !segmentRegex.MatchString(entry.Name()) {
			continue
		}

		// The directory is scanned while Weaviate runs; compaction may remove an entry between ReadDir and the calls below.
		info, err := entry.Info()
		if errors.Is(err, fs.ErrNotExist) {
			continue
		}
		if err != nil {
			return err
		}

		level, err := readLevel(filepath.Join(segmentsPath, entry.Name()))
		if errors.Is(err, fs.ErrNotExist) {
			continue
		}
		if err != nil {
			return err
		}

		fmt.Fprintf(out, "%-64s%16d%16d\n", entry.Name(), info.Size(), level)
	}
	return nil
}

func readLevel(path string) (uint16, error) {
	f, err := os.Open(path)
	if err != nil {
		return 0, err
	}
	defer f.Close()

	buffer := make([]byte, 2)
	if _, err := io.ReadFull(f, buffer); err != nil {
		return 0, fmt.Errorf("reading level of segment %q: %w", path, err)
	}
	return binary.LittleEndian.Uint16(buffer), nil
}
