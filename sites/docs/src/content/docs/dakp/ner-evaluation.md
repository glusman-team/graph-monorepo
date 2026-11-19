---
title: NER evaluation
---

## Annotation policy

Gold spans are the maximal disease/phenotype mention spans a contraindication or indication targets. type is 'Disease' (a diagnosed condition) or 'PhenotypicFeature' (a clinical finding/symptom/condition-state). Borderline condition-states (hypersensitivity, pregnancy, bleeding, seizures, elevations, pain, nausea, vomiting, fatigue) are labeled 'PhenotypicFeature'; named disorders/syndromes are 'Disease'. 'Maximal' covers qualifiers that change the concept -- anatomical/etiological ('pulmonary hypertension', 'portal hypertension'), severity ('severe heart failure'), and course ('active liver disease') -- because the qualified term denotes a different condition than its head. It does NOT cover temporal or evidential hedges ('recent', 'a history of', 'known', 'suspected'), which say something about the patient record rather than about the condition: gold for 'a recent myocardial infarction' is 'myocardial infarction'. The duration descriptor 'persistent' is likewise excluded when it modifies a clinical finding's course: gold for 'persistent transaminase elevations' is 'transaminase elevations'. Population descriptors (e.g. 'women of childbearing potential') are subject populations, NOT disease/phenotype mentions, and get no span.

## Score and time a run

From `dagster/projects/dakp/pipeline`:

```sh
uv run python tests/eval/ner_gold_eval.py --offline --require-perfect \
  --max-elapsed-seconds 5 --output /tmp/ner-gold-offline.json
```

Drop `--offline` and add `--group ner` after `uv run` to use the configured
production checkpoint. It never falls back to the embedded gazetteer. Pick a
runtime budget on the same host, hardware, corpus, and cache state you benchmark.
The example's five-second budget is for offline use, not a production default.

The report includes exact-match precision, recall, F1, and span counts overall,
by source, and by entity type. A wrong type is a miss and an extra. Duplicates
count as extras. Exact-case rate requires no misses or extras in the whole row.
Negative-case accuracy measures how often no objects appear in negative controls;
it is null for a group without controls. Span metrics with zero denominator are zero.

Elapsed time includes extraction (including lazy model startup and teardown) and
scoring, but not file loading, Python imports, or report rendering. The runtime
gate fails when elapsed time exceeds `--max-elapsed-seconds`. Set `--min-f1` to
require a minimum micro F1, or `--require-perfect` for zero misses and extras.
Any failed gate exits 1 and still writes the requested report. The corpus is a
regression suite, not independent clinical validation.

Use `NERDataset.load(*paths)` and `NEREvaluator(dataset).run(offline=True)` from
`dakp_pipeline.ner.eval_dataset` and `dakp_pipeline.ner.evaluator` to reuse the
same test system from Python. Call `score(predictions)` to score saved predictions
keyed by case ID, with `surface`, `start`, `end`, and `type` for each span.
