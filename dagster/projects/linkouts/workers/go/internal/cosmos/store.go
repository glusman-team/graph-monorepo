// Package cosmos is the storage boundary. Everything above it talks to a Store; only the
// azure implementation knows about Cosmos DB, so the whole pipeline is testable offline and
// the web app can read the same documents from a file backend during development.
package cosmos

import (
	"context"
	"errors"
	"fmt"
	"strings"

	"github.com/glusman-team/graph-monorepo/dagster/projects/linkouts/workers/go/internal/ratelimit"
)

// Sentinel errors. Callers compare with errors.Is, never by string.
var (
	// ErrNotFound is a 404: no document with that id.
	ErrNotFound = errors.New("document not found")
	// ErrConflict is a 409: a create hit an existing id. The pipeline merges instead of
	// failing, because a create-first write against an id another KG version already owns
	// is the normal case, not an error.
	ErrConflict = errors.New("document already exists")
	// ErrPreconditionFailed is a 412: an If-Match etag no longer matches, i.e. someone else
	// wrote the document since it was read.
	ErrPreconditionFailed = errors.New("etag precondition failed")
	// ErrThrottled is a 429 that survived the SDK's own retries.
	ErrThrottled = errors.New("request throttled")
	// ErrScanUnsupported means the backend cannot enumerate its documents. Cosmos serves a scan
	// out of the index, and this container's indexing policy is none, so there is nothing to
	// scan with. Callers that need every document must use DropAll (and reload) instead.
	ErrScanUnsupported = errors.New("backend cannot scan documents")
)

// Doc is one Cosmos document: an edge UUID and every version of that edge, compressed into
// one base64 zstd frame. See ADR 0001 (wire format) on the documentation site.
type Doc struct {
	ID     string `json:"id"`
	Blob   string `json:"b"`
	DictID uint32 `json:"d,omitempty"`
	// KG is the slug of the knowledge graph that first stored this edge ("drugapprovals-kp" for
	// infores:drugapprovals-kp). No read path needs it — a filter against an unindexed container
	// would be a full scan, and the blob's version keys already carry the KG — so it is metadata:
	// it makes a document self-describing in Data Explorer and in an NDJSON file, and it is the
	// field an index would be added to if a query ever became worth its RU.
	//
	// A blob can hold versions of two KGs when both assert the same edge UUID. The first writer's
	// slug stays, because a document that says which graph created it is more useful than one that
	// says which graph touched it last.
	KG string `json:"k,omitempty"`
	// ETag is service state, never part of the stored JSON.
	ETag string `json:"-"`
}

// RandomPoolID is the reserved document id of the pool index: which knowledge graphs and which
// releases have a random pool, and how big each one is. Indexing is off on this container, so a
// random pick would be a full scan; the worker samples once per load and the app point-reads the
// result. The ids themselves live one document per release, at PoolDocID.
const RandomPoolID = "__random_pool__"

// PoolDocID is the reserved id of one release's sampled edge ids, e.g.
// "__random_pool__:drugapprovals-kp:1.16.0". One document per (kg, version) rather than one big
// document keeps every random a small point read: Cosmos charges a read by item size, so a
// single pool holding every release would make the cheapest random cost as much as the most
// expensive one, and would grow with every release.
//
// slug must not contain a colon (version.Slug drops the infores prefix) and version labels never
// do, so the id splits unambiguously at its last colon.
func PoolDocID(slug, version string) string {
	return RandomPoolID + ":" + slug + ":" + version
}

// SplitPoolDocID is the inverse of PoolDocID. ok is false for any id that is not a pool document,
// including the pool index itself.
func SplitPoolDocID(id string) (slug, version string, ok bool) {
	rest, found := strings.CutPrefix(id, RandomPoolID+":")
	if !found {
		return "", "", false
	}
	// Split at the last colon, not the first: a KG name that somehow kept a colon would
	// otherwise be truncated and the version mis-parsed.
	i := strings.LastIndex(rest, ":")
	if i <= 0 || i == len(rest)-1 {
		return "", "", false
	}
	return rest[:i], rest[i+1:], true
}

// IsReservedID reports whether an id belongs to the app rather than to an edge: the pool index
// and the per-release pool documents. The worker's status operation uses it to count edge documents without
// scanning, and purge uses it to leave the reserved documents out of an edge-only wipe.
func IsReservedID(id string) bool { return strings.HasPrefix(id, RandomPoolID) }

// StoreStats is what a backend can report about itself cheaply, without reading every document.
type StoreStats struct {
	// Items is the document count when the backend knows it for free. Negative means unknown.
	Items int
	// Usage and Quota are the service's x-ms-resource-usage / x-ms-resource-quota headers
	// verbatim ("documents=123;collections=1;partitionKeyRanges=4;"). Empty for backends that
	// have no such headers, which is every backend except Cosmos.
	Usage string
	Quota string
	// Container describes the backend's own configuration, when it has one worth reporting.
	// For Cosmos that is the partition key and the indexing policy — the two settings that make
	// every read in this system a point read, and the ones a portal check should confirm.
	Container string
}

// Store is the read/write surface the pipeline and the web app share.
type Store interface {
	// Read fetches one document by id, returning ErrNotFound when absent.
	Read(ctx context.Context, id string) (Doc, error)
	// Create writes a new document and returns ErrConflict if the id exists.
	Create(ctx context.Context, d Doc) error
	// Upsert writes the document whether or not it exists.
	Upsert(ctx context.Context, d Doc) error
	// Replace overwrites only when etag still matches; ErrPreconditionFailed otherwise.
	// An empty etag means "no precondition".
	Replace(ctx context.Context, d Doc, etag string) error
	// Provision creates the database and container if they are missing. It is idempotent.
	Provision(ctx context.Context) error
	// Delete removes one document by id, returning ErrNotFound when it is absent.
	Delete(ctx context.Context, id string) error
	// All streams every stored document to fn, stopping at the first error it returns. It is a
	// scan, so a Cosmos container with indexing off answers ErrScanUnsupported.
	All(ctx context.Context, fn func(Doc) error) error
	// DropAll removes every document at once. On Cosmos it drops and re-creates the container,
	// which is immediate and costs no RU; a file store truncates its file.
	DropAll(ctx context.Context) error
	// Stats reports cheap container facts for the worker's status operation.
	Stats(ctx context.Context) (StoreStats, error)
	// Name identifies the backend in logs and in --store output.
	Name() string
	// Close releases resources.
	Close() error
}

// Open builds a Store from a --store spec:
//
//	cosmos                       the configured Cosmos account (requires env, see internal/config)
//	file:/path/to/docs.ndjson    append-only NDJSON, no network, used by tests and local dev
//	mem://                       in-process, for tests that need to inject failures
//
// budget may be nil, which means unlimited.
func Open(ctx context.Context, spec string, cfg AzureConfig, budget ratelimit.Budget) (Store, error) {
	if budget == nil {
		budget = ratelimit.NewUnlimited()
	}
	switch {
	case spec == "cosmos":
		return NewAzure(cfg, budget)
	case spec == "":
		// An empty spec used to mean Cosmos. That made a zero-value globals{} in a test
		// resolve to the real account the moment credentials were in the environment, and
		// one such test dropped a live container. The flag default is the explicit string
		// "cosmos", so every real invocation is unaffected; only code that never chose a
		// store gets this error.
		return nil, errors.New("no --store spec given (want cosmos, file:<path>, or mem://)")
	case strings.HasPrefix(spec, "file:"):
		return OpenFile(strings.TrimPrefix(spec, "file:"), budget)
	case spec == "mem://" || spec == "mem":
		return NewFake(budget), nil
	default:
		return nil, fmt.Errorf("unknown --store spec %q (want cosmos, file:<path>, or mem://)", spec)
	}
}
