---
name: release-dakp
description: Cut and publish a DAKP release - rebuild the graph with an explicit DAKP_PUBLICATION_VERSION on the production build host, verify the screens held, then publish to the Hugging Face dataset (upload version dir, update the card, prune the superseded dir, tag). Use when the user says "cut a dakp release", "publish a new version to HF", "rebuild and upload the dataset", or after landing graph-changing fixes (source screens, denylists, resolution changes).
---

# Release DAKP (rebuild -> verify -> publish to HF)

A release is one versioned KGX rebuild plus its Hugging Face publication. The version is
explicit (`DAKP_PUBLICATION_VERSION`), not derived from tags. Patch bump when the change
corrects or screens content (1.25.0 -> 1.25.1); minor bump when it adds a source or predicate.

## 1. Rebuild

On the build host holding the production fullmap and the farmed `$DAKP_WORKDIR` (the port's
op-index cache makes acquire/extract/shape stages warm; only config-dependent stages re-run):

```sh
export DAKP_PUBLICATION_VERSION=<new>          # e.g. 1.25.2
# the in-process executor run (sqlite + OOM history: never the default multiprocess executor)
.venv/bin/dagster job execute -j dakp_build -m dakp_dag.definitions -c dagster-inprocess.yaml
```

Long run: launch it in a remote tmux session, not the raw SSH connection. Stdout through a
pipe is block-buffered - read progress from the Dagster run db or the compute logs under
`$DAGSTER_HOME`, not from the empty tee'd log. The versioned outputs land in
`$DAKP_WORKDIR/kgx/DRUG_APPROVALS_KP_<version>.{nodes,edges}.ndjson` + `.RIG.yaml`.

## 2. Verify before publishing (all mandatory)

- `qc.json` at `$MO_PUBLISHED_ROOT/dakp/kg/`: `dangling_edges: 0`, node/edge counts sane.
- `tablassert validate-kgx <nodes> <edges>`: "KGX output is Biolink-compliant", 0 failures.
- RIG: correct version everywhere, `infores:icd10cm` present, artifact URLs point at the new
  `https://huggingface.co/datasets/SkyeAv/drug-approvals-kp/resolve/main/<version>/` dir.
- Screens hold (the point of the last releases): no node id from
  `icd10cm_curies.SCREENED_CURIES` or the water family; no subject node whose normalized name
  is in `icd10cm_denylist.SCREENED_SUBJECTS` minus `LABEL_BACKED_EXCEPTIONS`; GHB/GBL
  (`CHEBI:30830`, `CHEBI:42639`, `UMLS:C0673093`, `UMLS:C0000503`) present.
- Diff vs the previous release explains every removed/added edge. Strongest form: load the
  previous release's release.qc parquets from `$DAKP_WORKDIR/.tablassert/store/` (they keep
  `subject_pre_resolution` raw wordings) and check each removed edge against the denied rows,
  and each new release parquet for denied-row leaks. A verifier script pattern lives in the
  build host's home (`verify-*.py`).

## 3. Publish to HF (runbook order, in one script that stops on surprises)

1. Stage `<version>/` with the ndjson pair + RIG; record bytes + MD5 for the card.
2. Upload the version dir to `SkyeAv/drug-approvals-kp` (main).
3. Update the card (`README.md`): swap both `config_name`s + `data_files` to the new version
   (`default: true` stays on the `_edges` config), add the releases-table row, refresh the
   checksums/details block, counts in Schema, and the Loading snippet.
4. Prune the superseded version dir from main - only after confirming its `v<old>` tag exists.
5. Create dataset tag `v<new>` after the prune.
6. Verify: re-download all three files from main and from the old tag, MD5 match; datasets-server
   `/splits` + `/first-rows` return 200 for both configs (the 240MB edges file takes minutes to
   parse; a 500 "server is busier than usual" means retry, not failure).

`publish.py` in the release staging dir implements 2-5 with assertions; copy and sed the
version. HF publication follows the `dakp-release` runbook: present the plan for sign-off
before mutating the public dataset when the request did not already authorize the upload.

## Gotchas

- Gated tests need the real Tablassert: `uv run --with 'tablassert[cli,log,qc]==22.2.0'
  pytest` inside `pipeline/`; plain `uv run pytest` silently skips them (importorskip).
- The poison screen derives in-DAG (`derive_icd10cm_screen` always;
  `derive_icd10cm_curies` when a fullmap is configured). A changed ICD-10-CM release or
  fullmap therefore flows into the configs on the next build with NO manual step; when it
  does, also refresh the checked-in snapshots via `tests/eval/icd10cm_{denylist,curies}.py`
  so the offline default and the build-time derivation agree, then regenerate
  `tests/fixtures/tables/*.yaml` goldens.
- Changing table-config content changes the Tablassert config fingerprint: the tablassert and
  kg stages re-run warm-cached; shapes and extracts do not. Budget the run accordingly.
