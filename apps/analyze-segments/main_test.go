package main

import (
	"encoding/binary"
	"os"
	"path/filepath"
	"strings"
	"testing"
)

func TestSegmentRegex(t *testing.T) {
	cases := []struct {
		name  string
		match bool
	}{
		{"segment-1757843642188211200.db", true},
		{"segment-1757843642188211200_1757843642188211300.db", true},
		{"segment-1757843642188211200_1757843642188211300.l2.s0.db", true},
		{"segment-1757843642188211200_1757843642188211300_1757843642188211400_1757843642188211500.l3.s1.db", true},
		{"segment-1757843642188211200.db.tmp", false},
		{"segment-1757843642188211200.db.deleteme", false},
		{"segment-1757843642188211200.wal", false},
		{"segment-1757843642188211200.bloom", false},
		{"segment-1757843642188211200.secondary.0.bloom", false},
		{"segment-1757843642188211200.cna", false},
		{"segment-1757843642188211200Xdb", false},
		{"segment-.db", false},
	}

	for _, c := range cases {
		t.Run(c.name, func(t *testing.T) {
			if got := segmentRegex.MatchString(c.name); got != c.match {
				t.Errorf("MatchString(%q) = %v, want %v", c.name, got, c.match)
			}
		})
	}
}

func TestAnalyzeSegments(t *testing.T) {
	dir := t.TempDir()

	writeSegment := func(name string, level uint16) {
		content := make([]byte, 16)
		binary.LittleEndian.PutUint16(content, level)
		if err := os.WriteFile(filepath.Join(dir, name), content, 0o644); err != nil {
			t.Fatal(err)
		}
	}

	writeSegment("segment-1757843642188211200.db", 0)
	writeSegment("segment-1757843642188211300_1757843642188211400.l2.s0.db", 2)
	writeSegment("segment-1757843642188211500.db.deleteme", 0)
	if err := os.WriteFile(filepath.Join(dir, "segment-1757843642188211200.wal"), nil, 0o644); err != nil {
		t.Fatal(err)
	}

	var out strings.Builder
	if err := analyzeSegments(dir, &out); err != nil {
		t.Fatal(err)
	}

	lines := strings.Split(strings.TrimRight(out.String(), "\n"), "\n")
	// The driver script skips the first 3 lines (path, blank, header) and reads columns 2 and 3.
	if len(lines) != 5 {
		t.Fatalf("expected 3 header lines + 2 segment rows, got %d lines:\n%s", len(lines), out.String())
	}

	rows := map[string]string{}
	for _, line := range lines[3:] {
		fields := strings.Fields(line)
		if len(fields) != 3 {
			t.Fatalf("expected 3 columns per row, got %q", line)
		}
		rows[fields[0]] = fields[2]
	}

	expected := map[string]string{
		"segment-1757843642188211200.db":                           "0",
		"segment-1757843642188211300_1757843642188211400.l2.s0.db": "2",
	}
	for name, level := range expected {
		if got, ok := rows[name]; !ok {
			t.Errorf("segment %q missing from output", name)
		} else if got != level {
			t.Errorf("segment %q level = %s, want %s", name, got, level)
		}
	}
}

func TestAnalyzeSegmentsEntryVanishes(t *testing.T) {
	dir := t.TempDir()
	if err := os.Symlink(filepath.Join(dir, "gone"), filepath.Join(dir, "segment-1757843642188211200.db")); err != nil {
		t.Fatal(err)
	}

	var out strings.Builder
	if err := analyzeSegments(dir, &out); err != nil {
		t.Fatalf("expected vanished entry to be skipped, got error: %v", err)
	}
	if strings.Contains(out.String(), "segment-1757843642188211200.db") {
		t.Errorf("vanished entry should not be listed:\n%s", out.String())
	}
}
