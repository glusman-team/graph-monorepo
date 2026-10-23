package worker

import (
	"path/filepath"
	"strconv"
	"testing"
)

// benchStringParquet writes rows x cols all-string rows through StringParquetWriter and returns
// the reported row count. Used by the batching benchmark (before/after perf on Append).
func benchStringParquet(b *testing.B, rows, cols int) {
	b.Helper()
	columns := make([]string, cols)
	for c := range columns {
		columns[c] = "col_" + strconv.Itoa(c)
	}
	grid := make([][]string, rows)
	for r := range grid {
		rec := make([]string, cols)
		for c := range rec {
			rec[c] = strconv.Itoa(r*cols + c)
		}
		grid[r] = rec
	}
	dir := b.TempDir()
	b.ResetTimer()
	for i := 0; i < b.N; i++ {
		path := filepath.Join(dir, "bench_"+strconv.Itoa(i)+".parquet")
		w, err := NewStringParquetWriter(path, columns)
		if err != nil {
			b.Fatal(err)
		}
		for r := range grid {
			if err := w.Append(grid[r]); err != nil {
				b.Fatal(err)
			}
		}
		if _, err := w.Close(); err != nil {
			b.Fatal(err)
		}
	}
}

func BenchmarkStringParquet200kx8(b *testing.B) {
	benchStringParquet(b, 200_000, 8)
}
