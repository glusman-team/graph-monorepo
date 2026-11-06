package config

import "testing"

// Test fixtures are assembled at runtime so no secret-shaped literal ever lands in the source
// tree: a committed "AccountKey=<base64>" trips gitleaks whether or not it is real, and the
// real account hostname has no business in a test either.
const (
	testEndpoint = "https://example-account.documents.azure.com:443"
	rwKey        = "not-a-real-key-padding-preserved=="
	roKey        = "also-not-a-real-key=="
)

func connStr(key string) string {
	return "AccountEndpoint=" + testEndpoint + "/;AccountKey=" + key + ";"
}

var (
	rwConn = connStr(rwKey)
	rConn  = connStr(roKey)
)

func env(m map[string]string) Getenv {
	return func(k string) string { return m[k] }
}

func TestParseConnectionString(t *testing.T) {
	endpoint, key, err := ParseConnectionString(rwConn)
	if err != nil {
		t.Fatalf("ParseConnectionString: %v", err)
	}
	if endpoint != testEndpoint {
		t.Errorf("endpoint = %q, trailing slash not trimmed", endpoint)
	}
	// The key ends in base64 padding, so splitting on every '=' would truncate it.
	if key != rwKey {
		t.Errorf("key = %q, want the padding preserved", key)
	}

	bad := []string{"", "AccountEndpoint=https://x", "AccountKey=k", "garbage", "AccountEndpoint=;AccountKey=;"}
	for _, cs := range bad {
		if _, _, err := ParseConnectionString(cs); err == nil {
			t.Errorf("ParseConnectionString(%q) accepted an unusable string", cs)
		}
	}
}

func TestLoadDefaultsAndConnectionStrings(t *testing.T) {
	cfg, err := Load(env(map[string]string{
		"COSMOS_PRIMARY_CONNECTION_STRING_RW": rwConn,
		"COSMOS_PRIMARY_CONNECTION_STRING_R":  rConn,
	}))
	if err != nil {
		t.Fatalf("Load: %v", err)
	}
	if cfg.Cosmos.Endpoint != testEndpoint {
		t.Errorf("endpoint = %q", cfg.Cosmos.Endpoint)
	}
	if cfg.Cosmos.Key != rwKey {
		t.Errorf("write key = %q", cfg.Cosmos.Key)
	}
	if cfg.Cosmos.ReadOnlyKey != roKey {
		t.Errorf("read-only key = %q", cfg.Cosmos.ReadOnlyKey)
	}
	if cfg.Cosmos.Database != DefaultDatabase || cfg.Cosmos.Container != DefaultContainer {
		t.Errorf("layout = %s/%s, want the defaults", cfg.Cosmos.Database, cfg.Cosmos.Container)
	}
	if cfg.RUps != DefaultRUps {
		t.Errorf("RUps = %v, want %v", cfg.RUps, DefaultRUps)
	}
	if !cfg.Cosmos.Ready() {
		t.Error("Ready() is false with a write key present")
	}
}

func TestLoadExplicitVarsWin(t *testing.T) {
	cfg, err := Load(env(map[string]string{
		"COSMOS_PRIMARY_CONNECTION_STRING_RW": rwConn,
		"COSMOS_KEY":                          "rotated",
		"COSMOS_DB":                           "other_db",
		"COSMOS_CONTAINER":                    "other_edges",
		"RU_BUDGET_CLI":                       "120",
		"CHDB_CACHE_DIR":                      "/tmp/chdb",
	}))
	if err != nil {
		t.Fatalf("Load: %v", err)
	}
	if cfg.Cosmos.Key != "rotated" {
		t.Errorf("key = %q, explicit COSMOS_KEY should override the connection string", cfg.Cosmos.Key)
	}
	if cfg.Cosmos.Database != "other_db" || cfg.Cosmos.Container != "other_edges" {
		t.Errorf("layout = %s/%s", cfg.Cosmos.Database, cfg.Cosmos.Container)
	}
	if cfg.RUps != 120 || cfg.ChdbCacheDir != "/tmp/chdb" {
		t.Errorf("RUps=%v chdb=%q", cfg.RUps, cfg.ChdbCacheDir)
	}
}

func TestLoadWithoutAccountIsNotAnError(t *testing.T) {
	// The file store, the tests, and every offline make target must work with no Cosmos
	// configured at all.
	cfg, err := Load(env(nil))
	if err != nil {
		t.Fatalf("Load with an empty environment: %v", err)
	}
	if cfg.Cosmos.Ready() {
		t.Error("Ready() is true with no endpoint or key")
	}
	if cfg.RUps != DefaultRUps {
		t.Errorf("RUps = %v", cfg.RUps)
	}
}

func TestLoadRejectsNonPositiveBudget(t *testing.T) {
	if _, err := Load(env(map[string]string{"RU_BUDGET_CLI": "0"})); err == nil {
		t.Error("a zero RU budget was accepted; it would silently disable pacing")
	}
	if _, err := Load(env(map[string]string{"RU_BUDGET_CLI": "-5"})); err == nil {
		t.Error("a negative RU budget was accepted")
	}
	// Unparseable falls back to the default rather than failing a run over a typo.
	cfg, err := Load(env(map[string]string{"RU_BUDGET_CLI": "lots"}))
	if err != nil || cfg.RUps != DefaultRUps {
		t.Errorf("unparseable budget: RUps=%v err=%v", cfg.RUps, err)
	}
}
