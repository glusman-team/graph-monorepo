---
name: DAKP NER regression
about: Report a missed, spurious, or incorrectly labeled disease or phenotype span
title: "[DAKP NER]: "
---

Describe the observed extraction and link the public source. Do not include patient data.

Replace the example with the exact public snippet and all expected object mentions.
Use one JSON object per line, unique IDs, and `mentions: []` for negative controls.
DailyMed requires `loinc` (`34066-1` or `34070-3`); FAERS omits it. For a repeated
surface, set its zero-based character `start`. Keep the `ner-gold` fence.

```ner-gold
{"id":"replace-this-example","source":"faers","text":"Migraine","mentions":[{"surface":"Migraine","type":"Disease"}]}
```

Delete the example rather than submitting it unchanged if no expected labels are known.
