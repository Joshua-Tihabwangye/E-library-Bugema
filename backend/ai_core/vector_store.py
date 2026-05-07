import os

from langchain_google_genai import ChatGoogleGenerativeAI, GoogleGenerativeAIEmbeddings
from langchain_postgres import PGVector


QA_COLLECTION_NAME = "library_docs"
QA_EMBEDDING_MODEL = "models/gemini-embedding-001"
QA_CHAT_MODEL = "gemini-1.5-flash"


def _build_connection_string() -> str:
    raw_connection = os.getenv("NEON_DATABASE_URL") or os.getenv("DATABASE_URL")
    if not raw_connection:
        raise ValueError(
            "Missing NEON_DATABASE_URL/DATABASE_URL for PGVector connection."
        )

    # Force SQLAlchemy to use psycopg v3 instead of defaulting to psycopg2.
    if raw_connection.startswith("postgresql://"):
        return raw_connection.replace("postgresql://", "postgresql+psycopg://", 1)
    if raw_connection.startswith("postgres://"):
        return raw_connection.replace("postgres://", "postgresql+psycopg://", 1)
    return raw_connection


def get_embeddings() -> GoogleGenerativeAIEmbeddings:
    return GoogleGenerativeAIEmbeddings(model=QA_EMBEDDING_MODEL)


def get_vector_store() -> PGVector:
    return PGVector(
        embeddings=get_embeddings(),
        collection_name=QA_COLLECTION_NAME,
        connection=_build_connection_string(),
        use_jsonb=True,
    )


def get_chat_model() -> ChatGoogleGenerativeAI:
    return ChatGoogleGenerativeAI(
        model=QA_CHAT_MODEL,
        temperature=0.3,
    )
