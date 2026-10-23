package worker

import (
	"bytes"
	"crypto/sha256"
	"io"
	"os"
	"path/filepath"
	"sort"
	"strconv"
	"testing"

	"github.com/parquet-go/parquet-go"
)

// readBack opens a parquet file and returns its column names (schema leaf order) and rows
// (each row's values in column-index order, decoded as strings).
func readBack(t *testing.T, path string) ([]string, [][]string) {
	t.Helper()
	f, err := os.Open(path)
	if err != nil {
		t.Fatal(err)
	}
	defer f.Close()
	info, err := f.Stat()
	if err != nil {
		t.Fatal(err)
	}
	pf, err := parquet.OpenFile(f, info.Size())
	if err != nil {
		t.Fatal(err)
	}
	var cols []string
	for _, colPath := range pf.Schema().Columns() {
		cols = append(cols, colPath[len(colPath)-1])
	}
	// column name -> index, to reorder each row's values into `cols` order
	idx := make(map[string]int, len(cols))
	for i, c := range cols {
		idx[c] = i
	}

	var rows [][]string
	for _, rg := range pf.RowGroups() {
		r := rg.Rows()
		buf := make([]parquet.Row, 64)
		for {
			n, err := r.ReadRows(buf)
			for i := 0; i < n; i++ {
				rec := make([]string, len(cols))
				for _, v := range buf[i] {
					rec[idx[cols[v.Column()]]] = v.String()
				}
				rows = append(rows, rec)
			}
			if err == io.EOF {
				break
			}
			if err != nil {
				r.Close()
				t.Fatal(err)
			}
		}
		r.Close()
	}
	return cols, rows
}

func TestWriteStringParquetRoundTrip(t *testing.T) {
	dir := t.TempDir()
	path := filepath.Join(dir, "t.parquet")
	columns := []string{"spl_set_id", "approval_code", "section_text"}
	rows := [][]string{
		{"set-1", "NDA123", "indications text"},
		{"set-2", "", "tab\tand \"quotes\" and empty next"},
		{"set-3", "BLA9", ""},
	}
	n, err := WriteStringParquet(path, columns, rows)
	if err != nil {
		t.Fatalf("write: %v", err)
	}
	if n != len(rows) {
		t.Fatalf("row count = %d, want %d", n, len(rows))
	}

	gotCols, gotRows := readBack(t, path)
	// Column names must match exactly (as a set; physical order is irrelevant to polars).
	wantSet := append([]string(nil), columns...)
	gotSet := append([]string(nil), gotCols...)
	sort.Strings(wantSet)
	sort.Strings(gotSet)
	if len(gotSet) != len(wantSet) {
		t.Fatalf("columns = %v, want %v", gotCols, columns)
	}
	for i := range wantSet {
		if gotSet[i] != wantSet[i] {
			t.Fatalf("columns = %v, want %v", gotCols, columns)
		}
	}
	// Row values must round-trip exactly (including empty strings and embedded tab/quotes).
	if len(gotRows) != len(rows) {
		t.Fatalf("read %d rows, want %d: %v", len(gotRows), len(rows), gotRows)
	}
	// Reorder got rows into the original `columns` order for comparison.
	colIdx := make(map[string]int, len(gotCols))
	for i, c := range gotCols {
		colIdx[c] = i
	}
	for ri, want := range rows {
		for ci, col := range columns {
			if got := gotRows[ri][colIdx[col]]; got != want[ci] {
				t.Errorf("row %d col %q = %q, want %q", ri, col, got, want[ci])
			}
		}
	}
}

// writeUnbatchedStringParquet is the pre-batching reference path: one WriteRows call per row
// through the same schema/leaf mapping NewStringParquetWriter builds. Used to prove the
// buffered writer produces identical values (semantics unchanged).
func writeUnbatchedStringParquet(t *testing.T, path string, columns []string, rows [][]string) {
	t.Helper()
	group := make(parquet.Group, len(columns))
	for _, col := range columns {
		group[col] = parquet.String()
	}
	schema := parquet.NewSchema("table", group)
	leafIdx := make(map[string]int, len(columns))
	for i, colPath := range schema.Columns() {
		leafIdx[colPath[len(colPath)-1]] = i
	}
	f, err := os.Create(path)
	if err != nil {
		t.Fatal(err)
	}
	w := parquet.NewWriter(f, schema, parquet.MaxRowsPerRowGroup(parquetMaxRowsPerRowGroup))
	for _, row := range rows {
		r := make(parquet.Row, len(columns))
		for ci, col := range columns {
			v := ""
			if ci < len(row) {
				v = row[ci]
			}
			idx := leafIdx[col]
			r[idx] = parquet.ValueOf(v).Level(0, 0, idx)
		}
		if _, err := w.WriteRows([]parquet.Row{r}); err != nil {
			t.Fatal(err)
		}
	}
	if err := w.Close(); err != nil {
		t.Fatal(err)
	}
	if err := f.Close(); err != nil {
		t.Fatal(err)
	}
}

func TestStringParquetWriterBatching(t *testing.T) {
	dir := t.TempDir()
	path := filepath.Join(dir, "batched.parquet")
	columns := []string{"b", "a"}
	const n = 20_000 // spans 2 full 8192-row flushes + a remainder flush on Close

	w, err := NewStringParquetWriter(path, columns)
	if err != nil {
		t.Fatal(err)
	}
	row := make([]string, 2) // reuse one backing slice: Append must capture values at call time
	for i := 0; i < n; i++ {
		row[0] = strconv.Itoa(i)
		row[1] = "v" + strconv.Itoa(i%7)
		if err := w.Append(row); err != nil {
			t.Fatalf("append %d: %v", i, err)
		}
	}
	cnt, err := w.Close()
	if err != nil {
		t.Fatalf("close: %v", err)
	}
	if cnt != n {
		t.Fatalf("Close rows = %d, want %d", cnt, n)
	}
	// Close is idempotent: a repeat call returns the same result without re-touching the file.
	cnt2, err2 := w.Close()
	if cnt2 != cnt || (err2 == nil) != (err == nil) {
		t.Errorf("second Close = (%d, %v), want (%d, same error)", cnt2, err2, cnt)
	}

	cols, rows := readBack(t, path)
	if len(rows) != n {
		t.Fatalf("read back %d rows, want %d", len(rows), n)
	}
	idx := make(map[string]int, len(cols))
	for i, c := range cols {
		idx[c] = i
	}
	for _, i := range []int{0, 1, 8191, 8192, 16383, 16384, n - 1} {
		if got := rows[i][idx["b"]]; got != strconv.Itoa(i) {
			t.Errorf("row %d b = %q, want %q", i, got, strconv.Itoa(i))
		}
		if got := rows[i][idx["a"]]; got != "v"+strconv.Itoa(i%7) {
			t.Errorf("row %d a = %q, want %q", i, got, "v"+strconv.Itoa(i%7))
		}
	}
}

func TestStringParquetBatchingValueIdentity(t *testing.T) {
	columns := []string{"second", "first", "third"}
	const n = 3*parquetWriteBatchRows + 411 // crosses several flush boundaries
	rows := make([][]string, n)
	for i := range rows {
		rows[i] = []string{strconv.Itoa(i), "", "cell_" + strconv.Itoa(i%31)} // empty cells exercise padding
	}
	batchedPath := filepath.Join(t.TempDir(), "batched.parquet")
	unbatchedPath := filepath.Join(t.TempDir(), "unbatched.parquet")

	if got, err := WriteStringParquet(batchedPath, columns, rows); err != nil || got != n {
		t.Fatalf("WriteStringParquet = (%d, %v), want (%d, nil)", got, err, n)
	}
	writeUnbatchedStringParquet(t, unbatchedPath, columns, rows)

	// Values must be identical to the per-row reference path; physical bytes may differ only in
	// page-split metadata (parquet-go splits pages on WriteRows call boundaries).
	colsA, rowsA := readBack(t, batchedPath)
	colsB, rowsB := readBack(t, unbatchedPath)
	if len(rowsA) != len(rowsB) {
		t.Fatalf("row counts differ: %d vs %d", len(rowsA), len(rowsB))
	}
	for ci, c := range colsA {
		if colsB[ci] != c {
			t.Fatalf("leaf order differs at %d: %q vs %q", ci, c, colsB[ci])
		}
	}
	for ri := range rowsA {
		for ci := range colsA {
			if rowsA[ri][ci] != rowsB[ri][ci] {
				t.Fatalf("row %d col %s: %q vs %q", ri, colsA[ci], rowsA[ri][ci], rowsB[ri][ci])
			}
		}
	}

	// Determinism across runs: re-writing the same rows must hash identically (BLAKE3 cache).
	repeatPath := filepath.Join(t.TempDir(), "repeat.parquet")
	if got, err := WriteStringParquet(repeatPath, columns, rows); err != nil || got != n {
		t.Fatalf("re-write = (%d, %v)", got, err)
	}
	a, _ := os.ReadFile(batchedPath)
	c, _ := os.ReadFile(repeatPath)
	ha, hc := sha256.Sum256(a), sha256.Sum256(c)
	if !bytes.Equal(ha[:], hc[:]) {
		t.Errorf("batched output differs across runs: %x vs %x", ha[:8], hc[:8])
	}
}
