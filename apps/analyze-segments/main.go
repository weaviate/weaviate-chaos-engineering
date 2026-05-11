package main

import (
	"encoding/binary"
	"errors"
	"flag"
	"fmt"
	"io"
	"log"
	"os"
	"path/filepath"
	"regexp"
	"strings"
)

func main() {
	var segmentsPath string
	flag.StringVar(&segmentsPath, "path", "", "path to lsm bucket")
	flag.Parse()

	_, err := os.Stat(segmentsPath)
	if err != nil {
		log.Fatal(err)
	}

	files, err := os.ReadDir(segmentsPath)
	if err != nil {
		log.Fatal(err)
	}

	fmt.Printf("path: %s\n\n", segmentsPath)
	fmt.Printf("SEGMENT                         ")
	fmt.Printf("            SIZE")
	fmt.Printf("           LEVEL")
	fmt.Println()

	buffer := make([]byte, 2)
	segmentRegex := regexp.MustCompile("^segment-[0-9]{18,}.db$")
	for i := range files {
		info, err := files[i].Info()
		if err != nil {
			log.Fatal(err)
		}

		if segmentRegex.MatchString(info.Name()) {
			func() {
				f, err := os.OpenFile(filepath.Join(segmentsPath, info.Name()), os.O_RDONLY, os.ModePerm)
				if err != nil {
					// A segment can disappear between os.ReadDir and os.OpenFile when an LSM
					// compaction completes in that window. Weaviate writes the merged result
					// (segment-{leftID}_{rightID}.db) before deleting the inputs, so the data
					// is never lost. We confirm this specific TOCTOU case by checking that
					// (a) the file is truly gone and (b) a derived segment carrying this
					// segment's ID exists. Any other open error is still fatal.
					if errors.Is(err, os.ErrNotExist) && wasCompacted(segmentsPath, info.Name()) {
						log.Printf("skipping %s: removed by compaction during analysis\n", info.Name())
						return
					}
					log.Fatal(err)
				}
				defer f.Close()

				n, err := io.ReadFull(f, buffer)
				if err != nil {
					log.Fatal(err)
				}
				if n != 2 {
					log.Fatalf("failed reading level of segment %q", info.Name())
				}
				fmt.Printf("%-32s%16d%16d\n", info.Name(), info.Size(), binary.LittleEndian.Uint16(buffer))
			}()
		}
	}
}

// wasCompacted confirms that a segment that could not be opened was removed by an LSM
// compaction rather than by an unexpected cause. Two conditions must hold:
//
//  1. The file is truly absent (os.ErrNotExist on re-stat), ruling out a transient
//     open error.
//
//  2. A segment derived from this one now exists in the same directory. Weaviate's
//     compactor merges two adjacent segments and names the output:
//       segment-{leftID}_{rightID}[.l{level}.s{strategy}].db
//     A cleanup pass may also produce segment-{id}.l{level}.s{strategy}.db for the
//     same base ID. In both cases the missing segment's ID appears as a name
//     component. Finding such a file confirms the data is present in a newer segment,
//     not silently lost.
func wasCompacted(dir, missingName string) bool {
	// (1) Confirm the file is actually gone.
	if _, err := os.Stat(filepath.Join(dir, missingName)); !errors.Is(err, os.ErrNotExist) {
		return false
	}

	// Extract the segment ID: everything between "segment-" and the first ".".
	// e.g. "segment-1771258130098421000.db" → "1771258130098421000"
	id, _, _ := strings.Cut(strings.TrimPrefix(missingName, "segment-"), ".")
	if id == "" {
		return false
	}

	// (2) Look for a file that carries this segment's ID as:
	//   - left half of a merge:    segment-{id}_...
	//   - same-ID cleanup rewrite: segment-{id}....  (different suffix, e.g. .l0.s5.db)
	//   - right half of a merge:   segment-{otherID}_{id}.  (id followed by ".")
	entries, err := os.ReadDir(dir)
	if err != nil {
		log.Printf("wasCompacted: readdir %s: %v\n", dir, err)
		return false
	}
	leftOrCleanup := "segment-" + id
	rightSuffix := "_" + id + "."
	for _, e := range entries {
		n := e.Name()
		if n == missingName {
			continue
		}
		if strings.HasPrefix(n, leftOrCleanup+"_") ||
			strings.HasPrefix(n, leftOrCleanup+".") ||
			(strings.HasPrefix(n, "segment-") && strings.Contains(n, rightSuffix)) {
			return true
		}
	}
	return false
}
