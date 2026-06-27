# Golden questions (synthetic example)

A tiny, synthetic golden-question set for the bundled `sample_corpus.jsonl`. It
demonstrates the evaluation format only — it contains no real or company data.

Columns: `ID | question | expected sources | focus`. The optional extension
columns (`expected_anchors | holdout | paraphrase`) are ignored for plain rows.

## testing sub-KB

| ID | question | expected sources | focus |
|---|---|---|---|
| TST-001 | What does DEVICE_SEQ mean? | `1000001` | parameter meaning |
| TST-002 | How do you tell devices apart in a multi-device run? | `1000001` | DEVICE_SEQ usage |
| TST-003 | Where do you look at task logs? | `1000005` | log inspection |

## release sub-KB

| ID | question | expected sources | focus |
|---|---|---|---|
| REL-001 | What is the difference between LogicType Change and Compare? | `1000002` | parameter semantics |
| REL-002 | Which field is the source json file for migration? | `1000002` | sourceFileName |

## incident sub-KB

| ID | question | expected sources | focus |
|---|---|---|---|
| INC-001 | Why did downloads fail on mobile networks? | `1000003` | root cause |
| INC-002 | What test improvement followed the download postmortem? | `1000003` | test improvement |
