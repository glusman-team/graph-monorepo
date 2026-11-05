// Package pipeline turns KGX files into stored edge documents. It owns the orchestration:
// streaming the join, merging versions into existing blobs, pacing RU spend, sampling ids for
// /random, and reporting progress. It knows nothing about Cosmos or ClickHouse beyond the
// Store and Engine interfaces, so the whole thing runs offline against fakes.
package pipeline

import (
	"bufio"
	"context"
	"errors"
	"fmt"
	"math/rand"
	"os"
	"strings"
	"sync"
	"time"

	"golang.org/x/sync/errgroup"

	"github.com/glusman-team/graph-monorepo/dagster/projects/linkouts/workers/go/internal/codec"
	"github.com/glusman-team/graph-monorepo/dagster/projects/linkouts/workers/go/internal/cosmos"
	"github.com/glusman-team/graph-monorepo/dagster/projects/linkouts/workers/go/internal/engine"
	"github.com/glusman-team/graph-monorepo/dagster/projects/linkouts/workers/go/internal/ratelimit"
	"github.com/glusman-team/graph-monorepo/dagster/projects/linkouts/workers/go/internal/version"
)

// Defaults for the knobs a caller usually leaves alone.
const (
	DefaultConcurrency = 8
	// DefaultSampleSize is the per-release /random reservoir cap. It was 4096 when one pool
	// document served the whole container; now that each release has its own pool document, the
	// cap is what a random read costs — Cosmos charges a point read by item size, and 1024 UUIDs
	// measure ~27 KB stored. 1024 distinct edges per release is far more variety than a person
	// clicking "random" can tell apart, and the web app caches the decoded pool, so the cold read
	// happens once per node per cache window.
	DefaultSampleSize = 1024
	// queueDepth decouples the join from the store: the engine can run ahead while workers
	// wait on network I/O, which is what keeps a 130k-edge load from serialising on latency.
	queueDepth = 256
	// maxPreconditionRetries bounds the read-modify-write loop. Two worker runs writing the same
	// edge concurrently is rare; looping forever on a hot document is worse than failing.
	maxPreconditionRetries = 3
	// maxThrottleRetries bounds the pipeline-level retry of a store call whose 429 outlasted
	// the SDK's own retries. The free-tier account is shared with the web app and Cosmos
	// meters RU per second, so second-boundary collisions are routine; aborting the run
	// instead would force a tail load to repay the whole filtered-prefix scan on restart.
	// Forty attempts of capped exponential backoff is roughly ten minutes per edge, long
	// enough to ride out real contention and short enough that a saturated account still
	// fails loudly.
	maxThrottleRetries = 40
)

// throttleBaseDelay seeds the throttle backoff; a var so tests can shrink it.
var throttleBaseDelay = 500 * time.Millisecond

// throttleBackoff grows exponentially from throttleBaseDelay, capped at 32x, with up to 25%
// jitter so sixteen workers do not wake from a collision in lockstep.
func throttleBackoff(attempt int) time.Duration {
	d := throttleBaseDelay << min(attempt, 5)
	return d + time.Duration(rand.Int63n(int64(d/4)+1))
}

// Options configures one load.
type Options struct {
	// Key is the "<kg>-<version>" this run stores, e.g. infores:drugapprovals-kp-1.11.2.
	Key string
	// BaseKey, when set, is the version new documents are diffed against. Empty means
	// "the newest version already stored for this edge".
	BaseKey string

	NodesPath string
	EdgesPath string

	Store  cosmos.Store
	Engine engine.Engine
	Budget ratelimit.Budget

	// Dict is the trained zstd dictionary, nil for plain compression. Every document read
	// back must have been written with the same dictionary id or it cannot be decoded.
	Dict      []byte
	ZstdLevel int

	// DryRun does everything except touch the store: no reads, no writes, no RU.
	DryRun bool
	// NoRepack skips documents that already carry this key instead of rewriting them, and
	// never re-encodes a blob just to change dictionary or level. Use it to resume a
	// partially completed load without paying to rewrite what already landed.
	NoRepack bool

	// IDsPath limits the load to the edges whose ids appear in this newline-delimited file:
	// the precise tail of a verified gap, so a resume pays nothing for the millions of edges
	// that are already stored. The engine still joins the whole file; excluded edges are
	// dropped before the store, so they cost no requests, no RU, and no pool slots.
	IDsPath string
	// SampleFullRelease offers every edge in the file to the /random reservoir, not only the
	// ones this run stores. It exists for an IDsPath tail run: the release's earlier bulk
	// load died before writing its pool, so the only run that finishes must sample for the
	// whole release or /random would draw only from the tail. The pool is written only on a
	// fully successful run, and a fully successful tail run means every file edge is stored.
	SampleFullRelease bool

	Concurrency int
	// SampleSize is the /random reservoir cap. Zero selects DefaultSampleSize unless
	// DisablePool explicitly requests the diagnostic no-pool mode.
	SampleSize  int
	DisablePool bool
	// SampleSeed fixes the reservoir's RNG. Zero (the default) means random. It is set only to
	// regenerate the committed contract fixtures reproducibly.
	SampleSeed int64
	// Now stamps the pool's sampled_at. Nil means time.Now. Fixed for the same reason as
	// SampleSeed.
	Now      func() time.Time
	Progress Progress

	// Threads caps ClickHouse parallelism for the join.
	Threads int

	// kg, versionLabel and slug are parsed out of Key by normalize: the canonical graph name
	// ("infores:drugapprovals-kp"), the release label ("1.16.0"), and the name as stored on
	// documents and used in URLs and pool document ids ("drugapprovals-kp").
	kg           string
	versionLabel string
	slug         string
}

func (o *Options) normalize() error {
	if o.Key == "" {
		return errors.New("a version key is required (for example infores:drugapprovals-kp-1.11.2)")
	}
	parsed, err := version.Parse(o.Key)
	if err != nil {
		return fmt.Errorf("version key %q: %w", o.Key, err)
	}
	// One parse here rather than per edge: the slug goes on every document and the label into the
	// pool index, and both must agree with what the web app derives from the same key.
	o.kg = parsed.KG
	o.versionLabel = parsed.Version()
	o.slug = version.Slug(parsed.KG)
	if o.Store == nil {
		return errors.New("no store configured")
	}
	if o.Engine == nil {
		return errors.New("no engine configured")
	}
	if o.NodesPath == "" || o.EdgesPath == "" {
		return errors.New("both --nodes and --edges are required")
	}
	if o.SampleFullRelease && o.IDsPath == "" {
		return errors.New("--sample-full-release only means something together with --ids")
	}
	if o.Budget == nil {
		o.Budget = ratelimit.NewUnlimited()
	}
	if o.Concurrency <= 0 {
		o.Concurrency = DefaultConcurrency
	}
	if o.Progress == nil {
		o.Progress = NopProgress{}
	}
	if o.SampleSize < 0 {
		return fmt.Errorf("sample size cannot be negative: %d", o.SampleSize)
	}
	if o.DisablePool && o.SampleSize > 0 {
		return errors.New("DisablePool cannot be combined with a positive sample size")
	}
	if !o.DisablePool && o.SampleSize == 0 {
		o.SampleSize = DefaultSampleSize
	}
	return nil
}

func (o Options) dictID() uint32 { return codec.DictID(o.Dict) }

// Stats is what a run reports. Every counter is updated from worker goroutines.
type Stats struct {
	Key   string
	Start time.Time
	End   time.Time

	mu sync.Mutex
	// Edges is the number of joined rows the engine produced.
	Edges int
	// Created, Merged, Skipped, Failed partition what happened to each document.
	Created int
	Merged  int
	Skipped int
	Failed  int
	// Filtered counts rows dropped by an --ids filter. On a tail load the filter swallows
	// the whole already-stored prefix, and without this counter the run looks wedged for
	// the twenty-plus minutes the join takes to reach the gap: zero edges, no progress.
	Filtered int
	// Throttled counts store calls that came back as a 429 which outlasted the SDK's own
	// retries and had to be backed off and retried here.
	Throttled int
	// Versions is how many versions the touched documents now hold in total.
	Versions int
	// RawBytes is canonical JSON size before compression; BlobBytes is base64 after.
	RawBytes  int64
	BlobBytes int64
	// RU is what the store reported spending, and Waits is how often pacing blocked.
	RU    float64
	Waits int64
	// Sampled is the size of the /random pool written.
	Sampled int

	sampler *reservoir
}

func (s *Stats) bump(fn func()) {
	s.mu.Lock()
	fn()
	s.mu.Unlock()
}

// Snapshot copies the counters into a plain value that is safe to pass to a Progress sink and
// format without holding the lock. Stats itself contains a mutex, so it must never be copied.
func (s *Stats) Snapshot() Snapshot {
	s.mu.Lock()
	defer s.mu.Unlock()
	snap := Snapshot{
		Key:       s.Key,
		Elapsed:   s.elapsedLocked(),
		Edges:     s.Edges,
		Created:   s.Created,
		Merged:    s.Merged,
		Skipped:   s.Skipped,
		Failed:    s.Failed,
		Filtered:  s.Filtered,
		Throttled: s.Throttled,
		Versions:  s.Versions,
		RawBytes:  s.RawBytes,
		BlobBytes: s.BlobBytes,
		RU:        s.RU,
		Waits:     s.Waits,
	}
	if s.sampler != nil {
		snap.Sampled = s.sampler.len()
		snap.Offered = s.sampler.offered()
	}
	return snap
}

// Snapshot is a point-in-time copy of the run counters.
type Snapshot struct {
	Key     string
	Elapsed time.Duration
	Edges   int
	Created int
	Merged  int
	Skipped int
	// Filtered counts rows dropped by an --ids filter. On a tail load the filter swallows
	// the whole already-stored prefix, and without this counter the run looks wedged for
	// the twenty-plus minutes the join takes to reach the gap: zero edges, no progress.
	Filtered int
	Failed   int
	// Throttled counts store calls retried after a 429 outlasted the SDK's retries.
	Throttled int
	Versions  int
	RawBytes  int64
	BlobBytes int64
	RU        float64
	Waits     int64
	Sampled   int
	Offered   int64
}

// Ratio is the compressed-to-raw size ratio, which is the number that says whether the trained
// dictionary is doing its job.
func (s Snapshot) Ratio() float64 {
	if s.RawBytes == 0 {
		return 0
	}
	return float64(s.BlobBytes) / float64(s.RawBytes)
}

// Rate is edges per second so far.
func (s Snapshot) Rate() float64 {
	if secs := s.Elapsed.Seconds(); secs > 0 {
		return float64(s.Edges) / secs
	}
	return 0
}

// Ratio reports the compressed-to-raw size ratio on the live counters.
func (s *Stats) Ratio() float64 { return s.Snapshot().Ratio() }

// Duration is the wall time of the run.
func (s *Stats) Duration() time.Duration {
	s.mu.Lock()
	defer s.mu.Unlock()
	return s.elapsedLocked()
}

// elapsedLocked is Duration for callers already holding the lock.
func (s *Stats) elapsedLocked() time.Duration {
	if s.End.IsZero() {
		return time.Since(s.Start)
	}
	return s.End.Sub(s.Start)
}

// Load runs one ingest. It streams: memory stays flat regardless of input size, and the first
// document is written before the last one is read.
func Load(ctx context.Context, o Options) (*Stats, error) {
	if err := o.normalize(); err != nil {
		return nil, err
	}
	st := &Stats{Key: o.Key, Start: time.Now()}
	if o.SampleSize > 0 && !o.DryRun {
		st.sampler = newReservoir(o.SampleSize, o.SampleSeed)
	}

	rows := make(chan engine.Row, queueDepth)
	g, gctx := errgroup.WithContext(ctx)

	// idFilter, when set, is the verified gap: every edge outside it is already stored and
	// is dropped here, before the channel, so no worker ever pays a request for it.
	var idFilter map[string]struct{}
	if o.IDsPath != "" {
		var err error
		idFilter, err = loadIDSet(o.IDsPath)
		if err != nil {
			return nil, err
		}
	}

	reporter := newReporter(o.Progress, st, func() float64 { return o.Budget.Consumed() })

	g.Go(func() error {
		defer close(rows)
		return o.Engine.Join(gctx, engine.Query{
			NodesPath: o.NodesPath,
			EdgesPath: o.EdgesPath,
			Threads:   o.Threads,
		}, func(r engine.Row) error {
			if o.SampleFullRelease && st.sampler != nil {
				st.sampler.offer(r.ID)
			}
			if idFilter != nil {
				if _, keep := idFilter[r.ID]; !keep {
					st.bump(func() { st.Filtered++ })
					// The reporter is poked per processed document in the worker loop, which an
					// --ids filter starves: poke it here too, or a tail load prints nothing
					// until the join reaches the gap.
					reporter.maybe(gctx)
					return nil
				}
			}
			select {
			case rows <- r:
				return nil
			case <-gctx.Done():
				return gctx.Err()
			}
		})
	})

	for range o.Concurrency {
		g.Go(func() error {
			for {
				select {
				case <-gctx.Done():
					return gctx.Err()
				case r, ok := <-rows:
					if !ok {
						return nil
					}
					if err := process(gctx, &o, st, r); err != nil {
						st.bump(func() { st.Failed++ })
						return fmt.Errorf("edge %s: %w", r.ID, err)
					}
					reporter.maybe(gctx)
				}
			}
		})
	}

	err := g.Wait()
	reporter.final()
	st.End = time.Now()
	st.RU = o.Budget.Consumed()
	st.Waits = o.Budget.Waits()
	if err != nil {
		return st, err
	}
	if st.Edges == 0 {
		return st, fmt.Errorf("no edges were loaded from %s", o.EdgesPath)
	}
	// The parent ctx, not gctx: errgroup.Wait has canceled gctx by the time we get here, so a
	// context-honouring store (cosmos) would reject the pool write with "context canceled" after
	// a fully successful run. The file and mem stores ignore ctx, which is why only the live
	// account ever saw this.
	if err := writePool(ctx, &o, st); err != nil {
		return st, err
	}
	return st, nil
}

// process handles one joined edge: build the document, then create it or merge into whatever
// is already stored.
func process(ctx context.Context, o *Options, st *Stats, r engine.Row) error {
	doc, err := engine.MergedDoc(r)
	if err != nil {
		return err
	}
	raw, err := codec.Marshal(doc)
	if err != nil {
		return err
	}
	blob, err := codec.NewBlob(o.Key, doc)
	if err != nil {
		return fmt.Errorf("build blob: %w", err)
	}
	encoded, err := blob.Encode(o.Dict, o.ZstdLevel)
	if err != nil {
		return fmt.Errorf("encode blob: %w", err)
	}

	st.bump(func() {
		st.Edges++
		st.RawBytes += int64(len(raw))
		st.BlobBytes += int64(len(encoded))
	})
	// With SampleFullRelease the reservoir was offered every edge in the emit wrapper, so
	// the pool can cover the whole release; offering again here would double the tail's
	// weight and bias /random toward exactly the edges this run stored.
	if st.sampler != nil && !o.SampleFullRelease {
		st.sampler.offer(r.ID)
	}
	if o.DryRun {
		st.bump(func() { st.Created++ })
		return nil
	}

	// Create first: on a fresh load this is one request per edge instead of a read plus a
	// write, and a 409 is the signal that this edge already has versions to merge into. The
	// whole store phase retries on ErrThrottled: the retry is safe because Create is
	// idempotent (a response lost after a server-side success comes back 409 and routes into
	// merge) and merge already re-reads on an etag mismatch.
	for attempt := 0; ; attempt++ {
		err = o.Store.Create(ctx, cosmos.Doc{ID: r.ID, Blob: encoded, DictID: o.dictID(), KG: o.slug})
		switch {
		case err == nil:
			st.bump(func() { st.Created++; st.Versions++ })
			return nil
		case errors.Is(err, cosmos.ErrConflict):
			if err = merge(ctx, o, st, r.ID, doc); err == nil {
				return nil
			}
		default:
			err = fmt.Errorf("create: %w", err)
		}
		if !cosmos.IsThrottled(err) || attempt >= maxThrottleRetries {
			return err
		}
		st.bump(func() { st.Throttled++ })
		select {
		case <-ctx.Done():
			return ctx.Err()
		case <-time.After(throttleBackoff(attempt)):
		}
	}
}

// merge adds this run's version to an existing document. It re-reads on an etag mismatch,
// because a concurrent writer means our copy of the blob is stale.
func merge(ctx context.Context, o *Options, st *Stats, id string, doc codec.Doc) error {
	for attempt := range maxPreconditionRetries + 1 {
		existing, err := o.Store.Read(ctx, id)
		if err != nil {
			return fmt.Errorf("read existing document: %w", err)
		}
		dict, err := DictionaryFor(existing.DictID, o.Dict)
		if err != nil {
			return err
		}
		blob, err := codec.DecodeBlob(existing.Blob, dict)
		if err != nil {
			return fmt.Errorf("decode stored blob: %w", err)
		}
		if _, present := blob.Versions[o.Key]; present && o.NoRepack {
			st.bump(func() { st.Skipped++; st.Versions += len(blob.Versions) })
			return nil
		}
		// The base must be chosen before this key is removed, and must never be this key:
		// reloading a version that is already stored would otherwise diff it against itself and
		// produce a delta pointing at a version that no longer exists.
		baseKey, baseDoc, err := chooseBase(blob, o)
		if err != nil {
			return err
		}
		// AddVersion refuses to overwrite a version that is already stored, so a re-run of the
		// same key replaces rather than duplicates.
		delete(blob.Versions, o.Key)
		if err := blob.AddVersion(o.Key, baseKey, doc, baseDoc); err != nil {
			return fmt.Errorf("add version: %w", err)
		}
		encoded, err := blob.Encode(o.Dict, o.ZstdLevel)
		if err != nil {
			return fmt.Errorf("re-encode blob: %w", err)
		}
		versions := len(blob.Versions)
		// The stored slug is kept rather than overwritten: a blob can hold versions of two graphs
		// when both assert the same edge UUID, and "the graph that created this document" is the
		// more useful fact than "the graph that touched it last".
		kg := existing.KG
		if kg == "" {
			kg = o.slug
		}
		err = o.Store.Replace(ctx, cosmos.Doc{ID: id, Blob: encoded, DictID: o.dictID(), KG: kg}, existing.ETag)
		switch {
		case err == nil:
			st.bump(func() {
				st.Merged++
				st.Versions += versions
			})
			return nil
		case errors.Is(err, cosmos.ErrPreconditionFailed) && attempt < maxPreconditionRetries:
			continue // someone else wrote it; re-read and try again
		case err != nil:
			return fmt.Errorf("replace: %w", err)
		}
	}
	return fmt.Errorf("%s: gave up after %d etag conflicts", id, maxPreconditionRetries)
}

// chooseBase picks the version a new delta is diffed against. Diffing against the newest
// stored version is what keeps deltas small: releases are incremental, so consecutive
// versions differ by a few fields while distant ones differ everywhere.
func chooseBase(blob *codec.Blob, o *Options) (string, codec.Doc, error) {
	if len(blob.Versions) == 0 {
		return "", nil, nil
	}
	key := o.BaseKey
	if key == "" {
		keys := make([]string, 0, len(blob.Versions))
		for k := range blob.Versions {
			if k == o.Key {
				continue // never diff a version against itself
			}
			keys = append(keys, k)
		}
		if len(keys) == 0 {
			// The only stored version is the one being rewritten: store it in full.
			return "", nil, nil
		}
		key = version.Newest(keys)
	}
	if _, ok := blob.Versions[key]; !ok {
		return "", nil, fmt.Errorf("base version %q is not stored in this document (have %v)", key, blob.VersionKeys())
	}
	doc, err := blob.Resolve(key)
	if err != nil {
		return "", nil, fmt.Errorf("resolve base version %q: %w", key, err)
	}
	return key, doc, nil
}

func (o *Options) now() time.Time {
	if o.Now != nil {
		return o.Now()
	}
	return time.Now()
}

// loadIDSet reads a newline-delimited id file into a membership set. Blank lines and
// surrounding whitespace are ignored; a file with no ids at all is almost certainly the
// wrong path, and an id that is simply absent from the edges file is dropped silently by
// the filter, which is the right behaviour when the file was cut from verified data.
func loadIDSet(path string) (map[string]struct{}, error) {
	f, err := os.Open(path)
	if err != nil {
		return nil, fmt.Errorf("open id file %s: %w", path, err)
	}
	defer func() { _ = f.Close() }()
	set := map[string]struct{}{}
	sc := bufio.NewScanner(f)
	sc.Buffer(make([]byte, 1<<20), 1<<20)
	for sc.Scan() {
		id := strings.TrimSpace(sc.Text())
		if id != "" {
			set[id] = struct{}{}
		}
	}
	if err := sc.Err(); err != nil {
		return nil, fmt.Errorf("read id file %s: %w", path, err)
	}
	if len(set) == 0 {
		return nil, fmt.Errorf("id file %s contains no ids", path)
	}
	return set, nil
}

// writePool stores this release's reservoir of edge ids at its own reserved document, then
// records the release in the pool index.
//
// One document per (kg, version) rather than one merged pool for everything: Cosmos charges a
// point read by item size, so a single pool would make the cheapest random cost as much as the
// priciest and would grow with every release loaded. Splitting also means a random in one release
// never reads another release's ids, and reloading one release cannot disturb the others.
func writePool(ctx context.Context, o *Options, st *Stats) error {
	if st.sampler == nil || o.DryRun {
		return nil
	}
	ids := st.sampler.snapshot()
	if len(ids) == 0 {
		return nil
	}
	sampledAt := o.now().UTC().Format(time.RFC3339)
	poolID := cosmos.PoolDocID(o.slug, o.versionLabel)

	// Merge with what an earlier load of this same key left, so re-running a release refills the
	// sample instead of replacing it. Only this key's pool is read: other releases are untouched.
	// Like the index, a pool this run cannot decode is a loud error rather than a silent refill.
	existing, err := o.Store.Read(ctx, poolID)
	switch {
	case errors.Is(err, cosmos.ErrNotFound):
	case err != nil:
		return fmt.Errorf("read random pool %s to merge the sample: %w", poolID, err)
	default:
		var old codec.Pool
		if _, derr := decodeReserved(existing, o.Dict, &old); derr != nil {
			return fmt.Errorf("random pool %s: %w", poolID, derr)
		}
		if old.Schema == codec.PoolSchema {
			merged := newReservoir(len(ids), o.SampleSeed)
			for _, id := range append(old.IDs, ids...) {
				merged.offer(id)
			}
			ids = merged.snapshot()
		}
	}

	pool := codec.Pool{
		Schema:    codec.PoolSchema,
		Key:       o.Key,
		SampledAt: sampledAt,
		IDs:       ids,
	}
	encoded, err := codec.EncodeJSON(pool, nil, o.ZstdLevel)
	if err != nil {
		return fmt.Errorf("encode random pool: %w", err)
	}
	doc := cosmos.Doc{ID: poolID, Blob: encoded}
	if err := o.Store.Upsert(ctx, doc); err != nil {
		return fmt.Errorf("store random pool %s: %w", poolID, err)
	}
	st.bump(func() { st.Sampled = len(ids) })

	return writePoolIndex(ctx, o, st, len(ids), sampledAt)
}

// writePoolIndex records this release in the reserved index document, preserving every other
// graph and release already listed.
//
// The index carries counts only — the ids stay in the per-release pool documents — so it remains
// well under a kilobyte however many graphs are loaded, and the one read every random route makes
// first stays the cheapest read in the system. The weight it records is the release's true edge
// count, which is what lets the app pick uniformly across releases of different sizes without
// reading any of their ids.
func writePoolIndex(ctx context.Context, o *Options, st *Stats, sampled int, sampledAt string) error {
	index := codec.PoolIndex{Schema: codec.PoolIndexSchema, KGs: map[string]codec.KGPool{}}
	// A leftover pool/1 document at this id — the pre-per-release format, which held a flat id
	// list — decodes into an empty index and is replaced. That is the migration: the reload that
	// writes this index is the same one that wipes the old documents.
	//
	// Every other failure must not fall through to the fresh index above: a failed read, or an
	// index this run cannot decode (written with a dictionary this run does not carry), would
	// otherwise silently drop every other graph's entry and clobber the shared document — the
	// failure that twice erased the index to one graph at the end of an otherwise successful
	// load. ErrNotFound is the only error that means "nothing to preserve"; anything else fails
	// the load loudly, and a rerun (idempotent, --no-repack) reaches this point again.
	existing, err := o.Store.Read(ctx, cosmos.RandomPoolID)
	switch {
	case errors.Is(err, cosmos.ErrNotFound):
	case err != nil:
		return fmt.Errorf("read pool index to preserve other graphs: %w", err)
	default:
		if _, derr := decodeReserved(existing, o.Dict, &index); derr != nil {
			return fmt.Errorf("pool index: %w", derr)
		}
		if index.Schema != codec.PoolIndexSchema {
			index = codec.PoolIndex{Schema: codec.PoolIndexSchema, KGs: map[string]codec.KGPool{}}
		}
	}
	index.Set(o.slug, o.versionLabel, st.sampler.offered(), sampled, sampledAt)

	encoded, err := codec.EncodeJSON(index, nil, o.ZstdLevel)
	if err != nil {
		return fmt.Errorf("encode pool index: %w", err)
	}
	doc := cosmos.Doc{ID: cosmos.RandomPoolID, Blob: encoded}
	if err := o.Store.Upsert(ctx, doc); err != nil {
		return fmt.Errorf("store pool index: %w", err)
	}
	return nil
}

// decodeReserved decodes a reserved document (the pool index, a release pool) for the
// read-modify-write path. Reserved documents are shared across graphs and are therefore
// written WITHOUT a dictionary, so any load can read them regardless of the --dict it
// compresses its own edges with; a stored dictionary id of 0 means exactly that. A document
// written with a dictionary this run does not carry is a loud error, never a silent empty
// decode: that path is what clobbered the shared index to whichever graph loaded last.
// A document that decodes but carries a foreign schema (the pre-per-release formats) is the
// documented migration and returns ok=false: the caller starts fresh and rewrites it.
func decodeReserved(doc cosmos.Doc, current []byte, into any) (ok bool, err error) {
	var dict []byte
	switch stored := doc.DictID; {
	case stored == 0:
	case stored == codec.DictID(current):
		dict = current
	default:
		return false, fmt.Errorf("written with dictionary %#x but this run carries %#x; pass that dictionary or repair the document", stored, codec.DictID(current))
	}
	if derr := codec.DecodeJSON(doc.Blob, dict, into); derr != nil {
		return false, nil // legacy or unreadable shape: the documented replace-on-migration path
	}
	return true, nil
}
