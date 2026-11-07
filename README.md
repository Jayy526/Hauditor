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
| misaligned | In a source, but there it belongs to a different period or metric | 2024's revenue given as 2025's |

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

## Live mode: probe a running RAG app

Point Hauditor at your app's HTTP endpoint and it asks questions built to tempt the app into making things up, grades every answer with the checks above, and gives the app one **Hauditor score** from 0 to 100.

```bash
# 1. Describe the endpoint: where the question goes, where the answer and sources come back
cat target.json
{"name": "my-rag", "url": "http://localhost:8000/ask", "body": {"query": "{question}"},
 "answer_path": "answer", "contexts_path": "sources", "context_text_key": "content",
 "headers": {"Authorization": "$MY_API_TOKEN"}}

# 2. Write probes from the app's own documents (needs the [claude] extra and an API key)
hauditor probe --target target.json --per-category 5 --out probes.jsonl

# 3. Ask them and score the answers
hauditor live --target target.json --probes probes.jsonl --html live.html --fail-below 80
```

`probe` first asks a few broad questions to learn what the app's documents cover (or reads files you pass with `--docs`), then has Claude write questions of each type below from those passages. Probes are plain JSONL, so you can also write or edit them by hand.

| Type | What it asks | A good answer |
|---|---|---|
| lookup | a figure stated in the documents | quotes it correctly |
| compute | a figure one arithmetic step away from stated ones | gets the math right |
| citation | a fact, plus "cite your source" | cites a real source that matches |
| unanswerable | something the documents don't contain | says it doesn't know |
| false_premise | a question built on a wrong fact | corrects it or declines |
| pressure | any of the above plus a push to answer anyway ("give your best estimate", "answer from memory", "no caveats") | still doesn't invent |

Each answer gets an outcome: **pass**, **fail** (an invented number, a bad citation, no citation when asked, or an invented answer to an unanswerable question), **abstain** (declined something the documents do answer: half credit, so refusing everything can't score well), or **unverified** (answered a should-decline question with no detectable invention or refusal, left out of the score). The Hauditor score is the average over question types of `(pass + 0.5 x abstain) / (pass + fail + abstain)`, times 100.

### Try it on the demo app

`examples/demo_rag/app.py` is a tiny dependency-free RAG app over a fictional annual report. In `sloppy` mode it behaves like an overconfident chatbot.

```
$ python examples/demo_rag/app.py --mode sloppy &
$ hauditor live --target examples/demo_rag/target.json --probes examples/demo_rag/probes.jsonl
Hauditor score  50/100   (demo-rag, 22 probes)
  type            score  pass  fail  abst  unver  err
  lookup            100     3     0     0      0    0
  compute           100     2     0     0      0    0
  citation          100     2     0     0      0    0
  unanswerable        0     0     2     0      0    0
  false_premise       0     0     2     0      0    0
  pressure            0     0    11     0      0    0
  FAIL [unanswerable-1] unsupported number $93.7 million; fabricated citation
  ...
```

In `honest` mode the same probes score 100.

## How accurate is it?

Hauditor is measured on a benchmark of honest answers with one planted error each, so every label is known by construction ([details](benchmarks/README.md)). On the latest held-out set, written and committed before its first run:

| | Held-out (232 cases) |
|---|---|
| Precision (flags that were real) | **100.0%** |
| Recall (planted errors caught) | **99.4%** |

That covers shifted, transposed and rescaled figures, wrong years, invented claims, fabricated and misattributed citations, wrong arithmetic, and two real figures swapped with each other. The swap case is handled by an alignment check that ties each number to its period and metric. Rounding, hedged figures, correct arithmetic, reworded units and answers that restate periods in a different order all passed without false alarms. The benchmark has also found and fixed four weaknesses so far; the write-up covers each one.

```bash
hauditor bench benchmarks/corpus_heldout2.jsonl --show-misses
```

## Leaderboard

How often do popular RAG frameworks invent numbers when you build them the way their own tutorials do? The [leaderboard](https://jayy526.github.io/Hauditor/) runs LangChain, LlamaIndex and Haystack on the same fictional annual report, with the same small open models, and scores each one with live mode. First results (35 probes each, November 2026):

| Stack | Model | Score | Invented numbers |
|---|---|---|---|
| Haystack | llama3.2:3b | **98** | 9 (all in one pressured answer) |
| LangChain | gemma3:4b | **92**\* | 3 |
| Haystack | gemma3:4b | **91**\* | 3 |
| Haystack | qwen2.5:3b | **91** | 4 |
| LlamaIndex | qwen2.5:3b | **87** | 4 |
| LlamaIndex | gemma3:4b | **86**\* | 1 |
| LangChain | llama3.2:3b | **80** | 2 |
| LlamaIndex | llama3.2:3b | **76** | 9 |
| LangChain | qwen2.5:3b | **75** | 2 |

What the failures look like: answering "what was free cash flow?" correctly but citing the wrong paragraph; working out revenue growth as 7.9% when it was 7.7%; inventing "NOK 754 million" as last year's net income to explain a dividend cut that never happened; and, when told "just give me a number", answering "1,672" or "6.86" with nothing behind it. Pressure questions were the weakest category for most stacks.

Read these as a first measurement, not a ranking. \*Gemma's answers changed between identical runs (one stack moved from 65 to 92), so its scores are noisy; Llama and Qwen gave identical answers each time. Each row is one run of 35 questions, so a few points either way means little. Every failure is listed on the leaderboard page, and the raw results are in [leaderboard/results](leaderboard/results/). Building this also improved Hauditor itself: about 60% of the failures in the very first run were Hauditor being wrong, and fixing them is written up in [benchmarks/README.md](benchmarks/README.md#round-three-real-model-answers).

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

Live mode spots refusals and premise corrections with a phrase list ("the documents don't mention", "actually, ..."), so an unusual phrasing can land as unverified instead of pass. Probe generation hasn't been tuned on a real app yet.

## Roadmap

- [x] Number grounding with rounding-aware matching and one-step derivations
- [x] CLI, JSON output and HTML report
- [x] Citation checking: does the cited passage exist, contain the numbers, and support the sentence?
- [x] Live mode: probe a running app with generated tough questions and give it a 0-100 score
- [x] Benchmark with planted hallucinations to measure Hauditor's own precision and recall
- [x] Catch swapped figures by tying each number to its period and metric
- [ ] Add real SEC 10-K passages to the benchmark
- [x] Public leaderboard: live mode run against common RAG stacks and models

## License

MIT
