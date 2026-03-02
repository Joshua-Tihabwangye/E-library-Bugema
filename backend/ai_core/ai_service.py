import os
from langchain_postgres import PGVector
from langchain_google_genai import (
    GoogleGenerativeAIEmbeddings,
    ChatGoogleGenerativeAI,
)
from langchain_core.prompts import ChatPromptTemplate
from langchain_core.output_parsers import StrOutputParser
from langchain_core.runnables import RunnablePassthrough
from operator import itemgetter


# 1️⃣ Embeddings
embeddings = GoogleGenerativeAIEmbeddings(
    model="models/text-embedding-004"
)

raw_connection = os.getenv("NEON_DATABASE_URL") or os.getenv("DATABASE_URL")
if not raw_connection:
    raise ValueError(
        "Missing NEON_DATABASE_URL/NEON_DATABASE_URL for PGVector connection."
    )

# Force SQLAlchemy to use psycopg v3 instead of defaulting to psycopg2.
if raw_connection.startswith("postgresql://"):
    CONNECTION_STRING = raw_connection.replace(
        "postgresql://", "postgresql+psycopg://", 1
    )
elif raw_connection.startswith("postgres://"):
    CONNECTION_STRING = raw_connection.replace(
        "postgres://", "postgresql+psycopg://", 1
    )
else:
    CONNECTION_STRING = raw_connection

# 2️⃣ Connect to Neon pgvector
vector_store = PGVector(
    embeddings=embeddings,
    collection_name="library_docs",
    connection=CONNECTION_STRING,
    use_jsonb=True,
)

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
llm = ChatGoogleGenerativeAI(
    model="gemini-1.5-flash",
    temperature=0.3,
)

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
