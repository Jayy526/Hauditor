# Hauditor benchmark

How often does Hauditor catch a hallucination, and how often does it cry wolf? This benchmark answers that with labels nobody has to argue about. It takes honest answers and plants one controlled error in each, so the right answer is known by construction.

Reproduce everything here with:

```bash
hauditor bench benchmarks/corpus_fictional.jsonl benchmarks/corpus_heldout.jsonl --show-misses
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
| `misattributed`: cites the distractor passage | |
| `invented_claim`: adds a forecast nobody made | |
| `wrong_math`: adds a difference computed wrong | |
| `swapped_figures`: 2025's figure given for 2024 and vice versa | |

The generator is in [`src/hauditor/bench/generate.py`](../src/hauditor/bench/generate.py). It is seeded, so every run produces the same cases.

## Results

The checker uses the default settings: arithmetic derivations allowed, the free lexical citation judge, and no LLM anywhere.

## Held-out set (20 passages, written and committed before its first run)

221 cases. Precision **100.0%**, recall **93.3%**, F1 **96.5%**.

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
| swapped_figures | flagged | 2/12 (17%) |
| quote | passed | 20/20 (100%) |
| rounded | passed | 15/15 (100%) |
| rounded_coarse | passed | 9/9 (100%) |
| derived | passed | 12/12 (100%) |
| unit_words | passed | 16/16 (100%) |

## Development set (24 passages, used while fixing Hauditor)

272 cases. Precision **100.0%**, recall **91.4%**, F1 **95.5%**.

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
| swapped_figures | flagged | 0/15 (0%) |
| quote | passed | 24/24 (100%) |
| rounded | passed | 18/18 (100%) |
| rounded_coarse | passed | 8/8 (100%) |
| derived | passed | 15/15 (100%) |
| unit_words | passed | 21/21 (100%) |

## What the benchmark found

The first run on the development set showed Hauditor at 87% precision. Three of the false alarms were real bugs, all fixed in the same change as this benchmark:

1. **Correct arithmetic was flagged as an unsupported citation.** "That is a difference of $105.5 million [nw-12]" passed the number check, but the lexical judge wanted the word "difference" to appear in the source. Words that only describe arithmetic are now ignored by the lexical judge, because the number check already verifies the math.
2. **"About 3,200" was read as exactly 3,200.** Hedge words (about, approximately, roughly and so on) now widen the tolerance to the rounding the number implies.
3. **"$(23.5) million" lost its scale.** The accounting-negative parser didn't allow a closing parenthesis before "million", so a million to billion swap went unnoticed. It now handles `($342)`, `$(342)` and `(342)`, and no longer reads a year in parentheses as negative. "negative 3.2%" and dates like "December 31" are parsed correctly too.

After fixing those, the held-out set was written and committed (see the git history) before it was run for the first time. Its results are the honest estimate.

## Known blind spot: swapped figures

When an answer swaps two numbers that both appear in the source ("revenue was $706.9 million in 2025, compared with $812.4 million in 2024"), every number is grounded and the wording matches, so Hauditor misses it nearly every time. The two held-out catches swapped digits across a million/billion boundary ("$702 billion" and "$5.87 million"), which changed the values themselves. Catching these needs the check to tie each number to what it measures and when, either by aligning the sentence with the source or by using the Claude judge. That's the next improvement to make, and this benchmark will show whether it works.

## Caveats

- The passages describe fictional companies written for this benchmark, in the style of annual-report text. The plan is to add passages from real SEC 10-K filings once this environment can reach sec.gov.
- One planted error per answer, in short answers. Real answers can be longer and messier.
- `wrong_math` misses can happen when a wrong figure happens to equal some other arithmetic combination of source numbers. That is the same coincidence risk noted in the main README.
