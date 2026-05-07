from langchain_core.prompts import ChatPromptTemplate
from langchain_core.output_parsers import StrOutputParser
from langchain_core.runnables import RunnablePassthrough
from .vector_store import get_chat_model, get_vector_store

# 3️⃣ Libris Personality Prompt
system_prompt = """
You are Libris, the wise AI librarian of Bugema E-Library.
Use ONLY the provided context to answer.
If the answer is not in the context, say you don't know.

Context:
{context}
"""

prompt = ChatPromptTemplate.from_messages(
    [
        ("system", system_prompt),
        ("human", "{input}"),
    ]
)

# 4️⃣ LLM
def format_docs(docs):
    return "\n\n".join(doc.page_content for doc in docs)


def _normalize_allowed_book_ids(allowed_book_ids):
    if allowed_book_ids is None:
        return None

    normalized = []
    for raw_id in allowed_book_ids:
        normalized.append(str(int(raw_id)))
    return normalized


def _build_filter(*, book_id=None, allowed_book_ids=None):
    normalized_allowed_ids = _normalize_allowed_book_ids(allowed_book_ids)

    if book_id is not None:
        normalized_book_id = str(int(book_id))
        if normalized_allowed_ids is not None and normalized_book_id not in normalized_allowed_ids:
            raise PermissionError("Requested book is not in the allowed scope.")
        return {"book_id": normalized_book_id}

    if normalized_allowed_ids is not None:
        return {"book_id": {"$in": normalized_allowed_ids}}

    return None


def _build_retriever(*, metadata_filter=None):
    vector_store = get_vector_store()
    search_kwargs = {"k": 5}
    if metadata_filter is not None:
        search_kwargs["filter"] = metadata_filter
    return vector_store.as_retriever(search_kwargs=search_kwargs)


def _build_rag_chain(*, retriever):
    llm = get_chat_model()
    return (
        {
            "context": retriever | format_docs,
            "input": RunnablePassthrough(),
        }
        | prompt
        | llm
        | StrOutputParser()
    )


def ask_libris(user_query: str, *, book_id=None, allowed_book_ids=None):
    metadata_filter = _build_filter(book_id=book_id, allowed_book_ids=allowed_book_ids)
    retriever = _build_retriever(metadata_filter=metadata_filter)
    rag_chain = _build_rag_chain(retriever=retriever)
    return rag_chain.invoke(user_query)
