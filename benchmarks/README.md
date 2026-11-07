# Hauditor benchmark

How often does Hauditor catch a hallucination, and how often does it cry wolf? This benchmark answers that with labels nobody has to argue about. It takes honest answers and plants one controlled error in each, so the right answer is known by construction.

Reproduce everything here with:

```bash
hauditor bench benchmarks/corpus_fictional.jsonl benchmarks/corpus_heldout.jsonl benchmarks/corpus_heldout2.jsonl --show-misses
```

## How cases are built

Each passage gets an honest answer that quotes it and cites it, with a second passage from the same company retrieved alongside as a distractor. Then one edit is applied per case:

| Planted hallucination (should be flagged) | Honest variant (should pass) |
|---|---|
| `number_shift`: a figure moved 4-30% | `quote`: the sentence as written |
| `digit_swap`: 812.4 becomes 821.4 | `rounded`: 812.4 becomes 812 |
| `scale_swap`: million becomes billion | `rounded_coarse`: 3,150 becomes "about 3,200" |
| `wrong_year`: a year no source mentions | `derived`: adds a correct difference of two figures |
| `fabricated_citation`: cites an id that wasn't retrieved | `unit_words`: "$812.4 million" becomes "812.4 million dollars" |
| `misattributed`: cites the distractor passage | `reordered`: the two periods restated in the other order |
| `invented_claim`: adds a forecast nobody made | `no_year`: a figure restated without its year |
| `wrong_math`: adds a difference computed wrong | |
| `swapped_figures`: 2025's figure given for 2024 and vice versa | |

The generator is in [`src/hauditor/bench/generate.py`](../src/hauditor/bench/generate.py). It is seeded, so every run produces the same cases.

## Results

The checker uses the default settings: arithmetic derivations allowed, the alignment check on, the free lexical citation judge, and no LLM anywhere.

### Held-out set 2 (20 passages, committed before its first run; the current estimate)

232 cases. Precision **100.0%**, recall **99.4%**, F1 **99.7%**.

| Case type | Should be | Hauditor got it right |
|---|---|---|
| number_shift | flagged | 19/20 (95%) |
| digit_swap | flagged | 20/20 (100%) |
| scale_swap | flagged | 12/12 (100%) |
| wrong_year | flagged | 14/14 (100%) |
| fabricated_citation | flagged | 20/20 (100%) |
| misattributed | flagged | 20/20 (100%) |
| invented_claim | flagged | 20/20 (100%) |
| wrong_math | flagged | 14/14 (100%) |
| swapped_figures | flagged | 14/14 (100%) |
| quote | passed | 20/20 (100%) |
| rounded | passed | 12/12 (100%) |
| rounded_coarse | passed | 11/11 (100%) |
| derived | passed | 14/14 (100%) |
| unit_words | passed | 15/15 (100%) |
| reordered | passed | 3/3 (100%) |
| no_year | passed | 3/3 (100%) |

### Held-out set 1 (20 passages; used as a development set for the alignment check)

229 cases. Precision **100.0%**, recall **100.0%**, F1 **100.0%**.

| Case type | Should be | Hauditor got it right |
|---|---|---|
| number_shift | flagged | 20/20 (100%) |
| digit_swap | flagged | 20/20 (100%) |
| scale_swap | flagged | 12/12 (100%) |
| wrong_year | flagged | 13/13 (100%) |
| fabricated_citation | flagged | 20/20 (100%) |
| misattributed | flagged | 20/20 (100%) |
| invented_claim | flagged | 20/20 (100%) |
| wrong_math | flagged | 12/12 (100%) |
| swapped_figures | flagged | 12/12 (100%) |
| quote | passed | 20/20 (100%) |
| rounded | passed | 15/15 (100%) |
| rounded_coarse | passed | 9/9 (100%) |
| derived | passed | 12/12 (100%) |
| unit_words | passed | 16/16 (100%) |
| reordered | passed | 3/3 (100%) |
| no_year | passed | 5/5 (100%) |

### Development set (24 passages)

291 cases. Precision **100.0%**, recall **99.5%**, F1 **99.7%**.

| Case type | Should be | Hauditor got it right |
|---|---|---|
| number_shift | flagged | 24/24 (100%) |
| digit_swap | flagged | 21/21 (100%) |
| scale_swap | flagged | 16/16 (100%) |
| wrong_year | flagged | 23/23 (100%) |
| fabricated_citation | flagged | 24/24 (100%) |
| misattributed | flagged | 24/24 (100%) |
| invented_claim | flagged | 24/24 (100%) |
| wrong_math | flagged | 14/15 (93%) |
| swapped_figures | flagged | 15/15 (100%) |
| quote | passed | 24/24 (100%) |
| rounded | passed | 18/18 (100%) |
| rounded_coarse | passed | 8/8 (100%) |
| derived | passed | 15/15 (100%) |
| unit_words | passed | 21/21 (100%) |
| reordered | passed | 6/6 (100%) |
| no_year | passed | 13/13 (100%) |

## What the benchmark found

The first run on the development set showed Hauditor at 87% precision. Three of the false alarms were real bugs, all fixed in the same change as this benchmark:

1. **Correct arithmetic was flagged as an unsupported citation.** "That is a difference of $105.5 million [nw-12]" passed the number check, but the lexical judge wanted the word "difference" to appear in the source. Words that only describe arithmetic are now ignored by the lexical judge, because the number check already verifies the math.
2. **"About 3,200" was read as exactly 3,200.** Hedge words (about, approximately, roughly and so on) now widen the tolerance to the rounding the number implies.
3. **"$(23.5) million" lost its scale.** The accounting-negative parser didn't allow a closing parenthesis before "million", so a million to billion swap went unnoticed. It now handles `($342)`, `$(342)` and `(342)`, and no longer reads a year in parentheses as negative. "negative 3.2%" and dates like "December 31" are parsed correctly too.

After fixing those, the held-out set was written and committed (see the git history) before it was run for the first time. Its results are the honest estimate.

## Round two: swapped figures

The first version of this benchmark showed one large blind spot. When an answer swaps two numbers that both appear in the source ("revenue was $706.9 million in 2025, compared with $812.4 million in 2024"), every number is grounded and the wording matches, so Hauditor caught it in only 2 of 12 held-out cases.

The fix is an alignment check ([`alignment.py`](../src/hauditor/alignment.py)). Each number gets a small signature: the year nearest to it (preferring its own clause) and the words just around it. A grounded number is marked **misaligned** when, in the source, a different figure's slot clearly fits the answer's wording better than any slot holding the answer's value. A different year outweighs word overlap, because the second figure in "X in 2025, compared with Y in 2024" has no subject words of its own.

The first attempt over-fired. It flagged honest answers that restate the periods in the other order ("In 2024, net income stood at $48.5 million. By 2025 it was $61.8 million."). To catch that kind of mistake, two honest variant types were added to the benchmark, `reordered` and `no_year`. Picking the nearest year across the whole sentence fixed it.

Held-out set 1 was used while building this, so it no longer counts as held out. A second held-out set was written and committed (with the alignment code, in the same commit) before its first run. Its results above are the current honest estimate. The one miss there is a shifted figure that happened to equal an arithmetic combination of other source numbers.

## Caveats

- The passages describe fictional companies written for this benchmark, in the style of annual-report text. The plan is to add passages from real SEC 10-K filings once this environment can reach sec.gov.
- One planted error per answer, in short answers. Real answers can be longer and messier.
- Honest answers here are mostly close to the source wording. Only a handful are real paraphrases (`reordered`, `no_year`), so precision on freely reworded answers is less certain than these numbers suggest.
- `wrong_math` and `number_shift` misses can happen when a wrong figure happens to equal some other arithmetic combination of source numbers. That is the same coincidence risk noted in the main README.
