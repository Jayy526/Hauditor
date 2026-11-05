# Hauditor

**Find the numbers your RAG app made up.**

Hauditor reads the outputs of a retrieval-augmented generation (RAG) system and checks every number in every answer against the context that was actually retrieved. A number passes only if it appears in a source (allowing for rounding) or can be reproduced from source numbers with one step of plain Python arithmetic. Anything else is flagged as invented.

No LLM is used to check the math. That's the point: a model grading its own arithmetic is how you get confident wrong answers.

```
$ hauditor audit examples/finance_rag.jsonl --html report.html
answers audited            5
numbers checked            10
numbers unsupported        2 (20.0%)
answers with invented num  2 (40.0%)
  [invented-growth] unsupported: 14%
  [invented-margin] unsupported: 34.8%
```

## How a number is judged

| Verdict | Meaning | Example |
|---|---|---|
| grounded | Appears in a source, within the answer's own rounding | "$4.2 billion" vs source "$4,213 million" |
| derived | One arithmetic step from two source numbers | "grew 11.5%" from $4,213M and $3,780M |
| unsupported | Nowhere in the sources and not computable from them | "grew 14%" |

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
- [ ] Citation checking: does the cited passage exist and support the sentence?
- [ ] Benchmark of public 10-K questions with planted hallucinations, to measure Hauditor's own precision and recall
- [ ] Public leaderboard comparing common RAG stacks and models

## License

MIT
