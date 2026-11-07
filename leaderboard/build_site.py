"""Turn leaderboard results into a static page (and a Markdown table for the README).

    python leaderboard/build_site.py --results leaderboard/results --out site
"""

from __future__ import annotations

import argparse
import json
import shutil
from html import escape
from pathlib import Path

CATS = ["lookup", "compute", "citation", "unanswerable", "false_premise", "pressure"]
CAT_LABEL = {
    "lookup": "Lookup", "compute": "Compute", "citation": "Citation",
    "unanswerable": "Unanswerable", "false_premise": "False premise", "pressure": "Pressure",
}
CAT_HELP = {
    "lookup": "a figure stated in the documents",
    "compute": "a figure one arithmetic step away",
    "citation": "asked to cite the paragraph used",
    "unanswerable": "the documents don't have it",
    "false_premise": "the question states something untrue",
    "pressure": "pushed to answer no matter what",
}

CSS = """
:root { --bg:#fbfbfa; --card:#fff; --fg:#1c1c1e; --muted:#66666e; --line:#e6e6e3;
        --ok:#17803d; --mid:#9a6700; --bad:#c4291c; --accent:#3b5bdb;
        --ok-bg:#e3f6e8; --mid-bg:#fdf3d1; --bad-bg:#fde8e6; }
@media (prefers-color-scheme: dark) { :root:not([data-theme="light"]) {
  --bg:#0f1115; --card:#161920; --fg:#e8eaed; --muted:#959aa3; --line:#2a2e37;
  --ok:#4cc26f; --mid:#d9a632; --bad:#f2675a; --accent:#8ea4ff;
  --ok-bg:#13291b; --mid-bg:#2c2510; --bad-bg:#331614; } }
* { box-sizing:border-box; }
body { background:var(--bg); color:var(--fg); margin:0;
       font:15px/1.55 ui-sans-serif, system-ui, -apple-system, "Segoe UI", sans-serif; }
main { max-width:1040px; margin:0 auto; padding:40px 16px 80px; }
h1 { font-size:28px; letter-spacing:-0.01em; margin:0 0 6px; }
h2 { font-size:18px; margin:40px 0 10px; }
p { margin:0 0 12px; }
.lede { color:var(--muted); font-size:16px; max-width:720px; margin-bottom:28px; }
a { color:var(--accent); }
.wrap { overflow-x:auto; border:1px solid var(--line); border-radius:10px; background:var(--card); }
table { border-collapse:collapse; width:100%; font-variant-numeric:tabular-nums; }
th, td { padding:10px 12px; text-align:right; white-space:nowrap; }
th { font-size:12px; color:var(--muted); font-weight:600; border-bottom:1px solid var(--line); }
th:nth-child(-n+3), td:nth-child(-n+3) { text-align:left; }
tbody tr + tr td { border-top:1px solid var(--line); }
td.score { font-weight:700; font-size:17px; }
.chip { display:inline-block; min-width:38px; padding:2px 7px; border-radius:6px; text-align:center; font-size:13px; }
.hi { background:var(--ok-bg); color:var(--ok); } .md { background:var(--mid-bg); color:var(--mid); }
.lo { background:var(--bad-bg); color:var(--bad); } .na { color:var(--muted); }
.model { color:var(--muted); font-size:13px; }
.meta { color:var(--muted); font-size:13px; margin-top:10px; }
dl.cats { display:grid; grid-template-columns:max-content 1fr; gap:4px 16px; margin:0 0 12px; }
dl.cats dt { font-weight:600; } dl.cats dd { margin:0; color:var(--muted); }
details { border:1px solid var(--line); border-radius:10px; background:var(--card); margin-bottom:10px; }
summary { cursor:pointer; padding:12px 14px; font-weight:600; }
summary span { color:var(--muted); font-weight:400; margin-left:6px; }
.fail { border-top:1px solid var(--line); padding:12px 14px; }
.fail .q { font-weight:600; margin-bottom:4px; }
.fail .a { white-space:pre-wrap; margin-bottom:6px; }
.fail .why { color:var(--bad); font-size:13px; }
.tag { font-size:11px; text-transform:uppercase; letter-spacing:.04em; color:var(--muted); margin-right:6px; }
code { font-size:13px; background:var(--line); padding:1px 5px; border-radius:4px; }
"""


def load(results: Path) -> list[dict]:
    runs = [json.loads(p.read_text(encoding="utf-8")) for p in sorted(results.glob("*.json"))]
    return sorted(runs, key=lambda r: -r["hauditor_score"])


def _chip(v: float | None) -> str:
    if v is None:
        return '<span class="chip na">n/a</span>'
    cls = "hi" if v >= 80 else "md" if v >= 50 else "lo"
    return f'<span class="chip {cls}">{v:.0f}</span>'


def _failures(run: dict) -> str:
    rows = []
    for p in run["probes"]:
        if p["outcome"] != "fail":
            continue
        a = (p.get("audit") or {}).get("answer", "")
        rows.append(
            f'<div class="fail"><div class="q"><span class="tag">{escape(p["probe"]["category"])}</span>'
            f'{escape(p["probe"]["question"])}</div><div class="a">{escape(a.strip())}</div>'
            f'<div class="why">{escape(p["reason"])}</div></div>'
        )
    n = len(rows)
    head = f'{escape(run["meta"]["stack"])} + {escape(run["meta"]["model"])}<span>{n} failed probe{"s" * (n != 1)}</span>'
    return f"<details><summary>{head}</summary>{''.join(rows) or '<div class=fail>No failures.</div>'}</details>"


def render_html(runs: list[dict]) -> str:
    rows = []
    for i, r in enumerate(runs, 1):
        m, a = r["meta"], r["audit"]
        cats = "".join(f"<td>{_chip((r['categories'].get(c) or {}).get('score'))}</td>" for c in CATS)
        rows.append(
            f"<tr><td>{i}</td><td><b>{escape(m['stack'])}</b></td><td class=model>{escape(m['model'])}</td>"
            f"<td class=score>{r['hauditor_score']:.0f}</td>{cats}"
            f"<td>{a['numbers_unsupported']}</td><td>{a['citations_checked'] - a['citations_by_verdict']['supported']}</td></tr>"
        )
    head = "".join(f'<th title="{escape(CAT_HELP[c])}">{CAT_LABEL[c]}</th>' for c in CATS)
    probes = runs[0]["meta"]["probes"] if runs else 0
    when = max((r["meta"]["run_at"] for r in runs), default="")[:10]
    versions = sorted({f"{k} {v}" for r in runs for k, v in r["meta"]["packages"].items()})
    embed = runs[0]["meta"]["embed_model"] if runs else ""
    cats_dl = "".join(f"<dt>{CAT_LABEL[c]}</dt><dd>{escape(CAT_HELP[c])}</dd>" for c in CATS)
    return f"""<!doctype html>
<html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1">
<title>Hauditor RAG Leaderboard</title><style>{CSS}</style></head><body><main>
<h1>Hauditor RAG leaderboard</h1>
<p class="lede">How often do popular RAG frameworks, used the way their own tutorials use them, make up numbers
and citations? Each stack answers the same {probes} questions about the same fictional annual report, with the same
open model. Hauditor checks every figure and citation against the passages the stack retrieved.</p>
<div class="wrap"><table><thead><tr><th>#</th><th>Stack</th><th>Model</th><th>Score</th>{head}
<th title="numbers in answers that no retrieved passage supports">Invented numbers</th>
<th title="citations to missing or non-supporting passages">Bad citations</th></tr></thead>
<tbody>{''.join(rows)}</tbody></table></div>
<p class="meta">Last run {escape(when)}. Embeddings: {escape(embed)}. {escape(', '.join(versions))}.</p>

<h2>What the columns mean</h2>
<p>The score is the average of the six category scores. In each category a probe passes when the answer invents
nothing (or correctly declines), fails when it states an unsupported number or a bad citation, and gets half
credit when it declines a question the documents could answer. Answers to trick questions that neither decline nor
invent a number can't be graded automatically, so they're left out.</p>
<dl class="cats">{cats_dl}</dl>

<h2>How it's run</h2>
<p>Every stack is built the way its framework's getting-started tutorial builds it: LangChain's RAG tutorial
(in-memory store, 4 passages, the <code>rlm/rag-prompt</code> prompt), LlamaIndex's
<code>VectorStoreIndex.as_query_engine()</code> (2 passages, default prompt), and Haystack's first RAG pipeline
(embedding retriever, 10 passages, tutorial prompt). Models run locally through Ollama at temperature 0.
The documents describe a fictional company, so a model can't answer from memory: any figure that isn't in the
retrieved passages was made up. Code, data and raw results are in
<a href="https://github.com/Jayy526/Hauditor/tree/main/leaderboard">the repository</a>.</p>

<h2>Every failure</h2>
{''.join(_failures(r) for r in runs)}
</main></body></html>
"""


def render_markdown(runs: list[dict]) -> str:
    head = "| # | Stack | Model | Score | " + " | ".join(CAT_LABEL[c] for c in CATS) + " | Invented numbers |"
    lines = [head, "|" + "---|" * (head.count("|") - 1)]
    for i, r in enumerate(runs, 1):
        cats = " | ".join(
            "n/a" if (s := (r["categories"].get(c) or {}).get("score")) is None else f"{s:.0f}" for c in CATS
        )
        lines.append(f"| {i} | {r['meta']['stack']} | {r['meta']['model']} | **{r['hauditor_score']:.0f}** | "
                     f"{cats} | {r['audit']['numbers_unsupported']} |")
    return "\n".join(lines) + "\n"


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--results", default=str(Path(__file__).parent / "results"))
    ap.add_argument("--out", default="site")
    args = ap.parse_args(argv)
    runs = load(Path(args.results))
    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)
    (out / "index.html").write_text(render_html(runs), encoding="utf-8")
    (out / "leaderboard.md").write_text(render_markdown(runs), encoding="utf-8")
    raw = out / "results"
    raw.mkdir(exist_ok=True)
    for p in Path(args.results).glob("*.json"):
        shutil.copy(p, raw / p.name)
    print(render_markdown(runs))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
