---
title: "ADR 2: Verified API surface"
description: "Pinned Azure Cosmos, chdb-go, and Cosmos REST signing behavior."
sidebar:
  order: 2
---

Status: accepted. Every fact here was read from the tagged module source or executed
against the real engine/service this session; nothing is recalled from memory. Where a
fact could not be confirmed it is flagged.

These are the signatures the Go worker and the web client are written against. If a dependency
is upgraded, re-verify this file before trusting it.

## 1. Go: `github.com/Azure/azure-sdk-for-go/sdk/data/azcosmos` v1.5.0

Client construction (connection string is what `~/.envrc` exports):

```go
client, err := azcosmos.NewClientFromConnectionString(connStr, nil)
// also available: NewClientWithKey(endpoint string, cred azcosmos.KeyCredential, o *ClientOptions)
```

`v1.5.0`'s `go.mod` requires `go 1.25.0`; this repo builds with Go 1.26.7.

Idempotent provisioning. Both create calls take throughput in their options, and 409 means
"already exists", which `init` treats as success:

```go
throughput := azcosmos.NewManualThroughputProperties(1000)   // free tier allows up to 1000 RU/s
db := client.NewDatabase(dbID)
_, err := client.CreateDatabase(ctx, azcosmos.DatabaseProperties{ID: dbID},
    &azcosmos.CreateDatabaseOptions{ThroughputProperties: &throughput})          // shared throughput
cont := db.NewContainer(containerID)
_, err = db.CreateContainer(ctx, azcosmos.ContainerProperties{
    ID:                     containerID,
    PartitionKeyDefinition: azcosmos.PartitionKeyDefinition{Paths: []string{"/id"}},
    IndexingPolicy: &azcosmos.IndexingPolicy{
        ExcludedPaths: []azcosmos.ExcludedPath{{Path: "/*"}},   // index nothing but id
    },
}, &azcosmos.CreateContainerOptions{ThroughputProperties: &throughput})

var rerr *azcore.ResponseError
if errors.As(err, &rerr) && rerr.StatusCode == http.StatusConflict { /* exists: no-op */ }
```

Free tier accepts either shape: one shared-throughput database (up to 25 containers sharing
1000 RU/s) or dedicated container throughput totalling <= 1000 RU/s.

Item operations. **The payload is `[]byte`, not a struct** - the Go worker marshals the canonical
JSON itself, which is what keeps the stored bytes byte-identical to the contract fixture:

```go
CreateItem(ctx, pk azcosmos.PartitionKey, item []byte, o *ItemOptions) (ItemResponse, error)
UpsertItem(ctx, pk, item []byte, o *ItemOptions) (ItemResponse, error)
ReplaceItem(ctx, pk, itemId string, item []byte, o *ItemOptions) (ItemResponse, error)
ReadItem(ctx, pk, itemId string, o *ItemOptions) (ItemResponse, error)
DeleteItem(ctx, pk, itemId string, o *ItemOptions) (ItemResponse, error)
pk := azcosmos.NewPartitionKeyString(uuid)   // also NewPartitionKeyNumber/Bool/NewPartitionKey
```

Optimistic concurrency and charges:

```go
type ItemOptions struct { /* ... */ IfMatchEtag *azcore.ETag; EnableContentResponseOnWrite bool }
// IfMatchEtag is sent as If-Match and is honoured on Upsert/Replace/Delete.
// It is *azcore.ETag, NOT *string.
resp.RequestCharge   // float32  -  the RU charge to feed the budgeter
resp.ETag            // azcore.ETag, from the ETag header (includes surrounding quotes)
resp.ActivityID
```

Status codes are surfaced as errors, never as return values:

```go
var rerr *azcore.ResponseError
errors.As(err, &rerr)
rerr.StatusCode == http.StatusNotFound          // 404
rerr.StatusCode == http.StatusConflict          // 409
rerr.StatusCode == http.StatusPreconditionFailed // 412 (ETag mismatch)
rerr.StatusCode == http.StatusTooManyRequests    // 429
```

429 handling. The azcore pipeline **already retries 429 by default**: `MaxRetries: 3`
(4 tries), retryable codes 408/429/500/502/503/504, exponential backoff from `RetryDelay`
800 ms capped at `MaxRetryDelay` 60 s, and the delay is taken from the response headers
(`retry-after-ms`, then `x-ms-retry-after-ms`, then `retry-after`). azcosmos's own
`cosmos_client_retry_policy.go` is region failover only and never matches 429.

Consequence for `internal/ratelimit`: **do not add a second retry loop on top.** Configure
the SDK's policy and let it own retry-after:

```go
&azcosmos.ClientOptions{ClientOptions: azcore.ClientOptions{
    Retry: &policy.RetryOptions{MaxRetries: 5},
}}
// per call: policy.WithRetryOptions(ctx, opts)
```

There is no `RetryAfter` field on the response in v1.5.0; read
`rerr.RawResponse.Header.Get("x-ms-retry-after-ms")` if the budgeter wants to account the
wait.

Flagged: `resp.ETag` on writes is inferred from header parsing, not exercised against the
live service (default `EnableContentResponseOnWrite` is false).

## 2. Elixir: Cosmos DB REST point read

No Erlang Cosmos SDK exists, and `Req` was rejected because it hard-depends on `jason`.
The read path is Finch + this signature.

String to sign (`{verb}\n{resourceType}\n{resourceLink}\n{date}\n\n` - note the final
empty line). Verb and resource type lowercased; `resourceLink` keeps IDs in their declared
casing; `date` is the `x-ms-date` value **lowercased**:

```
get
docs
dbs/{db}/colls/{coll}/docs/{doc_id}
thu, 27 apr 2017 00:51:12 gmt
<blank>
```

```elixir
date = DateTime.utc_now() |> Calendar.strftime("%a, %d %b %Y %H:%M:%S GMT") |> String.downcase()
payload = "get\ndocs\ndbs/#{db}/colls/#{coll}/docs/#{id}\n#{date}\n\n"
{:ok, key} = Base.decode64(master_key)
sig = Base.encode64(:crypto.mac(:hmac, :sha256, key, payload))
auth = URI.encode_www_form("type=master&ver=1.0&sig=#{sig}")   # escapes the WHOLE string
```

`{doc_id}` in the signature is the **raw** id, never its percent-encoded form - the request path
escapes the id, the payload does not. The two diverge on the reserved pool ids, whose colons the
URL must escape: signing the escaped form 401s exactly those reads and nothing else, which is how
it hid until the first live `/random` read a colon-bearing id (verified against the real account,
2026-10). `EdgeLinkouts.Cosmos.HTTP.doc_links/2` returns both forms side by side so they cannot
drift again.

Headers: `authorization: <auth>`, `x-ms-date: <the same date, RFC 1123 UTC>`,
`x-ms-version: 2020-11-05`, `x-ms-documentdb-partitionkey: ["<id>"]` (single-element JSON
array; our partition key is `/id` so the value is the document id), `accept: application/json`.

Response: RU charge in `x-ms-request-charge`; throttle is HTTP 429 with
`x-ms-retry-after-ms` (milliseconds). 404 = missing document.

**Known-answer vector** - published by Microsoft, recomputed independently this session and
matched exactly (it is the unit test for the signer):

```
verb         get
resourceType dbs
resourceLink dbs/ToDoList
date         Thu, 27 Apr 2017 00:51:12 GMT   (lowercased in the payload)
key          dsZQi3KtZmCv1ljt3VNWNm7sQUF1y5rJ  (+)
             fC6kv5JiwvW0EndXdDku/dkKBp8/ufDT  (+)
             oSxLzR4y+O/0H/t4bQtVNw==
expected     type%3dmaster%26ver%3d1.0%26sig%3dc09PEVJrgp2uQRkr934kFbTqhByc7TVr3OHyqlu%2bc%2bc%3d
```

The key is Microsoft's public sample and grants nothing, but it has the exact shape of a real
account key, so it is split across lines here and assembled at compile time in
`cosmos_http_test.exs`. Concatenate the three pieces to use it.

Percent-escape hex case is irrelevant (Microsoft's own C# and Node samples differ).
Flagged: `x-ms-version` - the REST docs' version table stops at 2018-12-31 and says the
latest is used when the header is omitted, while the official Go SDK sends `2020-11-05`.
Use `2020-11-05` for SDK parity.

## 3. Go: `github.com/chdb-io/chdb-go/v2` v2.2.0 + `lib/embedded` v0.260703.1

Bundled engine is ClickHouse **26.7.2.1** (live `SELECT version()`).

```go
import (
    "github.com/chdb-io/chdb-go/v2/chdb"
    _ "github.com/chdb-io/chdb-go/lib/embedded"   // blank import = use the embedded engine
)

sess, err := chdb.NewSession(":memory:")          // or "file:/abs/path"; ONE data path per process
defer sess.Close()
res, err := sess.Query(sql, "JSONEachRow")        // default output format is CSV
res.Buf(); res.String(); res.Error(); res.Free()

stream, err := sess.QueryStream(sql, "JSONEachRow")
for {
    chunk := stream.GetNext()      // nil == EOF; an empty chunk (RowsRead()==0) also means EOF
    if chunk == nil { break }
    if err := chunk.Error(); err != nil { return err }
    // chunk.Buf() is newline-delimited JSON for JSONEachRow
    chunk.Free()                   // optional; the next GetNext frees the previous chunk
}
stream.Free()                      // or Cancel(); required if you stop reading early
```

- CGO-free: `purego.Dlopen`, no `import "C"` anywhere in v2.2.0; `CGO_ENABLED=0 go build` passes.
- `chdb.Shutdown()` errors while any session is open ("cannot shut the engine down with 1 still open").
- Concurrency: one engine singleton per process; multiple sessions may share a path and run
  queries concurrently (`Session.Query` takes an RLock, `Close`/`Cleanup` wait for in-flight
  queries). **Stream objects are not goroutine-safe - one stream per goroutine.**
- `CHDB_CACHE_DIR` names the extraction root (default `os.UserCacheDir()/chdb-go`, then
  `$TMPDIR/chdb-go`); an explicit value failing is fatal, no fallthrough. Extracts
  `<root>/<sha256>/libchdb.so`, ~540 MiB, dir 0700 / file 0750. Root must be owned by the
  current user and not group/other-writable; every ancestor must be non-world-writable or sticky.
- **NixOS needs no workaround.** Contrary to the usual chdb folklore, this payload has no
  libstdc++ dependency: `DT_NEEDED` is only libpthread, libc.so.6, ld-linux-x86-64.so.2, libm,
  librt, libdl; max symbol version `GLIBC_2.4`; zero `GLIBCXX` strings (libstdc++ is statically
  linked). It loaded and ran on this NixOS host (glibc 2.42) with no `LD_LIBRARY_PATH`.
  Keep `CHDB_CACHE_DIR` pointed at persistent storage so the 540 MiB is not re-extracted.
- Any `go vet`/`go test` on a module importing `lib/embedded` needs the platform payloads
  (~363 MB of zips for all four platforms; linux-amd64 is 117,375,301 bytes) plus the ~540 MiB
  extraction - so CI caches the Go module cache and `_build`, and never re-downloads it.

### Query shapes that work on this engine

```sql
-- whole NDJSON lines into a JSON column: JSONAsObject, NOT JSONEachRow
SELECT j FROM file('/abs/path/edges.ndjson', 'JSONAsObject', 'j JSON')
-- JSONEachRow with structure 'j JSON' yields {} unless each line is wrapped as {"j": {...}}

j.subject::String            -- subcolumns are Dynamic; a missing path returns NULL
toJSONString(j)              -- null paths dropped, arrays of objects preserved,
                             -- keys emitted in ALPHABETICAL order (canonical for free)
mapFilter((k, v) -> ..., map(...))
jsonMergePatch(a, b)         -- KEEPS explicit nulls (not RFC 7396); cast ::JSON to fold them away
```

- `file()` paths resolve relative to the **process CWD** - always pass absolute paths.
- JSON type is production-ready here: `allow_experimental_json_type` and `enable_json_type`
  both default to 1, no flags needed.
- Numeric inference: `15` -> Int64, `15.0` and `1e2` -> Float64, and re-serialization is
  shortest-form, so `15.0` round-trips as `15`. Integer quoting in `toJSONString` output was
  unquoted here despite the docs' `output_format_json_quote_64bit_integers` note
  (engine-specific; verify before depending on it).
- `max_dynamic_paths` defaults to 1024, `max_dynamic_types` to 32; nested `Array(JSON)` uses
  reduced defaults (16/256).
- Keys containing dots collapse into nested paths unless `json_type_escape_dots_in_keys`
  (25.8+) is enabled; duplicate flattened paths need `type_json_skip_duplicated_paths`.
- `max_threads`: per query `... SETTINGS max_threads = N`, or per session `SET max_threads = N;`.

Unverified: darwin/windows behaviour, and no docs page for `JSONAsObject` was located (the
format itself is live-verified).
