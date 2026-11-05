---
title: "ADR 1: Blob wire format"
description: "Canonical versioned documents shared by the Go writer and Elixir reader."
sidebar:
  order: 1
---

Status: accepted. Implemented in `dagster/projects/linkouts/workers/go/internal/codec`; checked against the Elixir reader by
`sites/linkouts/test/contract_test.exs` and the golden files in `dagster/projects/linkouts/contracts/`.

This is the only interface between the two languages. Both sides must agree **byte for
byte**, because the Go worker hashes canonical bytes to decide whether a version changed and the
web app re-encodes for KGX download.

## Cosmos document

```json
{
  "id": "575af3e8-8015-3718-be03-4da18a0bacfc",
  "b": "KLUv/QAMAdw...",
  "d": 1163284273,
  "k": "drugapprovals-kp"
}
```

- `id` - the edge UUID, taken verbatim from the KGX `id` field. It is also the partition
  key (`/id`) and the only path a read ever uses, so this document is reachable by exactly
  one point read and nothing else is queryable.
- `b` - base64 (standard alphabet, padded) of one zstd frame holding the blob JSON below.
- `d` - dictionary id the frame was compressed with; **omitted when 0** (no dictionary).
  Present so a reader holding the wrong dictionary fails loudly instead of returning
  garbage. Dictionaries are trained by the dictionary job and deployed under `sites/linkouts/priv/zstd/<slug>.<id>.dict`.
- `k` - the slug of the knowledge graph this edge belongs to: the canonical name with its
  `infores:` prefix dropped (`infores:drugapprovals-kp` -> `drugapprovals-kp`). One short
  field per document, outside the frame, so "which graph is this?" is answerable without
  decompressing anything, and so the same slug can be used for URLs and pool ids.
  **Omitted when empty**, which is what every document written before this field existed
  looks like: an old reader never sees a field it cannot interpret, and a new reader treats
  an absent `k` as "unattributed" rather than as a graph named `""`.

Deviation from the original `{id, b}` sketch (an untracked planning note): the `d`
field is added. A blob compressed
against a trained dictionary cannot be decoded without it, and there is no other place to
record which dictionary was in force when the document was written. The `k` field is added
for the same reason: the identity of the graph an edge came from is not recoverable from
the blob without decoding it, and the version key inside the frame is a string that has to
be parsed to get there.

## Reserved documents

Indexing is off, so anything that would need a query has to be a document that can be
point-read instead. Three reserved ids do that for `/random`; all of them use the same
envelope as an edge, with `b` holding JSON rather than a blob.

- `__random_pool__` - the **pool index**, `{"schema":"edgelinkouts.pool_index/1", "kgs":
{"drugapprovals-kp": {"versions": {"1.16.0": {"edges": 129807, "sampled": 1024,
"sampled_at": "2026-02-19T14:29:44Z"}}}}}`. Counts and no ids: which graphs exist, which
  releases each one has, how many edges each release holds and how many were sampled. One
  point read of this document is what the root page lists and what a weighted random pick
  runs on. Size grows with releases, not with edges.
- `__random_pool__:<slug>:<version>` - one release's **pool**,
  `{"schema":"edgelinkouts.pool/1", "key":"infores:drugapprovals-kp-1.16.0",
"sampled_at":"...", "ids":["...", ...]}`, the reservoir sample for that release alone. Ids are
  plain hex UUID strings. Binary and delta-varint packings were both measured on a 1024-id
  pool: packed-then-compressed is _larger_ than hex-then-compressed (36,896 B vs 30,902 B),
  because zstd already removes the repetition that packing removes, and it loses what makes
  the document readable in the portal. The storage lever for a pool is the sample cap
  (`sample_size`), not the encoding of one id.

A reserved id can never collide with an edge id: an edge id is a UUID, and every reserved id
starts with `__random_pool__` (`cosmos.IsReservedID` in Go, `Cosmos.reserved_id?/1` in
Elixir apply the same rule).

Superseded: a single flat `{"ids":[...]}` list at `__random_pool__` backed `/random` before
per-release pools existed. A document in that old shape is refused by schema, not
mis-decoded; the load that writes an index is the migration, and `linkouts_purge` with `scope: all`
followed by a reload is the explicit one.

## Blob JSON (inside the frame)

```json
{
  "schema": "edgelinkouts.blob/1",
  "versions": {
    "infores:drugapprovals-kp-1.11.2": {
      "id": "...",
      "subject": "CHEBI:64019",
      "predicate": "biolink:treats"
    },
    "infores:drugapprovals-kp-1.12.0": {
      "$t": "infores:drugapprovals-kp-1.11.2",
      "$set": { "clinical_approval_status": "approved_for_condition" },
      "$add": { "publications": ["PMID:40123456"] },
      "$del": ["number_of_cases"]
    }
  }
}
```

Deviation from that sketch: the version map is wrapped in an envelope carrying `schema`, so a
reader can refuse a format it does not implement rather than mis-decoding one. A reader
must reject any `schema` it does not know exactly.

Version keys are `<kg>-<version>`, opaque strings. The canonical `<kg>` is the infores the
graph is registered under - `infores:drugapprovals-kp-1.16.0`, not `drug-approvals-kg-1.16.0`

- because the registry name is the identifier a curator can check and the one every other
  Translator tool uses. Its **slug** (the name without the `infores:` prefix) is what the `k`
  field stores, what a URL carries (`/drugapprovals-kp/random`) and what a pool id is built
  from: `infores:` is a registry scheme, and a colon in a path segment or a document id buys
  nothing. Ordering for the UI is the KG's own release order, not lexicographic.

### Two payload shapes

A version payload is either

1. **full** - a JSON object with no `$`-prefixed keys: the complete edge document; or
2. **delta** - an object with `$t` naming the version it applies to, plus any of:
   - `$set`: object of keys whose value changed or is new here
   - `$add`: object mapping a field to the list elements **appended** to that field's list
   - `$del`: array of key names present in the base and absent here

Resolution: `resolve(v)` = the payload if full, else `apply(resolve($t), payload)`.
Application order is `$del`, then `$set`, then `$add`. A cycle is an error, not a loop.

`$add` exists for KGX `publications` lists, which grow between releases: one edge in the
DAKP fixtures carries 3158 PMIDs, so appending one element instead of re-storing the list
is the single largest saving in this workload. `$add` is only emitted when the base list is
a strict prefix of the new one - reordering or shrinking falls back to `$set`.

An **empty but present** `$set` (`{"$t":"v1","$set":{}}`) means "identical to the base" and
is the cheapest legal payload. A delta with none of the three keys is corrupt and must be
rejected. `null` vs `{}` therefore carries meaning; a reader must not normalize one into
the other.

Whether a version is stored full or as a delta is decided by comparing canonical byte sizes
of both candidates and keeping the smaller - not by counting changed keys.

## Canonical JSON

The bytes inside the frame are canonical, and canonical means:

1. Object keys sorted by byte order (UTF-8 code unit order) at **every** depth.
2. No insignificant whitespace; separators are `,` and `:`.
3. **No nulls anywhere.** A null at any depth is a contract violation and must be rejected
   on read as well as never written. Absence is expressed by omitting the key (`$del` for a
   removal). Nullish values - `null`, `""`, `"None"`, `"NULL"`, `"nan"`, `"N/A"`, `"-"`,
   `"unknown"`, `"not provided"`, empty arrays, empty objects - are stripped by the join
   before encoding.
4. Numbers keep their input text. `15.0` stays `15.0`, `9007199254740993` stays exact (no
   float64 round trip). Integers are never quoted.
5. `<`, `>`, `&` are **not** escaped. Go's `encoding/json` escapes them by default
   (`\u003c`); Elixir's `JSON` does not. HTML escaping must be off on the Go side
   (`sonic.Config{EscapeHTML: false}`) or the two languages disagree on bytes.
6. Strings are UTF-8, with the JSON-mandatory escapes only (`"`, `\`, and U+0000-U+001F).

Go implementation: `sonic.Config{SortMapKeys: true, UseNumber: true, CopyString: true,
EscapeHTML: false}.Froze()`. Elixir implementation must sort keys explicitly - do not rely
on map iteration order.

Note that ClickHouse's `toJSONString` also sorts keys and drops null paths, but it
re-serializes numbers to shortest form (`15.0` -> `15`) and its 64-bit integer quoting is
engine-specific. The engine output is therefore always re-parsed and re-encoded by
`codec.Marshal` before it reaches a blob, so the wire format does not depend on the engine
version.

## Compression

- Algorithm: zstd, one frame per document, level 3 by default (`zstd_level`).
- Dictionary: trained from the KG's own documents via `zstd.BuildDict`, id
  `0x454C4F31` ("ELO1"). Small JSON documents are mostly repeated key names, so a trained
  dictionary is what takes a ~250-byte document to ~25 bytes (measured).
- Training detail that bites: `BuildDict` needs `History` **and** `Contents`. If the history
  buffer covers the samples, every sample compresses to pure matches and the builder aborts
  with `0 literals found`. History is therefore capped at 1/8 of total sample bytes
  (and at 128 KiB).
- Base64 uses the standard alphabet with padding, so the stored string is safe in JSON and
  in a URL.

## What the reader must tolerate

- A version key it has no display configuration for: render raw KGX, do not 500.
- Fields renamed between KG releases (measured in DAKP: `FDA_regulatory_approvals` ->
  `regulatory_approvals`), which is why display configs carry version-scoped aliases.
- `d` absent (no dictionary) as well as present.
- A blob whose `schema` it does not know: refuse with a clear error.
