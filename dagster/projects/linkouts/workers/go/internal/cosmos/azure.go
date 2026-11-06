package cosmos

import (
	"context"
	"encoding/json"
	"errors"
	"fmt"
	"net/http"
	"strconv"
	"strings"
	"time"

	"github.com/Azure/azure-sdk-for-go/sdk/azcore"
	"github.com/Azure/azure-sdk-for-go/sdk/azcore/policy"
	"github.com/Azure/azure-sdk-for-go/sdk/data/azcosmos"

	"github.com/glusman-team/graph-monorepo/dagster/projects/linkouts/workers/go/internal/config"
	"github.com/glusman-team/graph-monorepo/dagster/projects/linkouts/workers/go/internal/ratelimit"
)

// maxSDKRetries is raised well above the SDK default of 3 because a bulk load is exactly
// the workload that hits 429s, and the SDK's backoff already honours x-ms-retry-after-ms
// (ADR 0002). Retrying here too would double the wait and hide throttling from the budget.
// At the 90%-of-free-tier budget the account keeps only ~10% headroom for the web app, and
// Cosmos meters per second, so the two workloads collide at second boundaries; the retry
// budget is what lets a load ride those sub-second spikes out instead of aborting. A
// genuinely saturated account still fails loudly once the retries are spent.
const maxSDKRetries = 12

// DropAll re-creates the container after deleting it, and Cosmos can take a moment to release the
// name. These bound the wait: three tries over roughly six seconds, which is longer than the
// teardown has ever taken and short enough that a real failure is reported rather than hung on.
const (
	dropAllRetries    = 3
	dropAllRetryDelay = 2 * time.Second
)

// AzureStore is the Cosmos DB backend. Writes use the read-write key; the web app never
// touches this type and uses the read-only key over REST instead.
type AzureStore struct {
	cfg       config.Cosmos
	client    *azcosmos.Client
	container *azcosmos.ContainerClient
	budget    ratelimit.Budget
}

var _ Store = (*AzureStore)(nil)

// AzureConfig is the subset of config.Cosmos this package needs, kept as an alias so the
// constructor signature stays short.
type AzureConfig = config.Cosmos

// NewAzure builds a client. It performs no I/O: the first request is Provision or a read.
func NewAzure(cfg AzureConfig, budget ratelimit.Budget) (*AzureStore, error) {
	if !cfg.Ready() {
		return nil, errors.New("cosmos store needs an endpoint and a key: set COSMOS_PRIMARY_CONNECTION_STRING_RW (see .envrc.example)")
	}
	if budget == nil {
		budget = ratelimit.NewUnlimited()
	}
	cred, err := azcosmos.NewKeyCredential(cfg.Key)
	if err != nil {
		return nil, fmt.Errorf("cosmos key: %w", err)
	}
	client, err := azcosmos.NewClientWithKey(cfg.Endpoint, cred, &azcosmos.ClientOptions{
		ClientOptions: azcore.ClientOptions{
			// Retry is a value, not a pointer, in this azcore version.
			Retry: policy.RetryOptions{MaxRetries: maxSDKRetries},
		},
	})
	if err != nil {
		return nil, fmt.Errorf("cosmos client for %s: %w", cfg.Endpoint, err)
	}
	db, err := client.NewDatabase(cfg.Database)
	if err != nil {
		return nil, fmt.Errorf("database handle %s: %w", cfg.Database, err)
	}
	container, err := db.NewContainer(cfg.Container)
	if err != nil {
		return nil, fmt.Errorf("container handle %s/%s: %w", cfg.Database, cfg.Container, err)
	}
	return &AzureStore{cfg: cfg, client: client, container: container, budget: budget}, nil
}

// Provision creates the database and container if they are missing. Both calls treat 409 as
// success, so `linkouts init` is safe to re-run — and safe to run against an account that
// already has the container from a previous project.
//
// Throughput is manual at cfg.Throughput on the container. On the free tier the first
// 1000 RU/s is free, so 1000 here costs nothing and leaves the account's whole allowance
// to this container (ADR 0002).
func (s *AzureStore) Provision(ctx context.Context) error {
	throughput := azcosmos.NewManualThroughputProperties(s.cfg.Throughput)
	// v1.5.0 has no *IfNotExists variants; 409 is the "already exists" answer, and both
	// creates are therefore idempotent by construction. CreateDatabase is a method on the
	// client, CreateContainer on the database handle.
	_, err := s.client.CreateDatabase(ctx, azcosmos.DatabaseProperties{ID: s.cfg.Database},
		&azcosmos.CreateDatabaseOptions{ThroughputProperties: &throughput})
	if err != nil && !errors.Is(mapError(err), ErrConflict) {
		return fmt.Errorf("create database %s: %w", s.cfg.Database, mapError(err))
	}
	db, err := s.client.NewDatabase(s.cfg.Database)
	if err != nil {
		return fmt.Errorf("database handle %s: %w", s.cfg.Database, err)
	}
	// Indexing is off entirely: this workload is point reads by id only, which need no index,
	// and every indexed path is RU charged on every write. Cosmos rejects path lists when the
	// mode is none — the live account answered BadRequest, "IndexingPolicy Paths are not
	// allowed when indexing mode is set to 'none'." — so the policy carries no paths at all.
	indexing := &azcosmos.IndexingPolicy{
		Automatic:    false,
		IndexingMode: azcosmos.IndexingModeNone,
	}
	_, err = db.CreateContainer(ctx, azcosmos.ContainerProperties{
		ID:                     s.cfg.Container,
		PartitionKeyDefinition: azcosmos.PartitionKeyDefinition{Paths: []string{config.PartitionKeyPath}},
		IndexingPolicy:         indexing,
	}, nil)
	// No ThroughputProperties here: the container shares the database's provisioned throughput.
	// The free-tier account caps TOTAL account throughput at 1000 RU/s, so provisioning the
	// container separately doubled the ask and Cosmos refused. The worker and web rate limiters
	// already partition that 1000 in software (COSMOS_RU_BUDGET_CLI / _WEB).
	if err != nil && !errors.Is(mapError(err), ErrConflict) {
		return fmt.Errorf("create container %s: %w", s.cfg.Container, mapError(err))
	}
	return nil
}

// Read is a single point read: one partition key, one id, ~1 RU for a small document.
func (s *AzureStore) Read(ctx context.Context, id string) (Doc, error) {
	resp, err := s.container.ReadItem(ctx, azcosmos.NewPartitionKeyString(id), id, nil)
	s.charge(ctx, resp.RequestCharge)
	if err != nil {
		return Doc{}, mapError(err)
	}
	doc, err := decodeDoc(resp.Value)
	if err != nil {
		return Doc{}, fmt.Errorf("%s: %w", id, err)
	}
	doc.ETag = string(resp.ETag)
	return doc, nil
}

// Create writes a new document. ErrConflict means the id already exists, which the pipeline
// treats as "merge into the existing blob", not as failure.
func (s *AzureStore) Create(ctx context.Context, d Doc) error {
	payload, err := encodeDoc(d)
	if err != nil {
		return err
	}
	resp, err := s.container.CreateItem(ctx, azcosmos.NewPartitionKeyString(d.ID), payload, nil)
	s.charge(ctx, resp.RequestCharge)
	if err != nil {
		return mapError(err)
	}
	return nil
}

// Upsert writes whether or not the document exists. It costs more than Create on a miss and
// cannot detect a lost race, so the pipeline prefers Create-then-merge.
func (s *AzureStore) Upsert(ctx context.Context, d Doc) error {
	payload, err := encodeDoc(d)
	if err != nil {
		return err
	}
	resp, err := s.container.UpsertItem(ctx, azcosmos.NewPartitionKeyString(d.ID), payload, nil)
	s.charge(ctx, resp.RequestCharge)
	if err != nil {
		return mapError(err)
	}
	return nil
}

// Replace overwrites under an If-Match precondition. ErrPreconditionFailed means another
// writer moved the document; the caller must re-read, re-merge, and retry.
func (s *AzureStore) Replace(ctx context.Context, d Doc, etag string) error {
	payload, err := encodeDoc(d)
	if err != nil {
		return err
	}
	var opts *azcosmos.ItemOptions
	if etag != "" {
		match := azcore.ETag(etag)
		opts = &azcosmos.ItemOptions{IfMatchEtag: &match}
	}
	resp, err := s.container.ReplaceItem(ctx, azcosmos.NewPartitionKeyString(d.ID), d.ID, payload, opts)
	s.charge(ctx, resp.RequestCharge)
	if err != nil {
		return mapError(err)
	}
	return nil
}

// Delete removes one document. Deleting a document that is not there is a 404, which callers
// compare against ErrNotFound; purge treats it as already-done rather than as failure.
func (s *AzureStore) Delete(ctx context.Context, id string) error {
	resp, err := s.container.DeleteItem(ctx, azcosmos.NewPartitionKeyString(id), id, nil)
	s.charge(ctx, resp.RequestCharge)
	if err != nil {
		return mapError(err)
	}
	return nil
}

// All streams every document through a cross-partition query.
//
// This container's indexing policy is none, which is what makes its writes and reads cheap, and
// Cosmos serves a query out of the index — so the service is expected to refuse this with a
// BadRequest. The refusal is mapped to ErrScanUnsupported with the operator's way out in the
// message, rather than retried or worked around: the honest alternatives are DropAll (drop the
// container and reload) or temporarily switching the indexing policy to consistent.
func (s *AzureStore) All(ctx context.Context, fn func(Doc) error) error {
	pager := s.container.NewQueryItemsPager("SELECT * FROM c", azcosmos.NewPartitionKey(), nil)
	for pager.More() {
		resp, err := pager.NextPage(ctx)
		if err != nil {
			s.charge(ctx, resp.RequestCharge)
			return scanError(mapError(err))
		}
		s.charge(ctx, resp.RequestCharge)
		for _, raw := range resp.Items {
			doc, err := decodeDoc(raw)
			if err != nil {
				return err
			}
			if err := fn(doc); err != nil {
				return err
			}
		}
	}
	return nil
}

// scanError explains a refused scan instead of surfacing a raw BadRequest. A 400 here is almost
// always the indexing policy, and "indexing is off" is not something the message from the service
// says in so many words.
func scanError(err error) error {
	var rerr *azcore.ResponseError
	if errors.As(err, &rerr) && rerr.StatusCode == http.StatusBadRequest {
		return fmt.Errorf("%w: cosmos answered 400 (%s); this container's indexing policy is none, "+
			"so it cannot serve a scan - use the purge operation with scope=all and reload, or set the "+
			"indexing policy to consistent first", ErrScanUnsupported, rerr.ErrorCode)
	}
	return err
}

// DropOnly deletes the container and does NOT recreate it: the cleanup half of a
// blue/green cutover (`push` staged documents into the new container, flip the app, drop the
// old one here). Like DropAll's delete half, this is control plane and costs no RU.
func (s *AzureStore) DropOnly(ctx context.Context) error {
	if _, err := s.container.Delete(ctx, nil); err != nil {
		if mapped := mapError(err); !errors.Is(mapped, ErrNotFound) {
			return fmt.Errorf("delete container %s: %w", s.cfg.Container, mapped)
		}
	}
	return nil
}

// DropAll deletes the container and provisions it again with the same partition key and indexing
// policy. Dropping is immediate and free, which is why a wipe-and-reload uses it rather than
// deleting 130k documents one request at a time.
//
// Cosmos can still be tearing down the old container when the create arrives, and answers 409 for
// a name it has not finished releasing. That 409 is retried rather than accepted, because
// accepting it would leave the old container — and every document in it — in place while
// reporting success.
func (s *AzureStore) DropAll(ctx context.Context) error {
	if err := s.DropOnly(ctx); err != nil {
		return err
	}
	var last error
	for attempt := range dropAllRetries {
		if attempt > 0 {
			select {
			case <-ctx.Done():
				return ctx.Err()
			case <-time.After(dropAllRetryDelay):
			}
		}
		if err := s.Provision(ctx); err != nil {
			last = err
			continue
		}
		// A create that lands while the old container is still being released can succeed and then
		// leave the container unusable for a moment; one read confirms it is really back.
		if _, err := s.container.Read(ctx, nil); err == nil {
			return nil
		}
		last = errors.New("container was re-created but is not readable yet")
	}
	return fmt.Errorf("re-create container %s after drop: %w", s.cfg.Container, last)
}

// Stats reads the container's own metadata, which is where the service reports how many documents
// and how much storage it holds. This is a metadata read, not a scan, so it costs nothing
// meaningful and answers "did my load actually land" without opening the portal.
func (s *AzureStore) Stats(ctx context.Context) (StoreStats, error) {
	resp, err := s.container.Read(ctx, nil)
	if err != nil {
		return StoreStats{Items: -1}, mapError(err)
	}
	st := StoreStats{Items: -1}
	if resp.RawResponse != nil {
		st.Usage = resp.RawResponse.Header.Get("x-ms-resource-usage")
		st.Quota = resp.RawResponse.Header.Get("x-ms-resource-quota")
		st.Items = parseResourceCount(st.Usage)
	}
	st.Container = describeContainer(resp.ContainerProperties)
	return st, nil
}

// describeContainer renders the settings that decide what this container costs: the partition key
// every point read is addressed by, and the indexing policy that keeps writes at their minimum
// and index storage at zero.
func describeContainer(p *azcosmos.ContainerProperties) string {
	if p == nil {
		return ""
	}
	pk := strings.Join(p.PartitionKeyDefinition.Paths, ",")
	if pk == "" {
		pk = "(none)"
	}
	mode, automatic, included := "unreported", "", 0
	if ip := p.IndexingPolicy; ip != nil {
		mode = string(ip.IndexingMode)
		if mode == "" {
			mode = "consistent" // the service's default, which it omits rather than restates
		}
		if ip.Automatic {
			automatic = ", automatic"
		}
		included = len(ip.IncludedPaths)
	}
	ttl := ""
	if p.DefaultTimeToLive != nil {
		ttl = fmt.Sprintf(" - ttl %ds", *p.DefaultTimeToLive)
	}
	analytical := ""
	if p.AnalyticalStoreTimeToLiveInSeconds != nil {
		analytical = " - analytical store on"
	}
	return fmt.Sprintf("partition key %s - indexing %s%s - %d indexed path%s%s%s",
		pk, mode, automatic, included, plural(included), ttl, analytical)
}

func plural(n int) string {
	if n == 1 {
		return ""
	}
	return "s"
}

// parseResourceCount pulls the document count out of a resource-usage header
// ("documents=123;collections=1;partitionKeyRanges=4;"), returning -1 when it is absent.
func parseResourceCount(header string) int {
	for _, part := range strings.Split(header, ";") {
		key, value, ok := strings.Cut(strings.TrimSpace(part), "=")
		if ok && key == "documents" {
			if n, err := strconv.Atoi(value); err == nil {
				return n
			}
		}
	}
	return -1
}

// Name identifies the account and container in logs.
func (s *AzureStore) Name() string {
	return fmt.Sprintf("cosmos://%s/%s/%s", s.cfg.Endpoint, s.cfg.Database, s.cfg.Container)
}

// Close is a no-op: the SDK client holds no OS resources beyond the HTTP transport.
func (s *AzureStore) Close() error { return nil }

// charge debits the actual RU the service reported. Charges are debited even when the call
// failed, because Cosmos bills 409s and 412s too — ignoring them would let a run overshoot
// its budget during a merge storm.
func (s *AzureStore) charge(ctx context.Context, ru float32) {
	if ru <= 0 {
		return
	}
	// Errors here are only ever "context cancelled", and a cancelled context is already
	// failing the caller's loop, so the charge is recorded best-effort.
	_ = s.budget.Take(ctx, float64(ru))
}

// encodeDoc renders the stored shape exactly as ADR 0001 specifies: id, b, d only when a
// dictionary was used, and k only when the writer knew which knowledge graph the edge came from.
func encodeDoc(d Doc) ([]byte, error) {
	if d.ID == "" {
		return nil, errors.New("document has no id")
	}
	if d.Blob == "" {
		return nil, fmt.Errorf("%s: empty blob", d.ID)
	}
	payload := struct {
		ID     string `json:"id"`
		Blob   string `json:"b"`
		DictID uint32 `json:"d,omitempty"`
		KG     string `json:"k,omitempty"`
	}{d.ID, d.Blob, d.DictID, d.KG}
	b, err := json.Marshal(payload)
	if err != nil {
		return nil, fmt.Errorf("%s: %w", d.ID, err)
	}
	return b, nil
}

func decodeDoc(raw []byte) (Doc, error) {
	var payload struct {
		ID     string `json:"id"`
		Blob   string `json:"b"`
		DictID uint32 `json:"d"`
		KG     string `json:"k"`
	}
	if err := json.Unmarshal(raw, &payload); err != nil {
		return Doc{}, fmt.Errorf("decode stored document: %w", err)
	}
	if payload.ID == "" || payload.Blob == "" {
		return Doc{}, fmt.Errorf("stored document is missing id or b")
	}
	return Doc{ID: payload.ID, Blob: payload.Blob, DictID: payload.DictID, KG: payload.KG}, nil
}

// mapError translates an azcore.ResponseError into this package's sentinels so callers
// never import azcore and never match on status codes by hand.
func mapError(err error) error {
	if err == nil {
		return nil
	}
	var rerr *azcore.ResponseError
	if errors.As(err, &rerr) {
		switch rerr.StatusCode {
		case http.StatusNotFound:
			return fmt.Errorf("%w (%d)", ErrNotFound, rerr.StatusCode)
		case http.StatusConflict:
			return fmt.Errorf("%w (%d)", ErrConflict, rerr.StatusCode)
		case http.StatusPreconditionFailed:
			return fmt.Errorf("%w (%d)", ErrPreconditionFailed, rerr.StatusCode)
		case http.StatusTooManyRequests:
			return fmt.Errorf("%w after %d retries (retry-after %dms)", ErrThrottled, maxSDKRetries, retryAfterMs(rerr))
		}
	}
	return err
}

// retryAfterMs reads the throttle hint the service sent. It is reported in the error so a
// throttled run says how long to wait instead of just failing.
func retryAfterMs(rerr *azcore.ResponseError) int {
	if rerr.RawResponse == nil {
		return 0
	}
	for _, h := range []string{"x-ms-retry-after-ms", "retry-after-ms"} {
		if v := rerr.RawResponse.Header.Get(h); v != "" {
			if ms, err := strconv.Atoi(v); err == nil {
				return ms
			}
		}
	}
	return 0
}

// IsThrottled reports whether err is a 429 that outlasted the SDK's retries.
func IsThrottled(err error) bool { return errors.Is(err, ErrThrottled) }
