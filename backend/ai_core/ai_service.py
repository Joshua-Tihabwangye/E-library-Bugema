from langchain_core.prompts import ChatPromptTemplate
from langchain_core.output_parsers import StrOutputParser
from langchain_core.runnables import RunnablePassthrough
from .vector_store import get_chat_model, get_vector_store

# 1️⃣ Connect to Neon pgvector
vector_store = get_vector_store()
retriever = vector_store.as_retriever(search_kwargs={"k": 5})

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
llm = get_chat_model()

# 5️⃣ Build RAG Pipeline (LCEL style)

def format_docs(docs):
    return "\n\n".join(doc.page_content for doc in docs)


rag_chain = (
    {
        "context": retriever | format_docs,
        "input": RunnablePassthrough(),
    }
    | prompt
    | llm
    | StrOutputParser()
)


def ask_libris(user_query: str):
    return rag_chain.invoke(user_query)
