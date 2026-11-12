package pipeline

import (
	"regexp"
	"strings"
	"testing"
)

var b3IDRe = regexp.MustCompile(`^b3:[0-9a-f]{64}$`)

// TestSourceRecordIDMatchesPython locks parity with spl_xml._source_record_id. Vectors
// were computed with the Python implementation (hash_bytes over the \x1f-joined parts).
func TestSourceRecordIDMatchesPython(t *testing.T) {
	srcAB := "b3:" + strings.Repeat("ab", 32)
	src00 := "b3:" + strings.Repeat("00", 32)
	cases := []struct {
		name string
		src  string
		kind string
		keys []string
		want string
	}{
		{"section", srcAB, "section", []string{"set1", "34067-9"},
			"b3:1c31ed19f48e44c8f3ea85b64a15734026e34a4a855c28787aeeb44d0c1f39e4"},
		{"set", src00, "set", []string{"SOMESET"},
			"b3:649f830dd99f0ac0f18176db665fab924dd0e18c3462be5c015d73cdf8d1d4a8"},
		{"no_local_keys", src00, "set", nil,
			"b3:9192432b470b7a82adac56fa536c5a807bb650ceca213d64db0fd1cb8e234f23"},
	}
	for _, c := range cases {
		if got := SourceRecordID(c.src, c.kind, c.keys...); got != c.want {
			t.Errorf("%s: SourceRecordID = %q, want %q", c.name, got, c.want)
		}
	}
}

func TestSourceRecordIDDeterministicAndDistinct(t *testing.T) {
	src := "b3:" + strings.Repeat("cd", 32)
	a := SourceRecordID(src, "set", "K1")
	if a != SourceRecordID(src, "set", "K1") {
		t.Error("SourceRecordID is not deterministic")
	}
	if !b3IDRe.MatchString(a) {
		t.Errorf("not a b3 id: %q", a)
	}
	if a == SourceRecordID(src, "section", "K1") {
		t.Error("different kind must yield a different id")
	}
	if a == SourceRecordID(src, "set", "K2") {
		t.Error("different local key must yield a different id")
	}
}
