"""Render an audit as a single self-contained HTML page."""

from __future__ import annotations

from html import escape

from .audit import AuditSummary, RecordResult
from .grounding import Verdict

_CSS = """
:root { --bg:#fff; --fg:#1b1b1f; --muted:#62626b; --line:#e4e4e8;
        --ok:#1a7f37; --warn:#9a6700; --bad:#cf222e;
        --ok-bg:#dafbe1; --warn-bg:#fff8c5; --bad-bg:#ffebe9; }
@media (prefers-color-scheme: dark) {
  :root { --bg:#0d1117; --fg:#e6edf3; --muted:#8d96a0; --line:#30363d;
          --ok:#3fb950; --warn:#d29922; --bad:#f85149;
          --ok-bg:#12261a; --warn-bg:#2b230f; --bad-bg:#2d1214; } }
body { background:var(--bg); color:var(--fg); margin:0;
       font:15px/1.55 system-ui, -apple-system, Segoe UI, sans-serif; }
main { max-width:900px; margin:0 auto; padding:24px 16px 64px; }
h1 { font-size:22px; margin:0 0 4px; }
.sub { color:var(--muted); margin:0 0 24px; }
.stats { display:grid; grid-template-columns:repeat(auto-fit,minmax(160px,1fr)); gap:12px; margin-bottom:32px; }
.stat { border:1px solid var(--line); border-radius:8px; padding:12px 14px; }
.stat b { display:block; font-size:24px; font-variant-numeric:tabular-nums; }
.stat span { color:var(--muted); font-size:13px; }
article { border:1px solid var(--line); border-radius:8px; padding:14px 16px; margin-bottom:14px; }
article h2 { font-size:14px; margin:0 0 6px; color:var(--muted); font-weight:600; }
.q { margin:0 0 8px; font-weight:600; }
.a { margin:0 0 10px; }
mark { border-radius:3px; padding:0 2px; color:inherit; }
mark.grounded { background:var(--ok-bg); }
mark.derived { background:var(--warn-bg); }
mark.unsupported { background:var(--bad-bg); outline:1px solid var(--bad); }
table { width:100%; border-collapse:collapse; font-size:13px; }
td { border-top:1px solid var(--line); padding:5px 6px; vertical-align:top; }
td.v-grounded, td.v-supported { color:var(--ok); }
td.v-fabricated, td.v-wrong_numbers, td.v-not_supported { color:var(--bad); font-weight:600; }
h3 { font-size:13px; margin:12px 0 4px; color:var(--muted); font-weight:600; }
td.v-grounded { color:var(--ok); } td.v-derived { color:var(--warn); } td.v-unsupported { color:var(--bad); font-weight:600; }
"""


def _highlight(r: RecordResult) -> str:
    text = r.record.answer
    out, pos = [], 0
    for n in sorted(r.numbers, key=lambda n: n.quantity.start):
        q = n.quantity
        out.append(escape(text[pos:q.start]))
        out.append(f'<mark class="{n.verdict.value}">{escape(text[q.start:q.end])}</mark>')
        pos = q.end
    out.append(escape(text[pos:]))
    return "".join(out)


def _row(n) -> str:
    return (
        f"<tr><td>{escape(n.quantity.raw)}</td>"
        f'<td class="v-{n.verdict.value}">{n.verdict.value}</td>'
        f"<td>{escape(n.evidence)}</td><td>{escape(n.source_id or '')}</td></tr>"
    )


def _cite_row(c) -> str:
    return (
        f"<tr><td>{escape(c.claim.text)}</td><td>{escape(', '.join(c.claim.source_ids))}</td>"
        f'<td class="v-{c.verdict.value}">{c.verdict.value.replace("_", " ")}</td>'
        f"<td>{escape(c.detail)}</td></tr>"
    )


def render_html(summary: AuditSummary, title: str = "Hauditor report") -> str:
    s = summary.to_dict()
    stats = [
        (f"{s['answer_hallucination_rate']:.0%}", "answers flagged"),
        (f"{s['unsupported_number_rate']:.0%}", f"of {s['numbers_checked']} numbers unsupported"),
        (f"{s['bad_citation_rate']:.0%}", f"of {s['citations_checked']} citations bad"),
        (str(s["answers"]), "answers audited"),
    ]
    # Worst answers first so the reader sees problems immediately.
    ordered = sorted(summary.results, key=lambda r: -(len(r.unsupported) + len(r.bad_citations)))
    articles = []
    for r in ordered:
        rows = "".join(_row(n) for n in r.numbers) or '<tr><td colspan="4">No numbers in this answer.</td></tr>'
        cite_html = ""
        if r.citations:
            cite_html = "<h3>Citations</h3><table>" + "".join(_cite_row(c) for c in r.citations) + "</table>"
        articles.append(
            f"<article><h2>{escape(r.record.id)}</h2>"
            f'<p class="q">{escape(r.record.question)}</p>'
            f'<p class="a">{_highlight(r)}</p>'
            f"<h3>Numbers</h3><table>{rows}</table>{cite_html}</article>"
        )
    stat_html = "".join(f"<div class='stat'><b>{v}</b><span>{label}</span></div>" for v, label in stats)
    return (
        "<!doctype html><html lang='en'><head><meta charset='utf-8'>"
        "<meta name='viewport' content='width=device-width,initial-scale=1'>"
        f"<title>{escape(title)}</title><style>{_CSS}</style></head><body><main>"
        f"<h1>{escape(title)}</h1>"
        "<p class='sub'>Every number in each answer, checked against the retrieved context. "
        "Green appears in a source, amber is computed from two source numbers, red appears nowhere. "
        "Each citation is checked for a real source, matching numbers and supporting text.</p>"
        f"<div class='stats'>{stat_html}</div>{''.join(articles)}</main></body></html>"
    )


__all__ = ["Verdict", "render_html"]
