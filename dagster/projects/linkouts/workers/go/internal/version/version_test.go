package version

import (
	"testing"
)

func TestParseSplitsKGFromVersion(t *testing.T) {
	cases := []struct {
		in, kg, version string
		parts           []int
	}{
		{"drug-approvals-kg-1.11.2", "drug-approvals-kg", "1.11.2", []int{1, 11, 2}},
		{"multiomics-kg-1.12.0", "multiomics-kg", "1.12.0", []int{1, 12, 0}},
		{"wellness_kg-2.0.1", "wellness_kg", "2.0.1", []int{2, 0, 1}},
		{"kg-1.16.0", "kg", "1.16.0", []int{1, 16, 0}},
	}
	for _, c := range cases {
		got, err := Parse(c.in)
		if err != nil {
			t.Fatalf("Parse(%q): %v", c.in, err)
		}
		if got.KG != c.kg {
			t.Errorf("Parse(%q).KG = %q, want %q", c.in, got.KG, c.kg)
		}
		if v := got.Version(); v != c.version {
			t.Errorf("Parse(%q).Version() = %q, want %q", c.in, v, c.version)
		}
		if len(got.Parts) != len(c.parts) {
			t.Errorf("Parse(%q) parts = %v, want %v", c.in, got.Parts, c.parts)
			continue
		}
		for i, p := range c.parts {
			if got.Parts[i] != p {
				t.Errorf("Parse(%q) part %d = %d, want %d", c.in, i, got.Parts[i], p)
			}
		}
	}
}

func TestParsePreReleaseAndOddKeys(t *testing.T) {
	k, err := Parse("kg-1.0.0-rc1")
	if err != nil {
		t.Fatalf("Parse: %v", err)
	}
	if k.KG != "kg" || k.Suffix != "-rc1" {
		t.Errorf("kg=%q suffix=%q, want kg and -rc1", k.KG, k.Suffix)
	}
	if v := k.Version(); v != "1.0.0-rc1" {
		t.Errorf("Version() = %q", v)
	}

	// No numeric segment at all: still parseable, still orderable, never a panic.
	odd, err := Parse("wellness_kg")
	if err != nil {
		t.Fatalf("Parse(wellness_kg): %v", err)
	}
	if odd.KG != "wellness_kg" {
		t.Errorf("KG = %q", odd.KG)
	}
	if _, err := Parse(""); err == nil {
		t.Error("an empty key was accepted")
	}
}

func TestCompareOrdersNumericallyNotLexically(t *testing.T) {
	// The whole point: lexicographic order says 1.9.0 > 1.16.0, which would make the worker
	// diff a new release against the wrong base and the UI list versions backwards.
	if got := CompareKeys("kg-1.9.0", "kg-1.16.0"); got >= 0 {
		t.Errorf("Compare(kg-1.9.0, kg-1.16.0) = %d, want negative", got)
	}
	if got := CompareKeys("kg-1.11.2", "kg-1.16.0"); got >= 0 {
		t.Errorf("Compare(kg-1.11.2, kg-1.16.0) = %d, want negative", got)
	}
	if got := CompareKeys("kg-2.0.0", "kg-10.0.0"); got >= 0 {
		t.Errorf("Compare(kg-2.0.0, kg-10.0.0) = %d, want negative", got)
	}
	if got := CompareKeys("kg-1.0.0", "kg-1.0.0"); got != 0 {
		t.Errorf("Compare of equal keys = %d, want 0", got)
	}
	// Different KGs sort by name, so keys from two graphs never interleave.
	if got := CompareKeys("a-kg-9.9.9", "b-kg-0.0.1"); got >= 0 {
		t.Errorf("Compare across KGs = %d, want negative", got)
	}
	// A pre-release precedes its release.
	if got := CompareKeys("kg-1.0.0-rc1", "kg-1.0.0"); got >= 0 {
		t.Errorf("Compare(rc1, release) = %d, want negative", got)
	}
	// A missing trailing segment reads as zero, so 1.2 == 1.2.0.
	if got := CompareKeys("kg-1.2", "kg-1.2.0"); got != 0 {
		t.Errorf("Compare(kg-1.2, kg-1.2.0) = %d, want 0", got)
	}
}

func TestNewest(t *testing.T) {
	in := []string{"drug-approvals-kg-1.16.0", "drug-approvals-kg-1.9.0", "drug-approvals-kg-1.11.2"}
	if got := Newest(in); got != "drug-approvals-kg-1.16.0" {
		t.Errorf("Newest = %q", got)
	}
	if got := Newest(nil); got != "" {
		t.Errorf("Newest(nil) = %q", got)
	}
}

// The canonical KG name carries the infores registry prefix, which has a colon in it. The split
// point is still the hyphen before the numeric version, so the prefix must not confuse it.
func TestParseInforesKeys(t *testing.T) {
	cases := []struct{ in, kg, version, slug string }{
		{"infores:drugapprovals-kp-1.16.0", "infores:drugapprovals-kp", "1.16.0", "drugapprovals-kp"},
		{"infores:drugapprovals-kp-1.11.2", "infores:drugapprovals-kp", "1.11.2", "drugapprovals-kp"},
		{"infores:drugapprovals-kp-1.0.0-rc1", "infores:drugapprovals-kp", "1.0.0-rc1", "drugapprovals-kp"},
		// A graph with no registry prefix is its own slug, and stays working.
		{"my-kg-2.1.0", "my-kg", "2.1.0", "my-kg"},
	}
	for _, c := range cases {
		got, err := Parse(c.in)
		if err != nil {
			t.Fatalf("Parse(%q): %v", c.in, err)
		}
		if got.KG != c.kg {
			t.Errorf("Parse(%q).KG = %q, want %q", c.in, got.KG, c.kg)
		}
		if v := got.Version(); v != c.version {
			t.Errorf("Parse(%q).Version() = %q, want %q", c.in, v, c.version)
		}
		if s := got.Slug(); s != c.slug {
			t.Errorf("Parse(%q).Slug() = %q, want %q", c.in, s, c.slug)
		}
	}
}

func TestSlug(t *testing.T) {
	cases := map[string]string{
		"infores:drugapprovals-kp": "drugapprovals-kp",
		"drugapprovals-kp":         "drugapprovals-kp",
		"":                         "",
		// Only a leading prefix is stripped: a name that merely contains one is left alone.
		"not-infores:kg": "not-infores:kg",
		"infores:":       "",
	}
	for in, want := range cases {
		if got := Slug(in); got != want {
			t.Errorf("Slug(%q) = %q, want %q", in, got, want)
		}
	}
}

// Labels are what the pool index keys its releases by, and text order would put 1.9.0 after
// 1.11.2 — which is exactly the bug the web app's version ordering exists to avoid.
func TestCompareLabels(t *testing.T) {
	cases := []struct {
		a, b string
		want int
	}{
		{"1.9.0", "1.11.2", -1},
		{"1.11.2", "1.16.0", -1},
		{"1.16.0", "1.16.0", 0},
		{"2.0.0", "1.99.99", 1},
		// A pre-release sorts before the release it precedes.
		{"1.0.0-rc1", "1.0.0", -1},
	}
	for _, c := range cases {
		if got := CompareLabels(c.a, c.b); got != c.want {
			t.Errorf("CompareLabels(%q, %q) = %d, want %d", c.a, c.b, got, c.want)
		}
	}
	// Unparseable labels still get a total order rather than a panic.
	if CompareLabels("garbage", "also-garbage") == 0 {
		t.Error("two different unparseable labels compared equal")
	}
}
