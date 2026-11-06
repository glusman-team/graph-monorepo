// Package operations contains the reusable, terminal-independent Linkouts operations.
package operations

import (
	"context"
	"fmt"
	"os"

	"github.com/glusman-team/graph-monorepo/dagster/projects/linkouts/workers/go/internal/config"
	"github.com/glusman-team/graph-monorepo/dagster/projects/linkouts/workers/go/internal/cosmos"
	"github.com/glusman-team/graph-monorepo/dagster/projects/linkouts/workers/go/internal/ratelimit"
)

func OpenStore(
	ctx context.Context,
	spec string,
	settings config.Config,
	budget ratelimit.Budget,
) (cosmos.Store, error) {
	store, err := cosmos.Open(ctx, spec, settings.Cosmos, budget)
	if err != nil {
		return nil, err
	}
	return store, nil
}

func ReadDictionary(path string) ([]byte, error) {
	if path == "" {
		return nil, nil
	}
	dict, err := os.ReadFile(path)
	if err != nil {
		return nil, fmt.Errorf("read dictionary %s: %w", path, err)
	}
	if len(dict) == 0 {
		return nil, fmt.Errorf("dictionary %s is empty", path)
	}
	return dict, nil
}

func CloseWithError(closeFn func() error, err *error) {
	if closeErr := closeFn(); closeErr != nil && *err == nil {
		*err = closeErr
	}
}
