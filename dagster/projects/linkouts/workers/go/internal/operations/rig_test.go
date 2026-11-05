package operations

import (
	"bytes"
	"path/filepath"
	"runtime"
	"strings"
	"testing"
)

// fixtureEdges locates the committed DAKP edges fixture from this test file's own path, so the
// test does not depend on the directory `go test` is run from.
func fixtureEdges(t *testing.T) string {
	t.Helper()
	_, here, _, ok := runtime.Caller(0)
	if !ok {
		t.Fatal("cannot locate test file")
	}
	return filepath.Join(filepath.Dir(here), "..", "..", "..", "..", "fixtures", "kgx", "edges.ndjson")
}

func rigStarter(t *testing.T) string {
	t.Helper()
	var buf bytes.Buffer
	if _, err := InspectKGX(fixtureEdges(t), RigOptions{EmitConfig: true}, &buf); err != nil {
		t.Fatalf("InspectKGX config: %v", err)
	}
	return buf.String()
}

// The starter must be in the grammar the web app loads. A previous version emitted a different
// schema (predicates/fields/version_scope) that the loader rejects, which meant every new KG
// started by hand-rewriting the generated file. The Elixir side validates the full schema in CI;
// this pins the shape from the Go side so a regression fails here first.
func TestRigStarterUsesTheDisplayGrammar(t *testing.T) {
	got := rigStarter(t)

	for _, want := range []string{
		`name: "TODO-kg"`,
		`edge: "This relationship states that {subject} {predicate_words} {object}."`,
		`"subject" => {:link, "subject_name", "subject"}`,
		`evidence: [`,
	} {
		if !strings.Contains(got, want) {
			t.Errorf("starter config is missing %q", want)
		}
	}
	for _, stale := range []string{"predicates:", "version_scope:", "fields:"} {
		if strings.Contains(got, stale) {
			t.Errorf("starter config still uses the old schema key %q, which the loader rejects", stale)
		}
	}
}

// Every evidence sentence is gated on presence, so a starter config never renders a sentence
// with a hole in it for a field a particular edge lacks.
func TestRigStarterGatesEveryRowOnPresence(t *testing.T) {
	for _, line := range strings.Split(rigStarter(t), "\n") {
		if strings.Contains(line, "%{value:") && !strings.Contains(line, "if: [{:present,") {
			t.Errorf("evidence sentence is not gated on presence: %s", strings.TrimSpace(line))
		}
	}
}

// The value form follows the data's shape: CURIE lists are linked, lists of maps read a named key,
// and fields the page already shows are not repeated as evidence.
func TestRigStarterPicksValueFormsFromTheData(t *testing.T) {
	got := rigStarter(t)

	cases := map[string]string{
		"publications": `{:list, "publications", {:link, "self", "self"}, ", "}`,
		"sources":      `{:list, "sources", "{resource_id}", ", "}`,
		"agent_type":   `{:humanize, "agent_type"}`,
	}
	for field, want := range cases {
		if !strings.Contains(got, want) {
			t.Errorf("%s: want value spec %s", field, want)
		}
	}
	for _, shown := range []string{"Subject:", "Object:", "Predicate:", "Id:"} {
		if strings.Contains(got, "value: \""+shown) {
			t.Errorf("%s is already on the page and should not be an evidence sentence", shown)
		}
	}
}

// A field name is data from someone else's file. It must not be able to break out of the string
// literal or trigger interpolation when the config is evaluated.
func TestElixirStringEscapesInterpolationAndQuotes(t *testing.T) {
	cases := map[string]string{
		`plain`:            `"plain"`,
		`has "quotes"`:     `"has \"quotes\""`,
		`#{System.halt()}`: `"\#{System.halt()}"`,
		`back\slash`:       `"back\\slash"`,
	}
	for in, want := range cases {
		if got := elixirString(in); got != want {
			t.Errorf("elixirString(%q) = %s, want %s", in, got, want)
		}
	}
}

func TestLooksLikeCURIE(t *testing.T) {
	yes := []any{"MONDO:0004979", "dailymed:561c51aa", "NDA020346", []any{"CHEBI:1"}}
	no := []any{"asthma", "two words: here", 12.0, []any{}, []any{"plain"}, "http://x.test/a:b"}

	for _, v := range yes {
		if !looksLikeCURIE(v) {
			t.Errorf("looksLikeCURIE(%#v) = false, want true", v)
		}
	}
	for _, v := range no {
		if looksLikeCURIE(v) {
			t.Errorf("looksLikeCURIE(%#v) = true, want false", v)
		}
	}
}
