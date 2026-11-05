"""Command line entry point.

``hauditor audit outputs.jsonl``      grade answers an app already gave
``hauditor probe --target t.json``    write tough questions for a live app
``hauditor live --target t.json``     ask them, grade the answers, score the app
"""

from __future__ import annotations

import argparse
import json
import sys

from . import __version__
from .audit import audit, load_jsonl
from .citations import CitationChecker, LexicalJudge
from .grounding import GroundingChecker, Source
from .report import render_html, render_live_html


def _add_checker_args(a: argparse.ArgumentParser) -> None:
    a.add_argument("--strict", action="store_true", help="only accept numbers quoted from sources, no arithmetic")
    a.add_argument("--judge", choices=["lexical", "claude"], default="lexical",
                   help="how to decide whether a cited passage supports a sentence (default: lexical)")
    a.add_argument("--judge-model", default=None, help="Claude model for --judge claude")
    a.add_argument("--html", metavar="FILE", help="write an HTML report here")
    a.add_argument("--json", metavar="FILE", help="write full results as JSON here")


def _checkers(args) -> tuple[GroundingChecker, CitationChecker]:
    checker = GroundingChecker(allow_derived=not args.strict)
    if args.judge == "claude":
        from .claude_judge import DEFAULT_MODEL, ClaudeJudge

        judge = ClaudeJudge(model=args.judge_model or DEFAULT_MODEL)
    else:
        judge = LexicalJudge()
    return checker, CitationChecker(judge=judge, numbers=checker)


def _load_target(path: str):
    from .live import HttpTarget

    return HttpTarget.from_config(path)


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(prog="hauditor", description="Find the numbers and citations a RAG app made up.")
    p.add_argument("--version", action="version", version=f"hauditor {__version__}")
    sub = p.add_subparsers(dest="cmd", required=True)

    a = sub.add_parser("audit", help="check a JSONL file of RAG outputs")
    a.add_argument("path", help="JSONL with question, contexts and answer per line")
    _add_checker_args(a)
    a.add_argument("--fail-above", type=float, metavar="RATE",
                   help="exit 1 if the share of flagged answers exceeds RATE (for CI)")

    pr = sub.add_parser("probe", help="generate tough questions for a live RAG app (needs Claude)")
    pr.add_argument("--target", required=True, help="JSON config describing the app's HTTP endpoint")
    pr.add_argument("--docs", metavar="FILE", nargs="*",
                    help="text files from the app's corpus to write questions from (default: discover by asking)")
    pr.add_argument("--per-category", type=int, default=5)
    pr.add_argument("--no-pressure", action="store_true", help="skip the pressure variants")
    pr.add_argument("--model", default="claude-opus-5-5")
    pr.add_argument("--out", default="probes.jsonl")

    lv = sub.add_parser("live", help="ask a live RAG app the probe questions and score it")
    lv.add_argument("--target", required=True, help="JSON config describing the app's HTTP endpoint")
    lv.add_argument("--probes", required=True, help="JSONL of probes, from `hauditor probe` or hand-written")
    _add_checker_args(lv)
    lv.add_argument("--fail-below", type=float, metavar="SCORE", help="exit 1 if the Hauditor score is below SCORE")

    args = p.parse_args(argv)
    if args.cmd == "audit":
        return _cmd_audit(args)
    if args.cmd == "probe":
        return _cmd_probe(args)
    return _cmd_live(args)


def _cmd_audit(args) -> int:
    checker, cite_checker = _checkers(args)
    summary = audit(load_jsonl(args.path), checker, cite_checker)
    s = summary.to_dict()

    print(f"answers audited            {s['answers']}")
    print(f"numbers checked            {s['numbers_checked']}")
    print(f"numbers unsupported        {s['numbers_unsupported']} ({s['unsupported_number_rate']:.1%})")
    print(f"citations checked          {s['citations_checked']}")
    print(f"citations bad              {s['citations_checked'] - s['citations_by_verdict']['supported']}"
          f" ({s['bad_citation_rate']:.1%})")
    print(f"answers flagged            {s['answers_flagged']} ({s['answer_hallucination_rate']:.1%})")
    for r in summary.results:
        for n in r.unsupported:
            print(f"  [{r.record.id}] unsupported number: {n.quantity.raw}")
        for c in r.bad_citations:
            print(f"  [{r.record.id}] {c.verdict.value.replace('_', ' ')} citation: {c.detail}")

    if args.json:
        with open(args.json, "w", encoding="utf-8") as f:
            json.dump({"summary": s, "results": [r.to_dict() for r in summary.results]}, f, indent=2)
    if args.html:
        with open(args.html, "w", encoding="utf-8") as f:
            f.write(render_html(summary))

    if args.fail_above is not None and s["answer_hallucination_rate"] > args.fail_above:
        return 1
    return 0


def _cmd_probe(args) -> int:
    from .live import ClaudeProbeGenerator, add_pressure, discover, save_probes

    if args.docs:
        corpus = []
        for path in args.docs:
            with open(path, encoding="utf-8") as f:
                text = f.read()
            corpus += [Source(f"{path}#{i}", para) for i, para in enumerate(text.split("\n\n")) if para.strip()]
    else:
        corpus = discover(_load_target(args.target))
    if not corpus:
        print("No documents found. The app returned no sources; pass --docs instead.", file=sys.stderr)
        return 2
    print(f"writing questions from {len(corpus)} passages...")
    probes = ClaudeProbeGenerator(model=args.model).generate(corpus, per_category=args.per_category)
    if not args.no_pressure:
        probes += add_pressure(probes)
    save_probes(probes, args.out)
    print(f"wrote {len(probes)} probes to {args.out}")
    return 0


def _cmd_live(args) -> int:
    from .live import load_probes, run_live

    target = _load_target(args.target)
    probes = load_probes(args.probes)
    checker, cite_checker = _checkers(args)

    def progress(i, n, probe):
        print(f"\r  asked {i}/{n}", end="", file=sys.stderr, flush=True)

    report = run_live(target, probes, checker, cite_checker, on_progress=progress)
    print(file=sys.stderr)
    d = report.to_dict()

    print(f"Hauditor score  {d['hauditor_score']:.0f}/100   ({target.name}, {len(probes)} probes)")
    print(f"  {'type':<15}{'score':>6}{'pass':>6}{'fail':>6}{'abst':>6}{'unver':>7}{'err':>5}")
    for cat, c in d["categories"].items():
        score = "" if c["score"] is None else f"{c['score']:.0f}"
        print(f"  {cat:<15}{score:>6}{c['pass']:>6}{c['fail']:>6}{c['abstain']:>6}{c['unverified']:>7}{c['error']:>5}")
    for r in report.results:
        if r.outcome.value == "fail":
            print(f"  FAIL [{r.probe.id}] {r.reason}")

    if args.json:
        with open(args.json, "w", encoding="utf-8") as f:
            json.dump(d, f, indent=2)
    if args.html:
        with open(args.html, "w", encoding="utf-8") as f:
            f.write(render_live_html(report))

    if args.fail_below is not None and report.score < args.fail_below:
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
