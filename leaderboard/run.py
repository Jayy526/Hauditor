"""Run the Hauditor probe set against each RAG stack and save one result per run.

    python leaderboard/run.py --models llama3.2:3b --stacks langchain llamaindex haystack

Writes ``leaderboard/results/<stack>__<model>.json``. Build the site from
those with ``leaderboard/build_site.py``.
"""

from __future__ import annotations

import argparse
import json
import platform
import sys
import time
from datetime import datetime, timezone
from importlib.metadata import PackageNotFoundError, version
from pathlib import Path

from hauditor import __version__
from hauditor.live import PythonTarget, load_probes, run_live

sys.path.insert(0, str(Path(__file__).parent))
from stacks import STACKS

HERE = Path(__file__).parent
PACKAGES = {
    "langchain": ["langchain-core", "langchain-ollama"],
    "llamaindex": ["llama-index-core", "llama-index-llms-ollama"],
    "haystack": ["haystack-ai", "ollama-haystack"],
}


def _versions(stack: str) -> dict[str, str]:
    out = {}
    for pkg in PACKAGES.get(stack, []):
        try:
            out[pkg] = version(pkg)
        except PackageNotFoundError:
            out[pkg] = "?"
    return out


def slug(s: str) -> str:
    return s.replace(":", "-").replace("/", "-")


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--models", nargs="+", required=True, help="Ollama chat models, e.g. llama3.2:3b")
    ap.add_argument("--stacks", nargs="+", default=list(STACKS), choices=list(STACKS))
    ap.add_argument("--embed-model", default="nomic-embed-text")
    ap.add_argument("--base-url", default="http://127.0.0.1:11434")
    ap.add_argument("--corpus", default=str(HERE / "data" / "corpus.jsonl"))
    ap.add_argument("--probes", default=str(HERE / "data" / "probes.jsonl"))
    ap.add_argument("--out", default=str(HERE / "results"))
    ap.add_argument("--log-json", action="store_true", help="also print each run's full result as one JSON line")
    args = ap.parse_args(argv)

    docs = [json.loads(line) for line in Path(args.corpus).read_text(encoding="utf-8").splitlines() if line.strip()]
    probes = load_probes(args.probes)
    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)

    for model in args.models:
        for stack in args.stacks:
            name = f"{stack} + {model}"
            print(f"== {name}", flush=True)
            t0 = time.time()
            ask = STACKS[stack](docs, model=model, embed_model=args.embed_model, base_url=args.base_url)
            report = run_live(
                PythonTarget(ask, name=name), probes,
                on_progress=lambda i, n, p: print(f"\r  {i}/{n}", end="", flush=True),
            )
            d = report.to_dict()
            d["meta"] = {
                "stack": stack,
                "model": model,
                "embed_model": args.embed_model,
                "hauditor": __version__,
                "packages": _versions(stack),
                "python": platform.python_version(),
                "probes": len(probes),
                "seconds": round(time.time() - t0, 1),
                "run_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
            }
            path = out / f"{stack}__{slug(model)}.json"
            path.write_text(json.dumps(d, indent=2), encoding="utf-8")
            print(f"\r  score {d['hauditor_score']:.0f}/100 in {d['meta']['seconds']:.0f}s -> {path}", flush=True)
            for r in report.results:
                if r.outcome.value != "pass":
                    ans = " ".join((r.result.record.answer if r.result else r.error or "").split())
                    print(f"  {r.outcome.value.upper():<10} [{r.probe.id}] {r.reason}\n             {ans[:400]}")
            if args.log_json:
                # One line per run, so results can be recovered from CI logs alone.
                print("RESULT_JSON " + json.dumps(d, separators=(",", ":")), flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
