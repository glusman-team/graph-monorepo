# dakp-pipeline

DAKP pipeline core, ported from ISB/DAKP v1.25.0 for the graph-monorepo Dagster
deployment. Orchestrator-free: stages communicate through BLAKE3 content-addressed
`ArtifactRef`s and the operation index (`manifests/_index.json`). The Dagster code
location (`../src/dakp_dag`) wraps these stages as assets.

- Layout, conventions, commands: see the monorepo root AGENTS.md.
- Source of truth for behavior: DAKP v1.25.0 (tests are ported alongside the code).

## Private NER model

The default checkpoint is `SkyeAv/relmedner-biomed-dakp-20261020`, the private
DAKP specialist. Acquisition and inference use the same default and the existing
GLiNER2 backend; extraction thresholds are unchanged.

To select the broader zero-shot specialist instead, set this non-secret variable
in the runtime environment before acquisition and mining:

```sh
export DAKP_NER_MODEL=SkyeAv/relmedner-biomed-zeroshot-20261021
```

Acquisition and inference both resolve this override; an unset or blank value uses
the DAKP specialist. There is no automatic quality-based or authorization fallback.
Both model cards prescribe `AutoExtractor`, short labels mapped to natural-language
descriptions, `include_confidence=True`, and `include_spans=True`; the backend
already uses that interface with fp32 by default. The cards evaluate at threshold
`0.4`; DAKP retains its existing generation and profile-specific acceptance thresholds
rather than treating those diagnostics as pipeline calibration. The cards report
DAKP F1 of 0.8081 (DAKP specialist) and 0.7037 (round-2 zero-shot); these are reused
diagnostic probes, not a controlled comparison in this pipeline.

Model cards:

- https://huggingface.co/SkyeAv/relmedner-biomed-dakp-20261020
- https://huggingface.co/SkyeAv/relmedner-biomed-zeroshot-20261021

Set `HF_TOKEN` to a Hugging Face token with read access to this model in the
Dagster process environment (for example, through the untracked `dagster/.env`
loaded by the deployment), or use a saved Hugging Face Hub login on the runtime
host. Do not put token values in tracked configuration, commands, or run parameters.
The downloader resolves `HF_TOKEN` or the saved login with `huggingface_hub.get_token`
and explicitly passes it to `snapshot_download`, even when
`HF_HUB_DISABLE_IMPLICIT_TOKEN=1`.

Missing or denied private-model access fails acquisition without falling back to
the public base model. Tokens are not included in DAKP manifests or cache keys.
Verified model cache hits require no download or token. The changed model ID gets
its own cache directory and mention-cache identity, so old base-model mentions
are not reused for the specialist.

## NER gold evaluation

`tests/eval/ner_gold.json` contains 36 cases and 44 exact disease/phenotype object
mentions: the original 34-case corpus plus the losartan coadministration/diabetes
and fetal-toxicity/pregnancy cases from graph-monorepo issue 57. The evaluator
validates unique, non-overlapping surfaces, derives zero-based half-open offsets,
and reports duplicate-aware micro precision, recall, F1, missing mentions, and
false-positive mentions. DailyMed uses the contraindication profile and FAERS uses
the indication profile. Qualifier-channel mentions are outside this object-only
metric.

Run the torch-free unit and offline gates from this directory:

```sh
uv run pytest -q -n 0 tests/unit/test_ner_gold.py
uv run python tests/eval/ner_gold_eval.py --offline --require-perfect \
  --output /tmp/ner-gold-offline.json
```

Run the real configured checkpoint on the NER build host:

```sh
CUDA_VISIBLE_DEVICES=0 uv run --group ner python tests/eval/ner_gold_eval.py \
  --require-perfect --output /tmp/ner-gold-production.json
```

Production mode uses the private checkpoint and authentication described above;
it never falls back to offline extraction or a public model. `--require-perfect`
returns nonzero for any missing or extra object mention. The JSON report records
the mode, model ID, aggregate counts, and every case's predictions.

The current composite offline and production results are both 44 true positives,
zero false positives, and zero false negatives across these 36 cases. This is a
small, repeatedly used development corpus, not independent clinical validation or
a model-card-comparable score. In particular, the raw model-only output still
includes `persistent transaminase elevations`; the prompt lowers its confidence
so the unchanged 0.95 strict-extension merge retains the exact `transaminase
elevations` gazetteer anchor.

The issue-57 cases verify `diabetes` and `pregnancy` extraction and reject
medication names as disease/phenotype objects. They do not model or display the
aliskiren coadministration condition; that assertion-qualifier work is separate
from this NER metric.
