// Package config resolves runtime configuration from the environment. Every secret comes
// from ~/.envrc via direnv; nothing here has a default that could accidentally point at a
// production account.
package config

import (
	"fmt"
	"os"
	"strconv"
	"strings"
)

// Defaults for the non-secret layout. They match .envrc.example.
const (
	DefaultDatabase  = "edge_linkouts"
	DefaultContainer = "edges"
	// DefaultRUps is the worker's slice of the free tier's 1000 RU/s: 90%, because ingestion
	// is transient and should finish as fast as the tier allows; the web app's budget is
	// a pacing ceiling, not a reservation, so the app rides the remaining ~10% headroom
	// while a load runs.
	DefaultRUps       = 900.0
	DefaultThroughput = 1000 // free-tier ceiling; see ADR 0002
	// PartitionKeyPath is /id: the edge UUID is the only thing ever queried.
	PartitionKeyPath = "/id"
)

// Cosmos holds the account coordinates. Key is the read-write key used by the worker;
// ReadOnlyKey is what the web app uses and is never needed for writes.
type Cosmos struct {
	Endpoint    string
	Key         string
	ReadOnlyKey string
	Database    string
	Container   string
	Throughput  int32
}

// Ready reports whether the write path is configured. A missing key is normal for
// read-only work (the web app, tests), so callers decide whether to error.
func (c Cosmos) Ready() bool { return c.Endpoint != "" && c.Key != "" }

// Config is process-level worker configuration beyond one operation's typed payload.
type Config struct {
	Cosmos Cosmos
	// RUps is the spend ceiling for one worker operation. It is deliberately below the
	// account's 1000 RU/s so a load cannot starve the web app sharing the free tier.
	RUps float64
	// ChdbCacheDir is where the embedded ClickHouse engine extracts itself (~540 MiB).
	ChdbCacheDir string
}

// Getenv is the environment lookup, injectable so tests do not have to mutate the process
// environment.
type Getenv func(string) string

// Load reads configuration. It never fails on a missing Cosmos key: the worker's `load`
// operation with `--store file:...` (and every test) must work with no account configured at all.
func Load(getenv Getenv) (Config, error) {
	if getenv == nil {
		getenv = os.Getenv
	}
	cfg := Config{
		Cosmos: Cosmos{
			Database:   orDefault(getenv("COSMOS_DB"), DefaultDatabase),
			Container:  orDefault(getenv("COSMOS_CONTAINER"), DefaultContainer),
			Throughput: int32(orDefaultInt(getenv("COSMOS_THROUGHPUT"), DefaultThroughput)),
		},
		RUps:         orDefaultFloat(getenv("RU_BUDGET_CLI"), DefaultRUps),
		ChdbCacheDir: getenv("CHDB_CACHE_DIR"),
	}
	if cfg.RUps <= 0 {
		return cfg, fmt.Errorf("RU_BUDGET_CLI must be positive, got %v", cfg.RUps)
	}

	// A connection string carries endpoint and key together and is what the Azure portal
	// hands out, so it is the primary source. Explicit COSMOS_ENDPOINT/COSMOS_KEY win,
	// which lets a key be rotated in one place without editing the connection string.
	if cs := getenv("COSMOS_PRIMARY_CONNECTION_STRING_RW"); cs != "" {
		endpoint, key, err := ParseConnectionString(cs)
		if err != nil {
			return cfg, fmt.Errorf("COSMOS_PRIMARY_CONNECTION_STRING_RW: %w", err)
		}
		cfg.Cosmos.Endpoint, cfg.Cosmos.Key = endpoint, key
	}
	if cs := getenv("COSMOS_PRIMARY_CONNECTION_STRING_R"); cs != "" {
		endpoint, key, err := ParseConnectionString(cs)
		if err != nil {
			return cfg, fmt.Errorf("COSMOS_PRIMARY_CONNECTION_STRING_R: %w", err)
		}
		cfg.Cosmos.ReadOnlyKey = key
		if cfg.Cosmos.Endpoint == "" {
			cfg.Cosmos.Endpoint = endpoint
		}
	}
	if v := getenv("COSMOS_ENDPOINT"); v != "" {
		cfg.Cosmos.Endpoint = v
	}
	if v := getenv("COSMOS_KEY"); v != "" {
		cfg.Cosmos.Key = v
	}
	if v := getenv("COSMOS_READ_ONLY_KEY"); v != "" {
		cfg.Cosmos.ReadOnlyKey = v
	}
	return cfg, nil
}

// ParseConnectionString splits an Azure Cosmos DB connection string:
// "AccountEndpoint=https://acct.documents.azure.com:443/;AccountKey=base64==;".
// AccountKey values end in base64 padding, so only the first '=' separates name from value.
func ParseConnectionString(cs string) (endpoint, key string, err error) {
	var found int
	for _, part := range strings.Split(cs, ";") {
		name, value, ok := strings.Cut(strings.TrimSpace(part), "=")
		if !ok || value == "" {
			continue
		}
		switch {
		case strings.EqualFold(name, "AccountEndpoint"):
			endpoint = strings.TrimSuffix(value, "/")
			found++
		case strings.EqualFold(name, "AccountKey"):
			key = value
			found++
		}
	}
	if endpoint == "" || key == "" {
		return "", "", fmt.Errorf("expected AccountEndpoint and AccountKey, found %d of 2 (is it the full connection string?)", found)
	}
	return endpoint, key, nil
}

func orDefault(v, def string) string {
	if strings.TrimSpace(v) == "" {
		return def
	}
	return v
}

func orDefaultInt(v string, def int) int {
	if strings.TrimSpace(v) == "" {
		return def
	}
	n, err := strconv.Atoi(strings.TrimSpace(v))
	if err != nil {
		return def
	}
	return n
}

func orDefaultFloat(v string, def float64) float64 {
	if strings.TrimSpace(v) == "" {
		return def
	}
	f, err := strconv.ParseFloat(strings.TrimSpace(v), 64)
	if err != nil {
		return def
	}
	return f
}
