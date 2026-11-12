// Package pipeline holds the canonical source_record_id derivation shared by Go extractors.
package pipeline

import (
	"strings"

	"github.com/glusman-team/dakp/go/internal/blake3store"
)

// SourceRecordID derives a stable per-record b3:<hex> id = BLAKE3 over the \x1f-joined
// (source artifact id, kind, source-local keys). It mirrors spl_xml._source_record_id
// exactly, so Go and Python produce identical ids for the same inputs; joins across
// normalized tables stay stable and re-extraction stays idempotent. Source-specific
// derivations (e.g. FAERS/MEDI/Drugs@FDA string forms) are built on blake3store.HashBytes
// by the per-source extractors.
func SourceRecordID(sourceArtifactID, kind string, localKeys ...string) string {
	parts := make([]string, 0, 2+len(localKeys))
	parts = append(parts, sourceArtifactID, kind)
	parts = append(parts, localKeys...)
	return blake3store.HashBytes([]byte(strings.Join(parts, "\x1f")))
}
