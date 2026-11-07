"""The RAG stacks on the leaderboard, each built the way its own tutorial builds it.

Every stack gets the same documents, the same chat model and the same
embedding model, all served by Ollama. What differs is what each framework
does by default: how many passages it retrieves, and the prompt it wraps
around them. Those defaults are what most people ship, so they are what we
measure. Nothing here is tuned to do well on Hauditor.

Each ``build_*`` function returns ``ask(question) -> {"answer", "contexts"}``,
where ``contexts`` is the list of passages the stack actually retrieved and
showed the model. Hauditor grades the answer against exactly those.
"""

from __future__ import annotations

from collections.abc import Callable

Ask = Callable[[str], dict]

# The prompt from LangChain's RAG tutorial (the "rlm/rag-prompt" hub prompt).
LANGCHAIN_RAG_PROMPT = (
    "You are an assistant for question-answering tasks. Use the following pieces of retrieved context "
    "to answer the question. If you don't know the answer, just say that you don't know. Use three "
    "sentences maximum and keep the answer concise.\n"
    "Question: {question} \n"
    "Context: {context} \n"
    "Answer:"
)

# The prompt from Haystack's "first RAG pipeline" tutorial.
HAYSTACK_RAG_PROMPT = """
Given the following information, answer the question.

Context:
{% for document in documents %}
    {{ document.content }}
{% endfor %}

Question: {{question}}
Answer:
"""


def build_langchain(docs: list[dict], model: str, embed_model: str, base_url: str) -> Ask:
    """LangChain's RAG tutorial: in-memory vector store, k=4, rlm/rag-prompt."""
    from langchain_core.documents import Document
    from langchain_core.prompts import ChatPromptTemplate
    from langchain_core.vectorstores import InMemoryVectorStore
    from langchain_ollama import ChatOllama, OllamaEmbeddings

    store = InMemoryVectorStore(OllamaEmbeddings(model=embed_model, base_url=base_url))
    store.add_documents([Document(page_content=d["text"], metadata={"id": d["id"]}) for d in docs])
    prompt = ChatPromptTemplate.from_messages([("human", LANGCHAIN_RAG_PROMPT)])
    llm = ChatOllama(model=model, base_url=base_url, temperature=0)

    def ask(question: str) -> dict:
        hits = store.similarity_search(question)  # k=4, the default
        context = "\n\n".join(h.page_content for h in hits)
        reply = llm.invoke(prompt.invoke({"question": question, "context": context}))
        return {
            "answer": reply.content,
            "contexts": [{"id": h.metadata["id"], "text": h.page_content} for h in hits],
        }

    return ask


def build_llamaindex(docs: list[dict], model: str, embed_model: str, base_url: str) -> Ask:
    """LlamaIndex's starter: VectorStoreIndex + as_query_engine() with its default prompt and top-2."""
    from llama_index.core import Document, VectorStoreIndex
    from llama_index.embeddings.ollama import OllamaEmbedding
    from llama_index.llms.ollama import Ollama

    llm = Ollama(model=model, base_url=base_url, temperature=0, request_timeout=600)
    embed = OllamaEmbedding(model_name=embed_model, base_url=base_url)
    index = VectorStoreIndex.from_documents(
        [Document(text=d["text"], id_=d["id"]) for d in docs], embed_model=embed,
    )
    engine = index.as_query_engine(llm=llm)

    def ask(question: str) -> dict:
        resp = engine.query(question)
        return {
            "answer": str(resp),
            "contexts": [{"id": n.node.ref_doc_id or n.node.node_id, "text": n.node.get_content()}
                         for n in resp.source_nodes],
        }

    return ask


def build_haystack(docs: list[dict], model: str, embed_model: str, base_url: str) -> Ask:
    """Haystack's first-RAG-pipeline tutorial: embedding retriever (top 10) + its prompt."""
    from haystack import Document, Pipeline
    from haystack.components.builders import ChatPromptBuilder
    from haystack.components.retrievers.in_memory import InMemoryEmbeddingRetriever
    from haystack.dataclasses import ChatMessage
    from haystack.document_stores.in_memory import InMemoryDocumentStore
    from haystack_integrations.components.embedders.ollama import OllamaDocumentEmbedder, OllamaTextEmbedder
    from haystack_integrations.components.generators.ollama import OllamaChatGenerator

    store = InMemoryDocumentStore()
    embedded = OllamaDocumentEmbedder(model=embed_model, url=base_url).run(
        [Document(id=d["id"], content=d["text"]) for d in docs]
    )["documents"]
    store.write_documents(embedded)

    pipe = Pipeline()
    pipe.add_component("embedder", OllamaTextEmbedder(model=embed_model, url=base_url))
    pipe.add_component("retriever", InMemoryEmbeddingRetriever(store))
    pipe.add_component("prompt", ChatPromptBuilder(
        template=[ChatMessage.from_user(HAYSTACK_RAG_PROMPT)], required_variables=["question", "documents"],
    ))
    pipe.add_component("llm", OllamaChatGenerator(
        model=model, url=base_url, generation_kwargs={"temperature": 0}, timeout=600,
    ))
    pipe.connect("embedder.embedding", "retriever.query_embedding")
    pipe.connect("retriever", "prompt.documents")
    pipe.connect("prompt.prompt", "llm.messages")

    def ask(question: str) -> dict:
        out = pipe.run(
            {"embedder": {"text": question}, "prompt": {"question": question}},
            include_outputs_from={"retriever"},
        )
        hits = out["retriever"]["documents"]
        return {
            "answer": out["llm"]["replies"][0].text,
            "contexts": [{"id": h.id, "text": h.content} for h in hits],
        }

    return ask


STACKS: dict[str, Callable[..., Ask]] = {
    "langchain": build_langchain,
    "llamaindex": build_llamaindex,
    "haystack": build_haystack,
}
