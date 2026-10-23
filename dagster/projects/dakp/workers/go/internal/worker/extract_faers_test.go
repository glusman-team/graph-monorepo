package worker

import (
	"archive/zip"
	"bytes"
	"context"
	"os"
	"path/filepath"
	"testing"

	"github.com/glusman-team/dakp/go/internal/blake3store"
	"github.com/glusman-team/dakp/go/internal/faers"
)

// faersFixtureRefs builds an ArtifactRef per .txt fixture under ../faers/testdata (byte-identical
// to the Python fixtures; internal/faers parity-locks the parser to the Python reference).
func faersFixtureRefs(t *testing.T) []ArtifactRef {
	t.Helper()
	files, err := filepath.Glob("../faers/testdata/*.txt")
	if err != nil || len(files) == 0 {
		t.Fatalf("faers fixtures: %v (%d)", err, len(files))
	}
	var refs []ArtifactRef
	for _, f := range files {
		abs, _ := filepath.Abs(f)
		id, err := blake3store.HashFile(abs)
		if err != nil {
			t.Fatal(err)
		}
		refs = append(refs, ArtifactRef{URI: abs, Blake3: id, MediaType: "text/plain"})
	}
	return refs
}

func TestExtractFAERSParity(t *testing.T) {
	refs := faersFixtureRefs(t)
	cfg := Config{Workdir: t.TempDir(), Profile: "mock", Threads: 4}
	got, err := ExtractFAERS(context.Background(), cfg, refs)
	if err != nil {
		t.Fatalf("ExtractFAERS: %v", err)
	}

	wantNames := []string{"cases.parquet", "faers_cases.tsv", "delete_audit.parquet", "dedup_audit.parquet", "warnings.parquet"}
	if len(got) != len(wantNames) {
		t.Fatalf("got %d refs, want %d", len(got), len(wantNames))
	}
	for i, want := range wantNames {
		if filepath.Base(got[i].URI) != want {
			t.Errorf("ref[%d] = %q, want %q", i, filepath.Base(got[i].URI), want)
		}
	}

	// Canonical parser output for cross-checks.
	srcs, err := loadFAERSSources(mustStage(t, refs))
	if err != nil {
		t.Fatal(err)
	}
	res, err := faers.Extract(context.Background(), srcs, 4, &faers.Warnings{})
	if err != nil {
		t.Fatal(err)
	}

	// faers_cases.tsv must byte-match the canonical parser TSV.
	var wantTSV bytes.Buffer
	if err := faers.WriteCasesTSV(&wantTSV, res.Cases); err != nil {
		t.Fatal(err)
	}
	gotTSV, err := os.ReadFile(got[1].URI)
	if err != nil {
		t.Fatal(err)
	}
	if !bytes.Equal(gotTSV, wantTSV.Bytes()) {
		t.Errorf("faers_cases.tsv != canonical parser output\n--- got ---\n%s\n--- want ---\n%s", gotTSV, wantTSV.Bytes())
	}

	// cases.parquet: 17 columns (faersCaseColumns) + one row per case.
	cols, rows := readBack(t, got[0].URI)
	if len(cols) != len(faersCaseColumns) {
		t.Errorf("cases.parquet columns = %d (%v), want %d", len(cols), cols, len(faersCaseColumns))
	}
	if len(rows) != len(res.Cases) {
		t.Errorf("cases.parquet rows = %d, want %d", len(rows), len(res.Cases))
	}
	if got[0].Rows == nil || int(*got[0].Rows) != len(res.Cases) {
		t.Errorf("cases.parquet ref.Rows = %v, want %d", got[0].Rows, len(res.Cases))
	}
	colIndex := make(map[string]int, len(cols))
	for i, name := range cols {
		colIndex[name] = i
	}
	for i, want := range res.Cases {
		row := rows[i]
		checks := map[string]string{
			"nda_raw":          want.NdaRaw,
			"drug_seq":         want.DrugSeq,
			"indi_drug_seq":    want.IndiDrugSeq,
			"source_file":      want.SourceFile,
			"source_record_id": want.SourceRecordID,
		}
		for name, expected := range checks {
			if gotValue := row[colIndex[name]]; gotValue != expected {
				t.Errorf("cases.parquet row %d %s = %q, want %q", i, name, gotValue, expected)
			}
		}
	}

	// warnings.parquet: one row per collected warning (Python _WARNINGS_COLUMNS: quarter,
	// family, code, message, count), sorted by (quarter, family, code, message). The 24Q3
	// fixtures delete one DRUG row, so exactly one deleted_rows_dropped row is expected.
	wcols, wrows := readBack(t, got[4].URI)
	wantWarnings := [][]string{{"24Q3", "DRUG", "deleted_rows_dropped", "1 drug rows dropped (DELETE)", "1"}}
	assertWarningRows(t, "warnings.parquet", wcols, wrows, faersWarningsColumns, wantWarnings)
	if got[4].Rows == nil || int(*got[4].Rows) != len(wantWarnings) {
		t.Errorf("warnings.parquet ref.Rows = %v, want %d", got[4].Rows, len(wantWarnings))
	}
}

// assertWarningRows checks a warnings artifact against the expected rows: the column set must
// equal wantCols (parquet physical order is alphabetical, so compare by name), and the rows
// must match want row-by-row when cells are resolved by column name.
func assertWarningRows(t *testing.T, what string, cols []string, rows [][]string, wantCols []string, want [][]string) {
	t.Helper()
	if len(cols) != len(wantCols) {
		t.Fatalf("%s columns = %v, want set %v", what, cols, wantCols)
	}
	idx := make(map[string]int, len(cols))
	for i, c := range cols {
		idx[c] = i
	}
	for _, c := range wantCols {
		if _, ok := idx[c]; !ok {
			t.Fatalf("%s columns = %v, missing %q", what, cols, c)
		}
	}
	if len(rows) != len(want) {
		t.Fatalf("%s rows = %d, want %d: %v", what, len(rows), len(want), rows)
	}
	for ri, wr := range want {
		for ci, col := range wantCols {
			if got := rows[ri][idx[col]]; got != wr[ci] {
				t.Errorf("%s row %d %s = %q, want %q", what, ri, col, got, wr[ci])
			}
		}
	}
}

// mustRefByBase returns the ref whose basename is base (fails when absent).
func mustRefByBase(t *testing.T, refs []ArtifactRef, base string) ArtifactRef {
	t.Helper()
	for _, r := range refs {
		if filepath.Base(r.URI) == base {
			return r
		}
	}
	t.Fatalf("no ref named %s in %v", base, refNames(refs))
	return ArtifactRef{}
}

// TestExtractFAERSWarningRows proves the warnings.parquet parity fix: broken sources (missing
// primaryid, empty file) produce one sorted row per collected warning across multiple quarters
// (the per-family parse runs concurrently, so the sort by quarter/family/code/message is what
// keeps repeated runs byte-identical).
func TestExtractFAERSWarningRows(t *testing.T) {
	dir := t.TempDir()
	writeSrc := func(name, body string) {
		if err := os.WriteFile(filepath.Join(dir, name), []byte(body), 0o644); err != nil {
			t.Fatal(err)
		}
	}
	writeSrc("REAC23Q1.txt", "RID$HLGT$\n1$2$\n")      // no primaryid/isr column
	writeSrc("DEMO25Q1.txt", "DUMMY1$DUMMY2$\nx$y$\n") // no primaryid/isr column
	writeSrc("RPSR25Q1.txt", "")                       // empty file
	refs := faersFixtureRefs(t)
	for _, name := range []string{"REAC23Q1.txt", "DEMO25Q1.txt", "RPSR25Q1.txt"} {
		abs, _ := filepath.Abs(filepath.Join(dir, name))
		id, err := blake3store.HashFile(abs)
		if err != nil {
			t.Fatal(err)
		}
		refs = append(refs, ArtifactRef{URI: abs, Blake3: id, MediaType: "text/plain"})
	}

	cfg := Config{Workdir: t.TempDir(), Profile: "mock", Threads: 4}
	got, err := ExtractFAERS(context.Background(), cfg, refs)
	if err != nil {
		t.Fatalf("ExtractFAERS: %v", err)
	}
	warningsRef := mustRefByBase(t, got, "warnings.parquet")
	wcols, wrows := readBack(t, warningsRef.URI)
	want := [][]string{
		{"23Q1", "REAC", "missing_primaryid", "no primaryid/isr column", "1"},
		{"24Q3", "DRUG", "deleted_rows_dropped", "1 drug rows dropped (DELETE)", "1"},
		{"25Q1", "DEMO", "missing_primaryid", "no primaryid/isr column", "1"},
		{"25Q1", "RPSR", "empty_file", "no data rows", "1"},
	}
	assertWarningRows(t, "warnings.parquet (broken fixtures)", wcols, wrows, faersWarningsColumns, want)
	if warningsRef.Rows == nil || int(*warningsRef.Rows) != len(want) {
		t.Errorf("warnings.parquet ref.Rows = %v, want %d", warningsRef.Rows, len(want))
	}
	// Re-running with Force must produce the same file bytes (deterministic sort => stable
	// BLAKE3 for the op-index cache).
	cfg2 := Config{Workdir: t.TempDir(), Profile: "mock", Threads: 4, Force: true}
	got2, err := ExtractFAERS(context.Background(), cfg2, refs)
	if err != nil {
		t.Fatalf("ExtractFAERS(force): %v", err)
	}
	second := mustRefByBase(t, got2, "warnings.parquet")
	b1, _ := os.ReadFile(warningsRef.URI)
	b2, _ := os.ReadFile(second.URI)
	if !bytes.Equal(b1, b2) {
		t.Errorf("warnings.parquet differs across runs: %d vs %d bytes", len(b1), len(b2))
	}
	if second.Blake3 != warningsRef.Blake3 {
		t.Errorf("warnings.parquet blake3 = %s, want %s", second.Blake3, warningsRef.Blake3)
	}
}

// mustStage stages refs into a fresh dir and returns it (for loading canonical sources).
func mustStage(t *testing.T, refs []ArtifactRef) string {
	t.Helper()
	dir := t.TempDir()
	if err := StageInputs(refs, dir); err != nil {
		t.Fatal(err)
	}
	return dir
}

// TestLoadFAERSSourcesFromZip covers the real download shape: a quarterly ASCII zip nests the .txt
// files under ASCII/ (plus non-ASCII members to skip); loadFAERSSources must read the .txt members
// straight out of the archive (mirrors faers_ascii._iter_faers_sources).
func TestLoadFAERSSourcesFromZip(t *testing.T) {
	dir := t.TempDir()
	zipPath := filepath.Join(dir, "faers_ascii_24Q3.zip")
	f, err := os.Create(zipPath)
	if err != nil {
		t.Fatal(err)
	}
	zw := zip.NewWriter(f)
	for _, name := range []string{"DEMO24Q3.txt", "DRUG24Q3.txt"} {
		body, err := os.ReadFile(filepath.Join("../faers/testdata", name))
		if err != nil {
			t.Fatal(err)
		}
		w, err := zw.Create("ASCII/" + name)
		if err != nil {
			t.Fatal(err)
		}
		if _, err := w.Write(body); err != nil {
			t.Fatal(err)
		}
	}
	// A non-FAERS member (no family prefix) must be skipped.
	if w, err := zw.Create("ASCII/Readme.pdf"); err != nil {
		t.Fatal(err)
	} else if _, err := w.Write([]byte("not ascii")); err != nil {
		t.Fatal(err)
	}
	if err := zw.Close(); err != nil {
		t.Fatal(err)
	}
	if err := f.Close(); err != nil {
		t.Fatal(err)
	}

	id, err := blake3store.HashFile(zipPath)
	if err != nil {
		t.Fatal(err)
	}
	srcs, err := loadFAERSSources(mustStage(t, []ArtifactRef{{URI: zipPath, Blake3: id, MediaType: "application/zip"}}))
	if err != nil {
		t.Fatal(err)
	}
	if len(srcs) != 2 {
		t.Fatalf("got %d sources, want 2 (DEMO + DRUG from the zip)", len(srcs))
	}
	byFamily := map[string]faers.Source{}
	for _, s := range srcs {
		byFamily[s.Family] = s
	}
	for _, fam := range []string{"DEMO", "DRUG"} {
		s, ok := byFamily[fam]
		if !ok {
			t.Fatalf("missing %s source", fam)
		}
		if s.Quarter != "24Q3" {
			t.Errorf("%s quarter = %q, want 24Q3", fam, s.Quarter)
		}
		want, _ := os.ReadFile(filepath.Join("../faers/testdata", fam+"24Q3.txt"))
		if !bytes.Equal(s.Content, want) {
			t.Errorf("%s content mismatch", fam)
		}
	}
}
