package engine

import (
	"bytes"
	"context"
	_ "embed"
	"errors"
	"fmt"
	"os"
	"path/filepath"
	"strings"

	"github.com/chdb-io/chdb-go/v2/chdb"
	// The blank import is what selects the engine bundled in the Go module cache instead of
	// a system libchdb. It costs ~117 MB of module payload on linux/amd64 and extracts
	// ~540 MiB on first use (ADR 0002).
	_ "github.com/chdb-io/chdb-go/lib/embedded"

	"github.com/glusman-team/graph-monorepo/dagster/projects/linkouts/workers/go/internal/codec"
)

//go:embed join.sql
var joinSQL string

// Chdb is the embedded ClickHouse backend. One session, one process: chdb keeps a single
// engine singleton and permits only one data path per process, so this type is not safe to
// open twice.
type Chdb struct {
	sess *chdb.Session
}

var _ Engine = (*Chdb)(nil)

// NewChdb opens an in-memory session. cacheDir, when set, is exported as CHDB_CACHE_DIR
// before the engine loads — that env var is the only knob chdb offers for where it extracts
// libchdb.so, and it must be persistent, user-private, and not group/world-writable.
func NewChdb(cacheDir string, _ int) (*Chdb, error) {
	if cacheDir != "" {
		if err := os.MkdirAll(cacheDir, 0o700); err != nil {
			return nil, fmt.Errorf("chdb cache dir %s: %w", cacheDir, err)
		}
		// Setting process env from a library is unpleasant, but chdb reads the location only
		// from the environment, and it must be set before the first engine load.
		if err := os.Setenv("CHDB_CACHE_DIR", cacheDir); err != nil {
			return nil, fmt.Errorf("set CHDB_CACHE_DIR: %w", err)
		}
	}
	sess, err := chdb.NewSession(":memory:")
	if err != nil {
		return nil, fmt.Errorf("open embedded ClickHouse session (first run extracts ~540 MiB to %s): %w",
			orDefault(cacheDir, "the default cache dir"), err)
	}
	return &Chdb{sess: sess}, nil
}

// Name identifies the backend, including the engine version when it can be read.
func (c *Chdb) Name() string {
	res, err := c.sess.Query("SELECT version()", "TabSeparatedRaw")
	if err != nil {
		return "chdb (version unknown)"
	}
	defer res.Free()
	return "chdb ClickHouse " + strings.TrimSpace(res.String())
}

// Join streams the join query and emits one Row per edge.
func (c *Chdb) Join(ctx context.Context, q Query, emit func(Row) error) error {
	if err := q.validate(); err != nil {
		return err
	}
	sql, err := buildJoinSQL(q)
	if err != nil {
		return err
	}
	stream, err := c.sess.QueryStream(sql, "JSONEachRow")
	if err != nil {
		return fmt.Errorf("start join query: %w", err)
	}
	// Free cancels and releases the stream; required if we stop reading early, which a
	// failing emit callback or a cancelled context both do.
	defer stream.Free()

	// A chunk is a span of bytes, not a set of lines: rows can straddle chunk boundaries, so
	// the tail of each chunk is carried into the next.
	var carry []byte
	line, rows := 0, 0
	for {
		if err := ctx.Err(); err != nil {
			return err
		}
		chunk := stream.GetNext()
		if chunk == nil {
			break // end of stream
		}
		if err := chunk.Error(); err != nil {
			chunk.Free()
			return fmt.Errorf("join query failed: %w", err)
		}
		buf := append(carry, chunk.Buf()...)
		for {
			idx := bytes.IndexByte(buf, '\n')
			if idx < 0 {
				break
			}
			raw := buf[:idx]
			buf = buf[idx+1:]
			if len(bytes.TrimSpace(raw)) == 0 {
				continue
			}
			line++
			row, err := parseRow(raw)
			if err != nil {
				chunk.Free()
				return fmt.Errorf("join output line %d: %w", line, err)
			}
			rows++
			if err := emit(row); err != nil {
				chunk.Free()
				if errors.Is(err, ErrStop) {
					return nil
				}
				return err
			}
		}
		carry = append(carry[:0], buf...)
		chunk.Free()
	}
	if len(bytes.TrimSpace(carry)) > 0 {
		line++
		row, err := parseRow(carry)
		if err != nil {
			return fmt.Errorf("join output line %d: %w", line, err)
		}
		rows++
		if err := emit(row); err != nil {
			if errors.Is(err, ErrStop) {
				return nil
			}
			return err
		}
	}
	if err := stream.Error(); err != nil {
		return fmt.Errorf("join query: %w", err)
	}
	// An empty result is an error, not a successful load of nothing: it means the edges path
	// was wrong, the file was truncated, or the KGX shape changed so the JSON column came
	// back empty. Silently writing zero documents would empty the store.
	if rows == 0 {
		return fmt.Errorf("%s produced no joined edges (is it a KGX edges file?)", q.EdgesPath)
	}
	return nil
}

// Close releases the session. chdb.Shutdown is deliberately not called: it errors while any
// session is open, and the process exit reclaims the engine anyway.
func (c *Chdb) Close() error {
	if c.sess == nil {
		return nil
	}
	c.sess.Close()
	c.sess = nil
	return nil
}

// buildJoinSQL substitutes absolute, single-quote-escaped paths into the embedded query.
func buildJoinSQL(q Query) (string, error) {
	nodes, err := absPath(q.NodesPath)
	if err != nil {
		return "", fmt.Errorf("nodes: %w", err)
	}
	edges, err := absPath(q.EdgesPath)
	if err != nil {
		return "", fmt.Errorf("edges: %w", err)
	}
	sql := strings.ReplaceAll(joinSQL, "{nodes}", sqlQuote(nodes))
	sql = strings.ReplaceAll(sql, "{edges}", sqlQuote(edges))
	return strings.ReplaceAll(sql, "{threads}", fmt.Sprint(q.threads())), nil
}

// absPath resolves and checks existence. chdb's file() resolves relative paths against the
// process CWD, so a relative path here would make results depend on where the worker ran.
func absPath(p string) (string, error) {
	abs, err := filepath.Abs(p)
	if err != nil {
		return "", err
	}
	info, err := os.Stat(abs)
	if err != nil {
		return "", err
	}
	if info.IsDir() {
		return "", fmt.Errorf("%s is a directory", abs)
	}
	return abs, nil
}

// sqlQuote wraps a value as a SQL string literal, doubling embedded quotes.
func sqlQuote(s string) string { return "'" + strings.ReplaceAll(s, "'", "''") + "'" }

// parseRow decodes one JSONEachRow line. The outer envelope is parsed leniently because a
// missing id arrives as SQL NULL, which must become a clear error rather than a generic
// "null found" complaint; the inner documents are parsed strictly.
func parseRow(raw []byte) (Row, error) {
	outer, err := codec.ParseLenient(raw)
	if err != nil {
		return Row{}, err
	}
	id, _ := outer["id"].(string)
	if id == "" {
		return Row{}, fmt.Errorf("edge has no id (KGX records must carry one): %s", truncate(string(raw)))
	}
	row := Row{ID: id}
	if row.Edge, err = parseInner(outer["edge"], id, "edge"); err != nil {
		return Row{}, err
	}
	if row.SubjectNode, err = parseInner(outer["subject_node"], id, "subject_node"); err != nil {
		return Row{}, err
	}
	if row.ObjectNode, err = parseInner(outer["object_node"], id, "object_node"); err != nil {
		return Row{}, err
	}
	return row, nil
}

// parseInner decodes a JSON-valued column, which arrives as a string holding JSON text.
func parseInner(v any, id, field string) (codec.Doc, error) {
	switch t := v.(type) {
	case nil:
		return codec.Doc{}, nil // unmatched LEFT JOIN side
	case string:
		if strings.TrimSpace(t) == "" {
			return codec.Doc{}, nil
		}
		doc, err := codec.ParseLenient([]byte(t))
		if err != nil {
			return nil, fmt.Errorf("edge %s: %s is not valid JSON: %w", id, field, err)
		}
		return doc, nil
	case codec.Doc:
		return t, nil
	case map[string]any:
		return codec.Doc(t), nil
	default:
		return nil, fmt.Errorf("edge %s: %s has unexpected type %T", id, field, v)
	}
}

func truncate(s string) string {
	const max = 200
	if len(s) <= max {
		return s
	}
	return s[:max] + "..."
}

func orDefault(v, def string) string {
	if v == "" {
		return def
	}
	return v
}
