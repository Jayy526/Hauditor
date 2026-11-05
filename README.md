# Hauditor

**Find the numbers and citations your RAG app made up.**

Hauditor reads the outputs of a retrieval-augmented generation (RAG) system and checks every answer against the context that was actually retrieved:

- **Numbers.** A number passes only if it appears in a source (allowing for rounding) or can be reproduced from source numbers with one step of plain Python arithmetic. Anything else is flagged as invented.
- **Citations.** Each cited source must exist, must contain the numbers in the sentence that cites it, and must actually say what the sentence says.

No LLM is used to check the math. That's the point: a model grading its own arithmetic is how you get confident wrong answers.

```
$ hauditor audit examples/finance_rag.jsonl --html report.html
answers audited            9
numbers checked            18
numbers unsupported        3 (16.7%)
citations checked          6
citations bad              3 (50.0%)
answers flagged            5 (55.6%)
  [invented-growth] unsupported number: 14%
  [invented-margin] unsupported number: 34.8%
  [cited-fake-source] unsupported number: 2025
  [cited-fake-source] fabricated citation: cites 10k-p87, which is not among the retrieved sources
  [cited-wrong-source] wrong numbers citation: 61.2% is in 10k-p44, not the cited source
  [cited-unsupported-text] not supported citation: lexical: 1/7 content words found; missing: acquisition, ...
```

## How a number is judged

| Verdict | Meaning | Example |
|---|---|---|
| grounded | Appears in a source, within the answer's own rounding | "$4.2 billion" vs source "$4,213 million" |
| derived | One arithmetic step from two source numbers | "grew 11.5%" from $4,213M and $3,780M |
| unsupported | Nowhere in the sources and not computable from them | "grew 14%" |

## How a citation is judged

Inline markers like `[1]`, `[2, 3]`, `[^4]` or `[10k-p41]` are attached to the sentence they sit in. Numeric markers resolve by position, others by source id. Brackets that aren't citations (`[sic]`, markdown links) are ignored. Apps that return citations separately can pass them as `"citations": [{"claim": "...", "source_ids": ["..."]}]`.

| Verdict | Meaning |
|---|---|
| supported | The source exists, contains the sentence's numbers, and the judge agrees it backs the wording |
| fabricated | The cited id isn't among the retrieved sources |
| wrong_numbers | A number in the sentence isn't in the cited source. The detail says whether another source has it (misattributed) or none does (invented) |
| not_supported | The source exists but doesn't say what the sentence says |

Numbers are always checked in Python first, so a judge can never wave through a made-up figure. Wording is checked by a judge:

- `--judge lexical` (default): free, deterministic content-word overlap. Good at catching citations to the wrong document, weak on paraphrase.
- `--judge claude`: asks Claude whether the passage supports the sentence, with structured output. Needs `pip install -e '.[claude]'` and an Anthropic API key. Pick the model with `--judge-model`. Requests opt into server-side fallback so a declined request is retried on another model rather than failing the audit.

## Number details

It handles currency symbols, scale words (k, million, bn), percentages and basis points, accounting negatives like `(342)`, percent vs fraction (`3.4%` vs `0.034`), and tables printed "in millions". Years must be quoted exactly. Bare small integers ("3 reasons") are ignored.

## Install and run

```bash
pip install -e '.[dev]'
hauditor audit your_outputs.jsonl --html report.html --json results.json
```

Each line of the input is one interaction:

```json
{"id": "q1", "question": "...", "contexts": [{"id": "doc-3", "text": "..."}], "answer": "..."}
```

`contexts` may also be a list of plain strings. Useful flags:

- `--strict` accepts only quoted numbers, no arithmetic.
- `--fail-above 0.05` exits non-zero when more than 5% of answers contain an invented number, so you can gate a CI pipeline on it.

From Python:

```python
from hauditor import GroundingChecker, Source

checks = GroundingChecker().check(
    "Revenue grew 14% to $4.2 billion.",
    [Source("10k", "Revenue was $4,213 million vs $3,780 million last year.")],
)
for c in checks:
    print(c.quantity.raw, c.verdict.value, c.evidence)
```

## Known limits

Derivations search every pair of source numbers, so on very long contexts a made-up number can occasionally match by coincidence. Use `--strict` when answers should only quote. The planned benchmark exists to measure exactly this.

## Roadmap

- [x] Number grounding with rounding-aware matching and one-step derivations
- [x] CLI, JSON output and HTML report
- [x] Citation checking: does the cited passage exist, contain the numbers, and support the sentence?
- [ ] Benchmark of public 10-K questions with planted hallucinations, to measure Hauditor's own precision and recall
- [ ] Public leaderboard comparing common RAG stacks and models

## License

MIT
