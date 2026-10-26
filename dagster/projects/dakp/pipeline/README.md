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
