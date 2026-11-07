# RAG leaderboard

This folder holds the code that runs Hauditor's live mode against popular RAG frameworks and builds the leaderboard page from the results.

## What's tested

| Stack | Built like | Passages retrieved | Prompt |
|---|---|---|---|
| LangChain | the RAG tutorial | 4 (default `k`) | `rlm/rag-prompt` |
| LlamaIndex | `VectorStoreIndex.from_documents(...).as_query_engine()` | 2 (default) | built-in QA prompt |
| Haystack | the "first RAG pipeline" tutorial | 10 (default `top_k`) | the tutorial's prompt |

Every stack gets the same parts:

- **Documents:** [`data/corpus.jsonl`](data/corpus.jsonl), 24 paragraphs from the annual report of a made-up company. Because the company doesn't exist, a model can't answer from memory. Any figure that isn't in the passages it was given was invented.
- **Chat model:** an open model served by Ollama at temperature 0.
- **Embedding model:** `nomic-embed-text`.
- **Questions:** the 35 in [`data/probes.jsonl`](data/probes.jsonl), 5 in each of lookup, compute, citation, unanswerable and false premise, plus 10 pressure variants.

The stacks are not tuned for this test. Their defaults are what people actually ship, so those are what gets measured. See [`stacks.py`](stacks.py).

## Run it

```bash
pip install -e . -r leaderboard/requirements.txt
ollama pull llama3.2:3b && ollama pull nomic-embed-text
python leaderboard/run.py --models llama3.2:3b
python leaderboard/build_site.py --out site   # site/index.html and site/leaderboard.md
```

To check the setup without downloading a model, run `python leaderboard/fake_ollama.py` and pass `--base-url http://127.0.0.1:11435`. Scores from the fake model mean nothing; it only shows that the pipeline runs.

The [`leaderboard` workflow](../.github/workflows/leaderboard.yml) runs every stack with every model on GitHub's runners and publishes the page to GitHub Pages. It runs monthly, on demand, and on pull requests that change this folder.

## Adding a stack or model

Add a `build_<name>` function to `stacks.py` that returns `ask(question) -> {"answer", "contexts"}`. `contexts` must hold exactly the passages the model saw. To add a model, add it to the workflow's `model` matrix. Pull requests are welcome.
