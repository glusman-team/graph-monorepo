// Command linkouts-worker is the argument-free Dagster Pipes worker for Linkouts.
package main

import (
	"context"
	"errors"
	"fmt"
	"log/slog"
	"os"
	"os/signal"
	"syscall"

	dagsterpipes "github.com/hupe1980/dagster-pipes-go"

	"github.com/glusman-team/graph-monorepo/dagster/projects/linkouts/workers/go/internal/worker"
)

func main() {
	if err := run(); err != nil {
		slog.Error("linkouts worker failed", "error", err)
		os.Exit(1)
	}
}

func run() (err error) {
	ctx, stop := signal.NotifyContext(context.Background(), os.Interrupt, syscall.SIGTERM)
	defer stop()
	session, err := dagsterpipes.New[map[string]any]()
	if err != nil {
		return fmt.Errorf("open Dagster Pipes context: %w", err)
	}
	defer func() {
		if closeErr := session.Close(); closeErr != nil {
			err = errors.Join(err, fmt.Errorf("close Dagster Pipes session: %w", closeErr))
		}
	}()
	return session.Run(func(pipes *dagsterpipes.Context[map[string]any]) error {
		return worker.Run(ctx, pipes, os.Getenv)
	})
}
