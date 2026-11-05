package operations

import (
	"context"
	"errors"
	"fmt"

	"github.com/glusman-team/graph-monorepo/dagster/projects/linkouts/workers/go/internal/codec"
	"github.com/glusman-team/graph-monorepo/dagster/projects/linkouts/workers/go/internal/engine"
)

type TrainDictionaryOptions struct {
	NodesPath string
	EdgesPath string
	Samples   int
	Threads   int
	Engine    engine.Engine
}

type DictionaryResult struct {
	Bytes       []byte
	ID          uint32
	SamplesSeen int
	SamplesUsed int
}

func TrainDictionary(
	ctx context.Context,
	opts TrainDictionaryOptions,
) (DictionaryResult, error) {
	if opts.Engine == nil {
		return DictionaryResult{}, fmt.Errorf("dictionary training needs a join engine")
	}
	if opts.Samples <= 0 {
		return DictionaryResult{}, fmt.Errorf("dictionary training needs a positive sample count")
	}
	docs := make([][]byte, 0, opts.Samples)
	err := opts.Engine.Join(ctx, engine.Query{
		NodesPath: opts.NodesPath, EdgesPath: opts.EdgesPath, Threads: opts.Threads,
	}, func(row engine.Row) error {
		if len(docs) >= opts.Samples {
			return engine.ErrStop
		}
		doc, err := engine.MergedDoc(row)
		if err != nil {
			return err
		}
		blob, err := codec.NewBlob("sample", doc)
		if err != nil {
			return err
		}
		raw, err := codec.Marshal(blob)
		if err != nil {
			return err
		}
		docs = append(docs, raw)
		return nil
	})
	if err != nil && !errors.Is(err, engine.ErrStop) {
		return DictionaryResult{}, err
	}
	if len(docs) == 0 {
		return DictionaryResult{}, fmt.Errorf("no documents were sampled from %s", opts.EdgesPath)
	}
	dict, err := codec.BuildDict(docs, 0)
	if err != nil {
		return DictionaryResult{}, err
	}
	return DictionaryResult{
		Bytes:       dict,
		ID:          codec.DictID(dict),
		SamplesSeen: len(docs),
		SamplesUsed: len(docs),
	}, nil
}
