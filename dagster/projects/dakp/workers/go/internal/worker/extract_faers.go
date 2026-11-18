package worker

import (
	"archive/zip"
	"bufio"
	"context"
	"fmt"
	"io"
	"os"
	"path/filepath"
	"sort"
	"strconv"
	"strings"
	"sync"
	"time"

	"github.com/glusman-team/dakp/go/internal/blake3store"
	"github.com/glusman-team/dakp/go/internal/faers"
	"golang.org/x/sync/errgroup"
)

// faersCaseColumns is the rich interim case schema (mirrors faers_ascii._CASE_COLUMNS). The native
// streaming task preserves the full rich case row, including NDA raw value, role/sequence metadata,
// source file, and source_record_id, so downstream assertion shapers can trace exact FAERS records.
var faersCaseColumns = []string{
	"quarter", "primaryid", "caseid", "source", "occp_cod", "reporter_country",
	"drugname", "ingredient", "nda", "nda_raw", "role_cod", "drug_seq", "indi_drug_seq",
	"indication", "effects", "source_file", "source_record_id",
}

var faersWarningsColumns = []string{"quarter", "family", "code", "message", "count"}

// faersEvent prefixes every stat line the FAERS extractor emits (one stat per line).
const faersEvent = "extract_faers"

// faersTaskOperation is the task-level operation-index key for the already-done skip (the
// per-artifact manifests keep their own operation names; see opindex.go).
const faersTaskOperation = "extract_faers"

// ExtractFAERS runs both phases for callers that do not use Dagster's separate assets.
func ExtractFAERS(ctx context.Context, cfg Config, inputs []ArtifactRef) ([]ArtifactRef, error) {
	store := Store{Workdir: cfg.Workdir}
	if !cfg.Force {
		if cached := store.FindByOperation(faersTaskOperation, upstreamInputIDs(inputs)); cached != nil {
			Stat(ctx, faersEvent, "skipped_already_done", len(cached))
			return cached, nil
		}
	}
	quarters, err := ParseFAERSQuarters(ctx, cfg, inputs)
	if err != nil {
		return nil, err
	}
	refs, err := mergeFAERSQuarters(ctx, cfg, quarters, upstreamInputIDs(inputs))
	if err == nil {
		store.RecordOperation(faersTaskOperation, upstreamInputIDs(inputs), refs)
	}
	return refs, err
}

// mergeFAERSQuarters applies cross-quarter dedup in descending quarter order, retaining
// only one decoded quarter at a time before the final k-way merge.
func mergeFAERSQuarters(ctx context.Context, cfg Config, quarters []ArtifactRef, inputIDs []string) ([]ArtifactRef, error) {
	store := Store{Workdir: cfg.Workdir}
	deduper := faers.NewDeduper()
	scratch, err := os.MkdirTemp("", "dakp-faers-*")
	if err != nil {
		return nil, err
	}
	defer os.RemoveAll(scratch)
	faersDir := filepath.Join(store.InterimDir(), "faers", OpIndexKey(faersTaskOperation, append(append([]string(nil), inputIDs...), upstreamInputIDs(quarters)...)))
	if err := os.MkdirAll(faersDir, 0o755); err != nil {
		return nil, err
	}
	runsDir := filepath.Join(scratch, "runs")
	if err := os.MkdirAll(runsDir, 0o755); err != nil {
		return nil, err
	}

	var (
		runs        []string
		dedupAudit  []faers.DedupAuditRow
		deleteAudit []faers.DeleteAuditRow
		warnings    []faers.Warning
	)
	ordered := append([]ArtifactRef(nil), quarters...)
	sort.Slice(ordered, func(i, j int) bool { return filepath.Base(ordered[i].URI) > filepath.Base(ordered[j].URI) })
	previous := ""
	for _, ref := range ordered {
		if err := ctx.Err(); err != nil {
			return nil, err
		}
		quarterStart := time.Now()
		shard, err := readFAERSQuarter(ref.URI)
		if err != nil {
			return nil, err
		}
		q, cases := shard.Quarter, shard.Cases
		if filepath.Base(ref.URI) != q+".gob" || q == "" || (previous != "" && q >= previous) {
			return nil, fmt.Errorf("invalid or duplicate FAERS quarter %q in %s", q, ref.URI)
		}
		previous = q
		deleteAudit = append(deleteAudit, shard.Deletes...)
		warnings = append(warnings, shard.Warnings...)
		kept, superseded, newKeys := deduper.Split(q, cases)
		deduper.Commit(q, newKeys)
		dedupAudit = append(dedupAudit, superseded...)
		if len(kept) > 0 {
			run := filepath.Join(runsDir, fmt.Sprintf("cases_%s.parquet", q))
			if err := writeFAERSRun(run, kept); err != nil {
				return nil, fmt.Errorf("extract_faers: write run %s: %w", q, err)
			}
			runs = append(runs, run)
		}
		Stat(ctx, faersEvent, q+".cases", len(cases))
		Stat(ctx, faersEvent, q+".kept", len(kept))
		Stat(ctx, faersEvent, q+".superseded", len(superseded))
		Stat(ctx, faersEvent, q+".elapsed_s", fmt.Sprintf("%.3f", time.Since(quarterStart).Seconds()))
	}
	faers.SortDedupAudit(dedupAudit)
	var warningsTotal int64
	for _, item := range warnings {
		warningsTotal += int64(item.Count)
	}
	Stat(ctx, faersEvent, "warnings_total", warningsTotal)

	// 1+2. Global cases.parquet and public faers_cases.tsv, emitted in ONE merged pass.
	casesPath := filepath.Join(faersDir, "cases.parquet")
	tsvPath := filepath.Join(faersDir, "faers_cases.tsv")
	mergeStart := time.Now()
	casesN, err := mergeFAERSOutputs(runs, casesPath, tsvPath)
	if err != nil {
		return nil, fmt.Errorf("extract_faers: merge cases: %w", err)
	}
	Stat(ctx, faersEvent, "merged_rows", casesN)
	Stat(ctx, faersEvent, "merge_elapsed_s", fmt.Sprintf("%.3f", time.Since(mergeStart).Seconds()))
	// 1+2 registered as one batch (both files already exist; hashes run concurrently, refs
	// and stat lines keep their order).
	pairRefs, err := store.RegisterMany(ctx, []RegisterInput{
		{
			Path: casesPath, MediaType: ParquetMediaType, Rows: int64(casesN),
			SchemaFingerprint: SchemaFingerprint(faersCaseColumns), Inputs: inputIDs,
			Warnings: warningsTotal, Operation: "extract_faers_cases",
		},
		{
			Path: tsvPath, MediaType: TSVMediaType, Rows: int64(casesN),
			SchemaFingerprint: SchemaFingerprint(faers.CasesTSVColumns), Inputs: inputIDs,
			Warnings: warningsTotal, Operation: "emit_faers_cases_tsv",
		},
	})
	if err != nil {
		return nil, err
	}
	casesRef, tsvRef := pairRefs[0], pairRefs[1]
	StatOutput(ctx, faersEvent, "cases.parquet", casesRef)
	StatOutput(ctx, faersEvent, "faers_cases.tsv", tsvRef)

	// 3+4+5. delete_audit / dedup_audit / warnings parquets: written, then registered as
	// one batch (hashes run concurrently; refs and stat lines keep their order).
	deleteRows := make([][]string, 0)
	sort.SliceStable(deleteAudit, func(i, j int) bool {
		if deleteAudit[i].Quarter != deleteAudit[j].Quarter {
			return deleteAudit[i].Quarter < deleteAudit[j].Quarter
		}
		return deleteAudit[i].PrimaryID < deleteAudit[j].PrimaryID
	})
	for _, r := range deleteAudit {
		deleteRows = append(deleteRows, []string{r.Quarter, r.PrimaryID, r.CaseID, r.SourceFile, r.SourceRecordID})
	}
	deleteIn, err := writeFAERSAuditParquet(faersDir, "delete_audit", faers.DeleteAuditColumns, deleteRows, inputIDs, warningsTotal)
	if err != nil {
		return nil, err
	}
	dedupRows := make([][]string, 0, len(dedupAudit))
	for _, r := range dedupAudit {
		dedupRows = append(dedupRows, []string{r.Quarter, r.PrimaryID, r.CaseID, r.DedupKey, r.WinningQuarter, r.SourceFile})
	}
	dedupIn, err := writeFAERSAuditParquet(faersDir, "dedup_audit", faers.DedupAuditColumns, dedupRows, inputIDs, warningsTotal)
	if err != nil {
		return nil, err
	}
	// warnings.parquet: one row per collected warning (mirrors _Warnings.frame(): the five
	// _WARNINGS_COLUMNS, sorted by quarter, family, code, message so repeated runs produce
	// byte-identical output for the BLAKE3 op-index cache). The per-quarter parse runs in
	// parallel, so Items() order is scheduling-dependent; the sort makes it deterministic.
	warningsIn, err := writeFAERSAuditParquet(faersDir, "warnings", faersWarningsColumns, faersWarningRows(warnings), inputIDs, warningsTotal)
	if err != nil {
		return nil, err
	}
	auditRefs, err := store.RegisterMany(ctx, []RegisterInput{deleteIn, dedupIn, warningsIn})
	if err != nil {
		return nil, err
	}
	deleteRef, dedupRef, warningsRef := auditRefs[0], auditRefs[1], auditRefs[2]
	StatOutput(ctx, faersEvent, "delete_audit.parquet", deleteRef)
	StatOutput(ctx, faersEvent, "dedup_audit.parquet", dedupRef)
	StatOutput(ctx, faersEvent, "warnings.parquet", warningsRef)

	refs := []ArtifactRef{casesRef, tsvRef, deleteRef, dedupRef, warningsRef}
	Stat(ctx, faersEvent, "output_refs", len(refs))
	return refs, nil
}

// faersWarningRows renders collected warnings as warnings.parquet rows: one per item, ordered
// like faers_ascii._Warnings.frame() (sort by quarter, family, code, message). The count column
// carries the decimal count (all-string parquet convention; consumers read columns by name).
func faersWarningRows(items []faers.Warning) [][]string {
	sorted := append([]faers.Warning(nil), items...)
	sort.SliceStable(sorted, func(i, j int) bool {
		a, b := sorted[i], sorted[j]
		switch {
		case a.Quarter != b.Quarter:
			return a.Quarter < b.Quarter
		case a.Family != b.Family:
			return a.Family < b.Family
		case a.Code != b.Code:
			return a.Code < b.Code
		default:
			return a.Message < b.Message
		}
	})
	rows := make([][]string, 0, len(sorted))
	for _, w := range sorted {
		rows = append(rows, []string{w.Quarter, w.Family, w.Code, w.Message, strconv.Itoa(w.Count)})
	}
	return rows
}

// mergeFAERSOutputs k-way-merges the per-quarter sorted rich runs and streams the merged rows into
// cases.parquet (full CaseColumns via faersCaseRow17) and faers_cases.tsv (public projection)
// simultaneously. Returns the merged row count.
func mergeFAERSOutputs(runs []string, casesPath, tsvPath string) (int, error) {
	pw, err := NewStringParquetWriter(casesPath, faersCaseColumns)
	if err != nil {
		return 0, err
	}
	tsvFile, err := os.Create(tsvPath)
	if err != nil {
		pw.Close()
		return 0, err
	}
	tsv := bufio.NewWriterSize(tsvFile, 1<<20)
	if _, err := tsv.WriteString(strings.Join(faers.CasesTSVColumns, "\t") + "\n"); err != nil {
		pw.Close()
		tsvFile.Close()
		return 0, err
	}
	n := 0
	merr := mergeFAERSRuns(runs, func(row []string) error {
		if err := pw.Append(faersCaseRow17(row)); err != nil {
			return err
		}
		if err := writeFAERSRunTSVRow(tsv, row); err != nil {
			return err
		}
		n++
		return nil
	})
	if merr != nil {
		pw.Close()
		tsvFile.Close()
		return 0, merr
	}
	if err := tsv.Flush(); err != nil {
		pw.Close()
		tsvFile.Close()
		return 0, err
	}
	if err := tsvFile.Close(); err != nil {
		pw.Close()
		return 0, err
	}
	if _, err := pw.Close(); err != nil {
		return 0, err
	}
	return n, nil
}

// writeFAERSAuditParquet writes one audit parquet and returns its RegisterInput; the
// caller batches the registration via RegisterMany.
func writeFAERSAuditParquet(dir, name string, columns []string, rows [][]string, inputIDs []string, warnings int64) (RegisterInput, error) {
	path := filepath.Join(dir, name+".parquet")
	n, err := WriteStringParquet(path, columns, rows)
	if err != nil {
		return RegisterInput{}, fmt.Errorf("extract_faers: write %s: %w", name, err)
	}
	return RegisterInput{
		Path: path, MediaType: ParquetMediaType, Rows: int64(n),
		SchemaFingerprint: SchemaFingerprint(columns), Inputs: inputIDs,
		Warnings: warnings, Operation: "extract_faers_" + name,
	}, nil
}

// --- lazy source inventory (no bulk content reads) -------------------------------

// faersSourceHandle is one logical FAERS ASCII file opened on demand - loose .txt or a zip
// member. Content is never buffered up front: ParseStream hashes + parses it in one pass.
type faersSourceHandle struct {
	Name     string
	Identity string
	InputID  string
	Open     func() (io.ReadCloser, error)
}

// inventoryFAERSQuarters scans inDir (recursively) for FAERS ASCII sources - loose .txt files
// and .txt members inside quarterly ASCII .zip archives (the shape the FAERS fetcher
// downloads) - and returns quarter -> lazy family handles. Nothing is read into memory here;
// each handle streams its content when opened. Deterministic: paths walk in sorted order.
func inventoryFAERSQuarters(inDir string, refs map[string]ArtifactRef) (map[string]map[string]faersSourceHandle, func(), error) {
	var txtPaths, zipPaths []string
	err := filepath.WalkDir(inDir, func(path string, d os.DirEntry, walkErr error) error {
		if walkErr != nil {
			return walkErr
		}
		if d.IsDir() {
			return nil
		}
		switch {
		case strings.EqualFold(filepath.Ext(path), ".txt"):
			txtPaths = append(txtPaths, path)
		case strings.EqualFold(filepath.Ext(path), ".zip"):
			zipPaths = append(zipPaths, path)
		}
		return nil
	})
	if err != nil {
		return nil, nil, err
	}
	sort.Strings(txtPaths)
	sort.Strings(zipPaths)
	out := map[string]map[string]faersSourceHandle{}
	var readers []*zip.ReadCloser
	closeAll := func() {
		for _, reader := range readers {
			_ = reader.Close()
		}
	}
	add := func(q, family string, handle faersSourceHandle) {
		if out[q] == nil {
			out[q] = map[string]faersSourceHandle{}
		}
		out[q][family] = handle
	}
	for _, path := range txtPaths {
		name := filepath.Base(path)
		family, quarter := faers.FamilyAndQuarter(name)
		if family == "" || quarter == "" {
			continue
		}
		add(quarter, family, faersSourceHandle{Name: name, Identity: name, InputID: refs[path].Blake3, Open: func() (io.ReadCloser, error) { return os.Open(path) }})
	}
	for _, path := range zipPaths {
		reader, err := zip.OpenReader(path)
		if err != nil {
			closeAll()
			return nil, nil, err
		}
		readers = append(readers, reader)
		for i, file := range reader.File {
			if file.FileInfo().IsDir() || !strings.EqualFold(filepath.Ext(file.Name), ".txt") {
				continue
			}
			name := filepath.Base(file.Name)
			family, quarter := faers.FamilyAndQuarter(name)
			if family == "" || quarter == "" {
				continue
			}
			add(quarter, family, faersSourceHandle{Name: name, Identity: fmt.Sprintf("%d:%s", i, file.Name), InputID: refs[path].Blake3, Open: file.Open})
		}
	}
	return out, closeAll, nil
}

// parseFAERSQuarter streams one quarter's family files into normalized Tables concurrently
// (bounded by the family count - at most six files, never the whole corpus at once). The
// inventory owns the lifetime of the open zip readers.
func parseFAERSQuarter(ctx context.Context, quarter string, handles map[string]faersSourceHandle, warn *faers.Warnings, slots chan struct{}) (map[string]*faers.Table, error) {
	var (
		mu       sync.Mutex
		families = map[string]*faers.Table{}
	)
	g, gctx := errgroup.WithContext(ctx)
	for family, h := range handles {
		family, h := family, h
		g.Go(func() error {
			select {
			case slots <- struct{}{}:
				defer func() { <-slots }()
			case <-gctx.Done():
				return gctx.Err()
			}
			rc, err := h.Open()
			if err != nil {
				return err
			}
			defer rc.Close()
			tbl := faers.ParseStream(rc, quarter, family, h.Name, warn)
			if tbl == nil || len(tbl.Rows) == 0 {
				return nil
			}
			mu.Lock()
			families[family] = tbl
			mu.Unlock()
			return nil
		})
	}
	if err := g.Wait(); err != nil {
		return nil, err
	}
	return families, nil
}

// --- batch source loading (kept for the parity tests + dakp-worker-style checks) ---------

// loadFAERSSources collects the FAERS ASCII sources from inDir: loose .txt files (pre-unpacked
// quarters) and .txt members inside quarterly ASCII .zip archives (the shape the FAERS fetcher
// downloads). Each kept source's basename resolves to a FAERS family + quarter; content is hashed
// for the b3-derived source_record_id. Sorted path order for determinism. Mirrors
// faers_ascii._iter_faers_sources (loose .txt or zip members).
//
// NOTE: this batch loader reads whole files into memory and is used ONLY by the parity tests as
// the canonical oracle; the production ExtractFAERS path uses inventoryFAERSQuarters above.
func loadFAERSSources(inDir string) ([]faers.Source, error) {
	var txtPaths, zipPaths []string
	err := filepath.WalkDir(inDir, func(path string, d os.DirEntry, walkErr error) error {
		if walkErr != nil {
			return walkErr
		}
		if d.IsDir() {
			return nil
		}
		switch {
		case strings.EqualFold(filepath.Ext(path), ".txt"):
			txtPaths = append(txtPaths, path)
		case strings.EqualFold(filepath.Ext(path), ".zip"):
			zipPaths = append(zipPaths, path)
		}
		return nil
	})
	if err != nil {
		return nil, err
	}
	sort.Strings(txtPaths)
	sort.Strings(zipPaths)

	var srcs []faers.Source
	for _, p := range txtPaths {
		src, ok, err := faersSourceFromLoose(p)
		if err != nil {
			return nil, err
		}
		if ok {
			srcs = append(srcs, src)
		}
	}
	for _, zp := range zipPaths {
		zipSrcs, err := faersSourcesFromZip(zp)
		if err != nil {
			return nil, err
		}
		srcs = append(srcs, zipSrcs...)
	}
	return srcs, nil
}

// faersSourceFromLoose loads one loose .txt file as a faers.Source (ok=false if its name does not
// resolve to a FAERS family + quarter).
func faersSourceFromLoose(path string) (faers.Source, bool, error) {
	name := filepath.Base(path)
	family, quarter := faers.FamilyAndQuarter(name)
	if family == "" || quarter == "" {
		return faers.Source{}, false, nil
	}
	content, err := os.ReadFile(path)
	if err != nil {
		return faers.Source{}, false, err
	}
	return faers.Source{
		Quarter: quarter, Family: family, Content: content,
		SourceName: name, SourceB3: blake3store.HashBytes(content),
	}, true, nil
}

// faersSourcesFromZip reads each .txt member of a quarterly ASCII zip directly (no disk unpack),
// keeping members whose basename resolves to a FAERS family + quarter.
func faersSourcesFromZip(zipPath string) ([]faers.Source, error) {
	reader, err := zip.OpenReader(zipPath)
	if err != nil {
		return nil, err
	}
	defer reader.Close()
	var srcs []faers.Source
	for _, file := range reader.File {
		if file.FileInfo().IsDir() || !strings.EqualFold(filepath.Ext(file.Name), ".txt") {
			continue
		}
		name := filepath.Base(file.Name)
		family, quarter := faers.FamilyAndQuarter(name)
		if family == "" || quarter == "" {
			continue
		}
		rc, err := file.Open()
		if err != nil {
			return nil, err
		}
		content, err := io.ReadAll(rc)
		rc.Close()
		if err != nil {
			return nil, err
		}
		srcs = append(srcs, faers.Source{
			Quarter: quarter, Family: family, Content: content,
			SourceName: name, SourceB3: blake3store.HashBytes(content),
		})
	}
	return srcs, nil
}
