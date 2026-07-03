"""Re-push existing embeddings from FAISS pkl to Neon — skips re-embedding."""
import os, sys, pickle, json
import numpy as np
from pathlib import Path
from dotenv import load_dotenv

load_dotenv(dotenv_path=str(Path(__file__).parent / ".env"))

NEON_URL  = os.getenv("NEON_DATABASE_URL", "")
INDEX_DIR = Path(__file__).parent / "backend" / "data" / "faiss_index"

def main():
    import psycopg2
    from pgvector.psycopg2 import register_vector
    import faiss

    print("Loading cached embeddings...")
    with open(INDEX_DIR / "chunks_meta.pkl", "rb") as f:
        chunks = pickle.load(f)

    idx = faiss.read_index(str(INDEX_DIR / "legal_index.faiss"))
    # Reconstruct all vectors from index
    n = idx.ntotal
    dim = idx.d
    embs = np.zeros((n, dim), dtype="float32")
    for i in range(n):
        embs[i] = idx.reconstruct(i)

    print(f"Loaded {n} vectors (dim={dim}). Connecting to Neon...")

    conn = psycopg2.connect(NEON_URL)
    register_vector(conn)
    cur = conn.cursor()

    cur.execute(f"""
        CREATE TABLE IF NOT EXISTS legal_chunks (
            id          TEXT PRIMARY KEY,
            section_ref TEXT NOT NULL,
            title       TEXT NOT NULL,
            content     TEXT NOT NULL,
            act         TEXT,
            embedding   vector({dim})
        );
    """)
    conn.commit()

    for chunk, emb in zip(chunks, embs):
        cur.execute("""
            INSERT INTO legal_chunks (id, section_ref, title, content, act, embedding)
            VALUES (%s, %s, %s, %s, %s, %s)
            ON CONFLICT (id) DO UPDATE
                SET title=EXCLUDED.title,
                    content=EXCLUDED.content,
                    embedding=EXCLUDED.embedding;
        """, (chunk["id"], chunk["section_ref"], chunk["title"],
              chunk["text"], chunk["act"], emb.tolist()))

    conn.commit()
    cur.close()
    conn.close()
    print(f"Done. Upserted {len(chunks)} chunks to Neon legal_chunks.")

if __name__ == "__main__":
    main()
