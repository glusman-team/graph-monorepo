// Command extractor is the DAKP-pattern extraction worker.
//
// It runs as a Dagster Pipes subprocess (launched by the `extraction` asset
// via mo-dagster): reads the pipes context from env, consumes newline-
// delimited input records from the path in extras["input"], writes extracted
// records to extras["output"], and reports a materialization with BLAKE3
// cache metadata (cache_key, cache_hit, rows).
//
// The worker never names the asset it materializes: it reports to whatever
// asset key the pipes context carries, so the python asset owns the key and a
// narrow single-output run lands its metadata on the right asset.
//
// Phase 1 body: passthrough with row counting - the extraction logic itself
// migrates from ISB/DAKP in phase 2. The pipes/cache contract is final.
package main

import (
	"bufio"
	"bytes"
	"encoding/json"
	"fmt"
	"log"
	"os"
	"path/filepath"

	dagsterpipes "github.com/hupe1980/dagster-pipes-go"

	"github.com/glusman-team/dakp/go/internal/pipesutil"
)

func run(ctx *dagsterpipes.Context[map[string]any]) error {
	input, ok := ctx.Extras()["input"].(string)
	if !ok || input == "" {
		return fmt.Errorf("extras.input (path to input ndjson) is required")
	}
	output, _ := ctx.Extras()["output"].(string)
	if output == "" {
		return fmt.Errorf("extras.output (path to output ndjson) is required")
	}

	raw, err := os.ReadFile(input)
	if err != nil {
		return fmt.Errorf("read input: %w", err)
	}
	cacheKey := pipesutil.HashInput(raw)

	// content-addressed cache lookup: artifact exists -> replay it, no work
	if out, err := os.ReadFile(output); err == nil && len(out) > 0 {
		_ = ctx.LogInfo(fmt.Sprintf("cache hit: %s (%d bytes)", output, len(out)))
		return pipesutil.ReportMaterialization(ctx, cacheKey, pipesutil.CacheInfo{
			Key:          cacheKey,
			ArtifactPath: output,
			Hit:          true,
			InputBytes:   int64(len(raw)),
		}, int64(countLines(out)))
	}

	// Phase 1 demo: passthrough. Phase 2 replaces this with real extraction.
	rows := int64(0)
	outFile, err := os.Create(filepath.Clean(output))
	if err != nil {
		return fmt.Errorf("create output: %w", err)
	}
	defer func() { _ = outFile.Close() }() // safety for early returns; checked close below
	w := bufio.NewWriter(outFile)
	scanner := bufio.NewScanner(bytes.NewReader(raw))
	scanner.Buffer(make([]byte, 0, 1024*1024), 16*1024*1024)
	for scanner.Scan() {
		line := scanner.Bytes()
		if len(line) == 0 {
			continue
		}
		var record map[string]any
		if err := json.Unmarshal(line, &record); err != nil {
			return fmt.Errorf("line %d: %w", rows+1, err)
		}
		record["extracted"] = true
		record["worker_version"] = pipesutil.WorkerVersion
		encoded, err := json.Marshal(record)
		if err != nil {
			return err
		}
		if _, err := w.Write(encoded); err != nil {
			return err
		}
		if _, err := w.WriteString("\n"); err != nil {
			return err
		}
		rows++
	}
	if err := scanner.Err(); err != nil {
		return err
	}
	if err := w.Flush(); err != nil {
		return err
	}
	if err := outFile.Close(); err != nil {
		return fmt.Errorf("close output: %w", err)
	}

	_ = ctx.LogInfo(fmt.Sprintf("extracted %d rows -> %s", rows, output))
	return pipesutil.ReportMaterialization(ctx, cacheKey, pipesutil.CacheInfo{
		Key:          cacheKey,
		ArtifactPath: output,
		Hit:          false,
		InputBytes:   int64(len(raw)),
	}, rows)
}

func countLines(b []byte) int {
	n := 0
	for _, c := range b {
		if c == '\n' {
			n++
		}
	}
	return n
}

func main() {
	session, err := dagsterpipes.New[map[string]any]()
	if err != nil {
		log.Fatalf("dagster pipes session: %v", err)
	}
	defer func() {
		if err := session.Close(); err != nil {
			log.Printf("close session: %v", err)
		}
	}()

	if err := session.Run(run); err != nil {
		log.Fatalf("extractor: %v", err)
	}
}
