// Package worker holds the support layer for DAKP's native Go extraction workers:
// the ArtifactRef/Config (de)serialization, input staging, BLAKE3 store registration
// (mirroring Python io/artifact_store.py), and a generic all-string parquet writer for the
// interim tables. It is the Go counterpart to the Python io/ contracts, kept parity-compatible
// so the unchanged Python shaping stage can consume the Go-produced interim artifacts.
package worker

import (
	"os"

	"github.com/parquet-go/parquet-go"
)

// parquetMaxRowsPerRowGroup caps rows per parquet row group so writers stay bounded in memory:
// the parquet-go default is math.MaxInt64 (one row group == the whole table buffered). DAKP's
// small normalized tables still fit a single row group (byte-identical output); FAERS-scale
// outputs get many row groups instead of one giant in-memory one.
const parquetMaxRowsPerRowGroup = 1 << 20

// parquetWriteBatchRows is the number of rows StringParquetWriter buffers before one
// WriteRows call. Per-row WriteRows calls dominate extraction CPU at FAERS scale (each call
// re-enters the writer machinery); batching amortizes it. The written values are identical to
// the per-row path, but page-split boundaries move with the call boundaries, so the physical
// file bytes differ from the pre-batching writer (verified: TestStringParquetBatching).
// Batching is deterministic across runs, and WorkerVersion participates in every cache key
// (bumped with this change), so old cached parquets are invalidated rather than mis-matched.
const parquetWriteBatchRows = 8192

// WriteStringParquet writes columns + rows as an all-string (UTF8) parquet file and returns the
// row count. Every DAKP interim table is all-Utf8 (the Python extractor builds all-string frames
// and the Go TSV parity reads with infer_schema_length=0), so a single generic string writer
// covers all of them. Column *names* match the Python contracts exactly. parquet.Group is a map,
// so parquet-go writes the columns in alphabetical physical order; this is immaterial because
// polars reads parquet by column name (the shaping stage selects columns by name) and the
// schema_fingerprint is computed from the logical contract order, not the physical order
// (verified: polars reads Go-written parquet with the correct String schema and exact values).
// Empty cells are written as "" (never null), matching the Python all-string frames.
// Streaming callers (FAERS-scale outputs) use NewStringParquetWriter directly.
func WriteStringParquet(path string, columns []string, rows [][]string) (int, error) {
	w, err := NewStringParquetWriter(path, columns)
	if err != nil {
		return 0, err
	}
	for _, row := range rows {
		if err := w.Append(row); err != nil {
			w.Close()
			return 0, err
		}
	}
	return w.Close()
}

// StringParquetWriter streams all-string (UTF8) rows into one parquet file with bounded memory
// (row groups capped at parquetMaxRowsPerRowGroup rows). Same schema/leaf mapping as
// WriteStringParquet: rows smaller than the cap produce byte-identical files. Append buffers
// up to parquetWriteBatchRows rows before touching the underlying writer; Close flushes the
// remainder, finalizes the file, and is idempotent (repeat calls return the first result).
type StringParquetWriter struct {
	f       *os.File
	w       *parquet.Writer
	columns []string
	leafIdx map[string]int
	buf     []parquet.Row
	rows    int

	closed    bool
	closeRows int
	closeErr  error
}

// NewStringParquetWriter opens path for streaming all-string parquet rows under the given
// (logical contract) column names.
func NewStringParquetWriter(path string, columns []string) (*StringParquetWriter, error) {
	group := make(parquet.Group, len(columns))
	for _, col := range columns {
		group[col] = parquet.String()
	}
	schema := parquet.NewSchema("table", group)

	// Map column name -> leaf index in the schema's (deterministic) leaf order, so each value can
	// be placed at the correct column index regardless of the group map's iteration order.
	leafIdx := make(map[string]int, len(columns))
	for i, colPath := range schema.Columns() {
		leafIdx[colPath[len(colPath)-1]] = i
	}

	f, err := os.Create(path)
	if err != nil {
		return nil, err
	}
	w := parquet.NewWriter(f, schema, parquet.MaxRowsPerRowGroup(parquetMaxRowsPerRowGroup))
	return &StringParquetWriter{f: f, w: w, columns: columns, leafIdx: leafIdx}, nil
}

// Append buffers one row (values aligned to the writer's columns; short rows pad with "").
// The row values are captured at call time, so the caller may reuse the slice. When the
// buffer reaches parquetWriteBatchRows rows it is flushed to the underlying writer.
func (s *StringParquetWriter) Append(row []string) error {
	r := make(parquet.Row, len(s.columns))
	for ci, col := range s.columns {
		v := ""
		if ci < len(row) {
			v = row[ci]
		}
		idx := s.leafIdx[col]
		r[idx] = parquet.ValueOf(v).Level(0, 0, idx) // flat required column: rep=0, def=0
	}
	s.buf = append(s.buf, r)
	if len(s.buf) >= parquetWriteBatchRows {
		return s.flush()
	}
	return nil
}

// flush writes the buffered rows in one WriteRows call and advances the row counter.
// On error the buffer is kept (the caller aborts; the eventual Close surfaces the failure).
func (s *StringParquetWriter) flush() error {
	if len(s.buf) == 0 {
		return nil
	}
	if _, err := s.w.WriteRows(s.buf); err != nil {
		return err
	}
	s.rows += len(s.buf)
	s.buf = s.buf[:0]
	return nil
}

// Close flushes any buffered rows, finalizes the parquet file, and returns the number of rows
// written. It is idempotent: repeat calls return the first call's (rows, err) without touching
// the underlying writer again.
func (s *StringParquetWriter) Close() (int, error) {
	if s.closed {
		return s.closeRows, s.closeErr
	}
	s.closed = true
	err := s.flush()
	if werr := s.w.Close(); err == nil {
		err = werr
	}
	if ferr := s.f.Close(); err == nil {
		err = ferr
	}
	s.closeRows, s.closeErr = s.rows, err
	return s.closeRows, s.closeErr
}
