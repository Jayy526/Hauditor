from collections import Counter
from pathlib import Path

from hauditor.bench.generate import HALLUCINATED, HONEST, build_cases, load_cases, load_corpus, save_cases
from hauditor.bench.run import run_bench
from hauditor.cli import main
from hauditor.numbers import extract

ROOT = Path(__file__).resolve().parent.parent
CORPUS = load_corpus(ROOT / "benchmarks" / "corpus_fictional.jsonl")


def test_cases_are_reproducible_and_labelled():
    a, b = build_cases(CORPUS, seed=3), build_cases(CORPUS, seed=3)
    assert [c.record.answer for c in a] == [c.record.answer for c in b]
    for c in a:
        assert c.hallucinated == (c.kind in HALLUCINATED)
        assert c.kind in HALLUCINATED + HONEST


def test_every_kind_is_generated():
    kinds = Counter(c.kind for c in build_cases(CORPUS))
    assert set(kinds) == set(HALLUCINATED + HONEST)


def test_edits_change_exactly_what_they_say():
    cases = {c.record.id: c for c in build_cases(CORPUS)}
    shift = cases["nw-30:number_shift"]
    old, new = shift.note.split(" -> ")
    assert new in shift.record.answer and old not in shift.record.answer.split("[")[0].replace("48.5", "")
    assert "billion" in cases["nw-12:scale_swap"].record.answer
    assert cases["nw-12:quote"].record.answer.endswith("[nw-12]")


def test_roundtrip(tmp_path):
    cases = build_cases(CORPUS[:3])
    save_cases(cases, tmp_path / "c.jsonl")
    back = load_cases(tmp_path / "c.jsonl")
    assert [(c.record.answer, c.hallucinated, c.kind) for c in back] == [
        (c.record.answer, c.hallucinated, c.kind) for c in cases
    ]


def test_metrics():
    res = run_bench(build_cases(CORPUS))
    d = res.to_dict()
    assert d["confusion"]["tp"] + d["confusion"]["fn"] == sum(1 for c in build_cases(CORPUS) if c.hallucinated)
    # Guard against regressions: honest answers must keep passing.
    assert res.precision >= 0.99
    assert res.by_kind["number_shift"][0] == res.by_kind["number_shift"][1]


def test_cli_bench(tmp_path, capsys):
    md = tmp_path / "r.md"
    assert main(["bench", str(ROOT / "benchmarks" / "corpus_heldout.jsonl"), "--markdown", str(md)]) == 0
    assert "Precision" in md.read_text()


# Parser fixes the benchmark surfaced ------------------------------------------

def test_accounting_negative_keeps_scale():
    [q] = extract("Net loss was $(23.5) million")
    assert q.value == -23.5e6


def test_year_in_parentheses_is_not_negative():
    [q] = extract("results (2024)")
    assert q.value == 2024


def test_sign_word():
    [q] = extract("margin of negative 3.2%")
    assert q.value == -3.2


def test_day_of_month_skipped():
    assert [q.raw for q in extract("as of December 31, 2025", ignore_small_ints=0)] == ["2025"]


def test_hedge_widens_tolerance():
    [q] = extract("about 3,200 people")
    assert q.matches(3150) and not q.matches(3300)
    [q] = extract("3,200 people")
    assert not q.matches(3150)
