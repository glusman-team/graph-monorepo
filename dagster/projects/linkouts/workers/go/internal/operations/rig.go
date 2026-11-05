package operations

import (
	"bufio"
	"fmt"
	"io"
	"os"
	"regexp"
	"sort"
	"strings"

	"github.com/glusman-team/graph-monorepo/dagster/projects/linkouts/workers/go/internal/codec"
)

// fieldStats is what inspection accumulates per predicate.
type fieldStats struct {
	edges   int
	present map[string]int
	lists   map[string]int
	example map[string]string
	curie   map[string]bool
	mapKeys map[string][]string
}

type RigOptions struct {
	Limit      int
	Predicate  string
	EmitConfig bool
}

type RigReport struct {
	Seen       int            `json:"seen"`
	Malformed  int            `json:"malformed"`
	Predicates map[string]int `json:"predicates"`
}

// InspectKGX streams an NDJSON input, emitting either its tabular report or a starter config.
func InspectKGX(path string, opts RigOptions, out io.Writer) (RigReport, error) {
	fh, err := os.Open(path)
	if err != nil {
		return RigReport{}, err
	}
	// Read-only handle: nothing is buffered, so a Close failure cannot lose data.
	defer func() { _ = fh.Close() }()

	sc := bufio.NewScanner(fh)
	sc.Buffer(make([]byte, 0, 64*1024), 64*1024*1024)
	stats := map[string]*fieldStats{}
	predicates := map[string]int{}
	seen, malformed := 0, 0
	for sc.Scan() {
		raw := sc.Bytes()
		if len(raw) == 0 {
			continue
		}
		if opts.Limit > 0 && seen >= opts.Limit {
			break
		}
		// Lenient parse: rig inspects other people's data, so a null or an odd shape is a
		// finding to report, not a reason to abort.
		doc, err := codec.ParseLenient(raw)
		if err != nil {
			malformed++
			continue
		}
		seen++
		predicate, _ := doc["predicate"].(string)
		if predicate == "" {
			predicate = "(missing)"
		}
		if opts.Predicate != "" && predicate != opts.Predicate {
			continue
		}
		predicates[predicate]++
		st, ok := stats[predicate]
		if !ok {
			st = &fieldStats{present: map[string]int{}, lists: map[string]int{}, example: map[string]string{}, curie: map[string]bool{}, mapKeys: map[string][]string{}}
			stats[predicate] = st
		}
		st.edges++
		for k, v := range doc {
			if codec.Nullish(v) {
				continue
			}
			st.present[k]++
			if list, ok := v.([]any); ok {
				st.lists[k] += len(list)
			}
			if _, have := st.example[k]; !have {
				st.example[k] = exampleOf(v)
				st.curie[k] = looksLikeCURIE(v)
				st.mapKeys[k] = firstMapKeys(v)
			}
		}
	}
	if err := sc.Err(); err != nil {
		return RigReport{}, err
	}
	if seen == 0 {
		return RigReport{}, fmt.Errorf("%s contained no usable KGX edge records", path)
	}
	report := RigReport{Seen: seen, Malformed: malformed, Predicates: predicates}
	if opts.EmitConfig {
		return report, emitConfig(out, path, stats)
	}
	return report, printReport(out, path, seen, malformed, predicates, stats)
}

func printReport(out io.Writer, path string, seen, malformed int, predicates map[string]int, stats map[string]*fieldStats) error {
	fmt.Fprintf(out, "%s: %d edges inspected", path, seen)
	if malformed > 0 {
		fmt.Fprintf(out, " (%d lines unparseable)", malformed)
	}
	fmt.Fprintln(out, "")
	names := make([]string, 0, len(predicates))
	for p := range predicates {
		names = append(names, p)
	}
	sort.Slice(names, func(i, j int) bool { return predicates[names[i]] > predicates[names[j]] })
	for _, p := range names {
		st := stats[p]
		fmt.Fprintf(out, "\n%s  (%d edges)\n", p, predicates[p])
		fields := make([]string, 0, len(st.present))
		for k := range st.present {
			fields = append(fields, k)
		}
		sort.Slice(fields, func(i, j int) bool { return st.present[fields[i]] > st.present[fields[j]] })
		for _, k := range fields {
			n := st.present[k]
			line := fmt.Sprintf("  %-34s %6d  %5.1f%%", k, n, 100*float64(n)/float64(st.edges))
			if total, isList := st.lists[k]; isList {
				line += fmt.Sprintf("  list, %.0f items/edge", float64(total)/float64(n))
			}
			line += "  e.g. " + st.example[k]
			fmt.Fprintln(out, line)
		}
	}
	fmt.Fprintf(out, "\n%d predicates; fields present on under 1%% of edges are usually release noise\n", len(names))
	return nil
}

// emitConfig writes a starter kgs/*.exs in the display grammar the web app loads.
//
// The output must compile as-is: `mix linkouts.check` loads it, and a starter that fails to parse
// teaches people to hand-edit rather than regenerate. So every field becomes one evidence
// sentence gated on {:present, ...}, phrased as "Field name: <spec>." so it reads as prose with
// no storage key in sight, and the only TODOs are the ones a human has to decide — the name, the
// prose, and which sentences to keep.
//
// Fields the join adds or the page already shows are skipped, so the starter lists only what the
// KG itself asserts.
func emitConfig(out io.Writer, path string, stats map[string]*fieldStats) error {
	fields := map[string]string{}
	edges := 0
	for _, st := range stats {
		edges += st.edges
		for k := range st.present {
			if _, ok := fields[k]; !ok {
				fields[k] = st.example[k]
			}
		}
	}
	names := make([]string, 0, len(fields))
	for k := range fields {
		if !rigShownElsewhere[k] {
			names = append(names, k)
		}
	}
	sort.Strings(names)

	predicates := make([]string, 0, len(stats))
	for p := range stats {
		predicates = append(predicates, p)
	}
	sort.Strings(predicates)

	fmt.Fprintf(out, `# Generated by the linkouts rig operation from %s (%d edges, %d predicates).
#
# This compiles as-is. To finish it:
#   1. set name to the <name> part of the version key you load with (e.g. "my-kg" for my-kg-1.0.0)
#   2. replace the edge sentence; {:pick, "predicate", ...} lets each predicate read differently
#   3. delete the evidence sentences a reviewer does not need, and rewrite the rest in the KG's voice
# Then run `+"`cd sites/linkouts && mix linkouts.check`"+`. See the Linkouts add-a-KG guide in sites/docs.
#
# Predicates observed:
`, path, edges, len(predicates))
	for _, p := range predicates {
		fmt.Fprintf(out, "#   %-48s %d edges\n", p, stats[p].edges)
	}
	fmt.Fprintln(out, `%{
  name: "TODO-kg",
  display_name: "TODO",
  url: "https://example.org/TODO",

  slots: %{
    "subject" => {:link, "subject_name", "subject"},
    "object" => {:link, "object_name", "object"},
    "predicate_words" => {:humanize, "predicate"}
  },

  title: "{subject_name} - {object_name}",

  edge: "This relationship states that {subject} {predicate_words} {object}.",

  evidence: [`)
	for _, k := range names {
		fmt.Fprintf(out, "    # e.g. %s\n", exampleComment(fields[k]))
		fmt.Fprintf(out, "    %%{value: \"%s: %s.\", if: [{:present, %s}]},\n",
			humanize(k), valueSpecFor(k, stats), elixirString(k))
	}
	fmt.Fprintln(out, "  ]\n}")
	return nil
}

// rigShownElsewhere are fields the page already renders through the sentence, the header, or the
// diagram. Listing them again as evidence would show every edge's subject twice.
var rigShownElsewhere = map[string]bool{
	"id": true, "subject": true, "object": true, "predicate": true,
	"subject_name": true, "object_name": true,
	"subject_category": true, "object_category": true,
}

// valueSpecFor picks a value form from the shape rig observed: lists of CURIE-like strings get
// linked, other lists are joined, scalars are humanized — a starter sentence must not print a
// storage key like manual_validation_of_automated_agent where words belong.
func valueSpecFor(field string, stats map[string]*fieldStats) string {
	isList, curieLike := false, false
	var keys []string
	for _, st := range stats {
		if keys == nil && len(st.mapKeys[field]) > 0 {
			keys = st.mapKeys[field]
		}
		if st.lists[field] > 0 {
			isList = true
		}
		if st.curie[field] {
			curieLike = true
		}
	}
	q := elixirString(field)
	switch {
	case isList && len(keys) > 0:
		// Inside {:list, ...} a map element becomes the document, so its keys are read by name.
		// KGX sources carry resource_id, which is the useful one; otherwise take the first key.
		key := keys[0]
		for _, k := range keys {
			if k == "resource_id" {
				key = k
			}
		}
		return `{:list, ` + q + `, ` + elixirString("{"+key+"}") + `, ", "}`
	case isList && curieLike:
		return `{:list, ` + q + `, {:link, "self", "self"}, ", "}`
	case isList:
		return `{:list, ` + q + `, {:field, "self"}, ", "}`
	default:
		return `{:humanize, ` + q + `}`
	}
}

// exampleComment keeps an example on one comment line whatever the data contains.
func exampleComment(ex string) string {
	return strings.NewReplacer("\n", " ", "\r", " ").Replace(ex)
}

// elixirString quotes s as an Elixir string literal. `#{` is escaped because Elixir would
// otherwise interpolate it, and a KG field named "#{...}" must not execute in the config loader.
func elixirString(s string) string {
	r := strings.NewReplacer(`\`, `\\`, `"`, `\"`, "#{", `\#{`)
	return `"` + r.Replace(s) + `"`
}

// looksLikeCURIE reports whether a value (or a list's first element) is a prefixed identifier
// such as "MONDO:0004979" or "dailymed:561c…", which is what deserves a linkout. "NDA020346"
// counts too: the prefix table normalizes bare FDA application numbers.
func looksLikeCURIE(v any) bool {
	if list, ok := v.([]any); ok {
		if len(list) == 0 {
			return false
		}
		v = list[0]
	}
	s, ok := v.(string)
	if !ok || strings.ContainsAny(s, " \t") {
		return false
	}
	// A URL also has a colon, but "http" is a scheme, not a prefix the linkout table resolves.
	if prefix, rest, found := strings.Cut(s, ":"); found && prefix != "" && !strings.HasPrefix(rest, "//") {
		return true
	}
	return rigFDANumber.MatchString(s)
}

// firstMapKeys returns the sorted keys of a list's first element when that element is a map.
func firstMapKeys(v any) []string {
	list, ok := v.([]any)
	if !ok || len(list) == 0 {
		return nil
	}
	m, ok := list[0].(map[string]any)
	if !ok {
		return nil
	}
	keys := make([]string, 0, len(m))
	for k := range m {
		keys = append(keys, k)
	}
	sort.Strings(keys)
	return keys
}

var rigFDANumber = regexp.MustCompile(`^(?i:NDA|ANDA|BLA)\s*\d+$`)

func exampleOf(v any) string {
	switch t := v.(type) {
	case string:
		return truncateExample(t)
	case map[string]any:
		// Go's fmt prints maps as map[k:v], which reads as noise in a config comment.
		keys := make([]string, 0, len(t))
		for k := range t {
			keys = append(keys, k)
		}
		sort.Strings(keys)
		return truncateExample("{" + strings.Join(keys, ", ") + "}")
	case []any:
		if len(t) == 0 {
			return "[]"
		}
		return fmt.Sprintf("[%s, ...%d]", truncateExample(fmt.Sprint(t[0])), len(t))
	default:
		return truncateExample(fmt.Sprint(t))
	}
}

func truncateExample(s string) string {
	const max = 48
	s = strings.ReplaceAll(s, "\n", " ")
	if len(s) <= max {
		return s
	}
	return s[:max] + "..."
}

func humanize(k string) string {
	return strings.ToUpper(k[:1]) + strings.ReplaceAll(k[1:], "_", " ")
}
