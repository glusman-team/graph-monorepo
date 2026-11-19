---
title: NER evaluation
---

## Annotation policy

Gold spans are the maximal disease/phenotype mention spans a contraindication or indication targets. type is 'Disease' (a diagnosed condition) or 'PhenotypicFeature' (a clinical finding/symptom/condition-state). Borderline condition-states (hypersensitivity, pregnancy, bleeding, seizures, elevations, pain, nausea, vomiting, fatigue) are labeled 'PhenotypicFeature'; named disorders/syndromes are 'Disease'. 'Maximal' covers qualifiers that change the concept -- anatomical/etiological ('pulmonary hypertension', 'portal hypertension'), severity ('severe heart failure'), and course ('active liver disease') -- because the qualified term denotes a different condition than its head. It does NOT cover temporal or evidential hedges ('recent', 'a history of', 'known', 'suspected'), which say something about the patient record rather than about the condition: gold for 'a recent myocardial infarction' is 'myocardial infarction'. The duration descriptor 'persistent' is likewise excluded when it modifies a clinical finding's course: gold for 'persistent transaminase elevations' is 'transaminase elevations'. Population descriptors (e.g. 'women of childbearing potential') are subject populations, NOT disease/phenotype mentions, and get no span.
