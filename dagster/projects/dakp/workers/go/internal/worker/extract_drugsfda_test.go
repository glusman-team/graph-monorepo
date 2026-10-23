package worker

import (
	"archive/zip"
	"context"
	"encoding/json"
	"fmt"
	"os"
	"path/filepath"
	"strings"
	"testing"
	"unicode/utf8"

	"github.com/glusman-team/dakp/go/internal/blake3store"
	"github.com/glusman-team/dakp/go/internal/drugsfda"
)

func drugsfdaFixtureRefs(t *testing.T) []ArtifactRef {
	t.Helper()
	files, err := filepath.Glob("../drugsfda/testdata/*.tsv")
	if err != nil || len(files) == 0 {
		t.Fatalf("drugsfda fixtures: %v (%d)", err, len(files))
	}
	var refs []ArtifactRef
	for _, f := range files {
		abs, _ := filepath.Abs(f)
		id, err := blake3store.HashFile(abs)
		if err != nil {
			t.Fatal(err)
		}
		refs = append(refs, ArtifactRef{URI: abs, Blake3: id, MediaType: "text/tab-separated-values"})
	}
	return refs
}

func TestExtractDrugsFDAParity(t *testing.T) {
	refs := drugsfdaFixtureRefs(t)
	cfg := Config{Workdir: t.TempDir(), Profile: "mock", Threads: 4}
	got, err := ExtractDrugsFDA(context.Background(), cfg, refs)
	if err != nil {
		t.Fatalf("ExtractDrugsFDA: %v", err)
	}

	// products.parquet, drugsfda_products.tsv, applications.parquet, submissions.parquet,
	// lookups.parquet, extract_warnings.jsonl
	wantNames := []string{
		"products.parquet", "drugsfda_products.tsv", "applications.parquet",
		"submissions.parquet", "lookups.parquet", "extract_warnings.jsonl",
	}
	if len(got) != len(wantNames) {
		t.Fatalf("got %d refs (%v), want %d", len(got), refNames(got), len(wantNames))
	}
	for i, want := range wantNames {
		if filepath.Base(got[i].URI) != want {
			t.Errorf("ref[%d] = %q, want %q", i, filepath.Base(got[i].URI), want)
		}
	}

	// Canonical parser output for row-count cross-checks.
	discovered, err := discoverDrugsFDAInputs(mustStage(t, refs))
	if err != nil {
		t.Fatal(err)
	}
	tables, err := parseDrugsFDAInputs(discovered)
	if err != nil {
		t.Fatal(err)
	}
	res := drugsfda.Extract(tables)

	// Public products TSV must byte-match the Python/polars golden.
	gotTSV, err := os.ReadFile(got[1].URI)
	if err != nil {
		t.Fatal(err)
	}
	wantTSV, err := os.ReadFile("../drugsfda/testdata/golden/drugsfda_products.tsv")
	if err != nil {
		t.Fatal(err)
	}
	if string(gotTSV) != string(wantTSV) {
		t.Errorf("drugsfda_products.tsv != golden\n--- got ---\n%s\n--- want ---\n%s", gotTSV, wantTSV)
	}

	// products.parquet: ProductsColumns + one row per product.
	cols, rows := readBack(t, got[0].URI)
	if len(cols) != len(drugsfda.ProductsColumns) {
		t.Errorf("products.parquet columns = %d, want %d", len(cols), len(drugsfda.ProductsColumns))
	}
	if len(rows) != len(res.Products) {
		t.Errorf("products.parquet rows = %d, want %d", len(rows), len(res.Products))
	}

	// extract_warnings.jsonl must carry exactly the canonical collector warnings (the clean
	// fixture set yields none, but the file must exist and be referenced with rows == 0).
	lines := readJSONLines(t, got[5].URI)
	assertDrugsFDAWarningLines(t, lines, res.Warnings)
	if got[5].Rows == nil || int(*got[5].Rows) != len(res.Warnings) {
		t.Errorf("extract_warnings.jsonl ref.Rows = %v, want %d", got[5].Rows, len(res.Warnings))
	}
}

// TestExtractDrugsFDAWarningRows proves the extract_warnings.jsonl parity fix: a products file
// with one row missing every application-number column yields the row-level warning plus the
// absent applications/submissions table warnings, one JSON line each in Python's
// json.dumps(sort_keys=True) wire format (keys ascending, source_file only when present).
func TestExtractDrugsFDAWarningRows(t *testing.T) {
	fixture, err := os.ReadFile("../drugsfda/testdata/drugsfda_products.tsv")
	if err != nil {
		t.Fatal(err)
	}
	body := string(fixture)
	if !strings.HasSuffix(body, "\n") {
		body += "\n"
	}
	header := strings.SplitN(body, "\n", 2)[0]
	blank := strings.Repeat("\t", strings.Count(header, "\t"))
	body += blank + "\n"
	dataRows := strings.Count(strings.TrimSuffix(body, "\n"), "\n") // excluding header

	dir := t.TempDir()
	prodPath := filepath.Join(dir, "Products.txt")
	if err := os.WriteFile(prodPath, []byte(body), 0o644); err != nil {
		t.Fatal(err)
	}
	id, err := blake3store.HashFile(prodPath)
	if err != nil {
		t.Fatal(err)
	}
	cfg := Config{Workdir: t.TempDir(), Profile: "mock", Threads: 4}
	got, err := ExtractDrugsFDA(context.Background(), cfg, []ArtifactRef{{URI: prodPath, Blake3: id, MediaType: "text/tab-separated-values"}})
	if err != nil {
		t.Fatalf("ExtractDrugsFDA: %v", err)
	}
	warningsRef := mustRefByBase(t, got, "extract_warnings.jsonl")

	want := []string{
		fmt.Sprintf(`{"message":"row %d: missing application number","source_file":"Products.txt","table":"products"}`, dataRows+1),
		`{"message":"no Applications table found in inputs","table":"applications"}`,
		`{"message":"no Submissions table found in inputs","table":"submissions"}`,
	}
	lines := readJSONLines(t, warningsRef.URI)
	if len(lines) != len(want) {
		t.Fatalf("extract_warnings.jsonl lines = %d, want %d: %q", len(lines), len(want), lines)
	}
	for i := range want {
		if lines[i] != want[i] {
			t.Errorf("warning line %d =\n%s\nwant\n%s", i, lines[i], want[i])
		}
	}
	if warningsRef.Rows == nil || int(*warningsRef.Rows) != len(want) {
		t.Errorf("extract_warnings.jsonl ref.Rows = %v, want %d", warningsRef.Rows, len(want))
	}

	// Determinism: a forced re-run must write byte-identical jsonl (stable hash for the cache).
	cfg2 := Config{Workdir: t.TempDir(), Profile: "mock", Threads: 4, Force: true}
	got2, err := ExtractDrugsFDA(context.Background(), cfg2, []ArtifactRef{{URI: prodPath, Blake3: id, MediaType: "text/tab-separated-values"}})
	if err != nil {
		t.Fatal(err)
	}
	second := mustRefByBase(t, got2, "extract_warnings.jsonl")
	if second.Blake3 != warningsRef.Blake3 {
		t.Errorf("extract_warnings.jsonl blake3 = %s, want %s", second.Blake3, warningsRef.Blake3)
	}
}

// TestExtractDrugsFDAWarningRowsNoTables covers the "nothing recognized" path: the Python
// extractor records a table="*" warning first, then one per absent table.
func TestExtractDrugsFDAWarningRowsNoTables(t *testing.T) {
	dir := t.TempDir()
	readme := filepath.Join(dir, "Readme.txt")
	if err := os.WriteFile(readme, []byte("not a drugsfda table\n"), 0o644); err != nil {
		t.Fatal(err)
	}
	id, err := blake3store.HashFile(readme)
	if err != nil {
		t.Fatal(err)
	}
	cfg := Config{Workdir: t.TempDir(), Profile: "mock", Threads: 4}
	got, err := ExtractDrugsFDA(context.Background(), cfg, []ArtifactRef{{URI: readme, Blake3: id, MediaType: "text/plain"}})
	if err != nil {
		t.Fatalf("ExtractDrugsFDA(unrecognized): %v", err)
	}
	warningsRef := mustRefByBase(t, got, "extract_warnings.jsonl")
	want := []string{
		`{"message":"no Drugs@FDA tables recognized in inputs","table":"*"}`,
		`{"message":"no Products table found in inputs","table":"products"}`,
		`{"message":"no Applications table found in inputs","table":"applications"}`,
		`{"message":"no Submissions table found in inputs","table":"submissions"}`,
	}
	lines := readJSONLines(t, warningsRef.URI)
	if len(lines) != len(want) {
		t.Fatalf("extract_warnings.jsonl lines = %d, want %d: %q", len(lines), len(want), lines)
	}
	for i := range want {
		if lines[i] != want[i] {
			t.Errorf("warning line %d =\n%s\nwant\n%s", i, lines[i], want[i])
		}
	}
}

// readJSONLines returns the non-empty lines of a .jsonl file.
func readJSONLines(t *testing.T, path string) []string {
	t.Helper()
	b, err := os.ReadFile(path)
	if err != nil {
		t.Fatal(err)
	}
	var out []string
	for _, line := range strings.Split(string(b), "\n") {
		if line != "" {
			out = append(out, line)
		}
	}
	return out
}

// assertDrugsFDAWarningLines checks jsonl lines against canonical warnings: one line each,
// same order, parsed fields equal (key order on the wire is ascending per json.dumps sort_keys).
func assertDrugsFDAWarningLines(t *testing.T, lines []string, warnings []drugsfda.Warning) {
	t.Helper()
	if len(lines) != len(warnings) {
		t.Fatalf("warning lines = %d, want %d", len(lines), len(warnings))
	}
	for i, w := range warnings {
		var got map[string]string
		if err := json.Unmarshal([]byte(lines[i]), &got); err != nil {
			t.Fatalf("line %d: %v", i, err)
		}
		if got["table"] != w.Table || got["message"] != w.Message || got["source_file"] != w.SourceFile {
			t.Errorf("line %d = %s, want table=%q message=%q source_file=%q", i, lines[i], w.Table, w.Message, w.SourceFile)
		}
	}
}

func refNames(refs []ArtifactRef) []string {
	var out []string
	for _, r := range refs {
		out = append(out, filepath.Base(r.URI))
	}
	return out
}

// TestExtractDrugsFDAFromZip covers the real download shape: the Drugs@FDA data-files zip carries
// Products/Applications/Submissions as members at the archive root. StageInputs only links the zip
// into inDir, so ExtractDrugsFDA must unpack the members and extract them like the loose fixtures.
func TestExtractDrugsFDAFromZip(t *testing.T) {
	files, err := filepath.Glob("../drugsfda/testdata/*.tsv")
	if err != nil || len(files) == 0 {
		t.Fatalf("drugsfda fixtures: %v (%d)", err, len(files))
	}
	dir := t.TempDir()
	zipPath := filepath.Join(dir, "drugsfda_data_files.zip")
	f, err := os.Create(zipPath)
	if err != nil {
		t.Fatal(err)
	}
	zw := zip.NewWriter(f)
	for _, fpath := range files {
		body, err := os.ReadFile(fpath)
		if err != nil {
			t.Fatal(err)
		}
		w, err := zw.Create(filepath.Base(fpath))
		if err != nil {
			t.Fatal(err)
		}
		if _, err := w.Write(body); err != nil {
			t.Fatal(err)
		}
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
	cfg := Config{Workdir: t.TempDir(), Profile: "mock", Threads: 4}
	got, err := ExtractDrugsFDA(context.Background(), cfg, []ArtifactRef{{URI: zipPath, Blake3: id, MediaType: "application/zip"}})
	if err != nil {
		t.Fatalf("ExtractDrugsFDA(zip): %v", err)
	}
	if len(got) == 0 || filepath.Base(got[0].URI) != "products.parquet" {
		t.Fatalf("got %v, want products.parquet first", refNames(got))
	}
}

// TestExtractDrugsFdadirtyUTF8 covers the live-feed shape: the real Drugs@FDA Submissions.txt
// carries Windows-1252 bytes (0x92 ’, 0x96 – in SubmissionsPublicNotes). Before the
// toValidUTF8 fix these bytes reached the parquet STRING columns raw, violating the Parquet
// spec and breaking strict readers (polars: "String data contained invalid UTF-8"). The
// sanitized output must be valid UTF-8 with the cp1252 bytes decoded.
func TestExtractDrugsFDADirtyUTF8(t *testing.T) {
	dirty, err := os.ReadFile("../drugsfda/testdata/dirty/Submissions.txt")
	if err != nil {
		t.Fatal(err)
	}
	products, err := os.ReadFile("../drugsfda/testdata/drugsfda_products.tsv")
	if err != nil {
		t.Fatal(err)
	}
	dir := t.TempDir()
	zipPath := filepath.Join(dir, "drugsfda_data_files.zip")
	f, err := os.Create(zipPath)
	if err != nil {
		t.Fatal(err)
	}
	zw := zip.NewWriter(f)
	for name, body := range map[string][]byte{"Submissions.txt": dirty, "Products.txt": products} {
		w, err := zw.Create(name)
		if err != nil {
			t.Fatal(err)
		}
		if _, err := w.Write(body); err != nil {
			t.Fatal(err)
		}
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
	cfg := Config{Workdir: t.TempDir(), Profile: "mock", Threads: 4}
	got, err := ExtractDrugsFDA(context.Background(), cfg, []ArtifactRef{{URI: zipPath, Blake3: id, MediaType: "application/zip"}})
	if err != nil {
		t.Fatalf("ExtractDrugsFDA(dirty zip): %v", err)
	}

	// refs: products.parquet, drugsfda_products.tsv, submissions.parquet, extract_warnings.jsonl.
	var submissionsPath string
	for _, ref := range got {
		if filepath.Base(ref.URI) == "submissions.parquet" {
			submissionsPath = ref.URI
		}
	}
	if submissionsPath == "" {
		t.Fatalf("no submissions.parquet in refs %v", refNames(got))
	}

	cols, rows := readBack(t, submissionsPath)
	if len(rows) != 3 {
		t.Fatalf("submissions rows = %d, want 3", len(rows))
	}
	notesIdx := -1
	for i, c := range cols {
		if c == "submission_notes" {
			notesIdx = i
		}
	}
	if notesIdx < 0 {
		t.Fatalf("submission_notes column missing: %v", cols)
	}
	for ri, row := range rows {
		for ci, cell := range row {
			if !utf8.ValidString(cell) {
				t.Errorf("row %d col %s = %q is not valid UTF-8", ri, cols[ci], cell)
			}
		}
	}
	wantNotes := []string{
		"Label for Men\u2019s Rogaine",
		"FR Notice on DEA Scheduling; Date of Approval \u2013 March 23, 2017",
		"Caf\u00e9 \u201cquoted\u201d bullet \u2022 undefined \ufffd end",
	}
	for ri, want := range wantNotes {
		if got := rows[ri][notesIdx]; got != want {
			t.Errorf("row %d notes = %q, want %q", ri, got, want)
		}
	}
}
