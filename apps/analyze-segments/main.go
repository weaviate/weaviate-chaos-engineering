package main

import (
	"encoding/binary"
	"flag"
	"fmt"
	"io"
	"log"
	"os"
	"path/filepath"
	"regexp"
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
