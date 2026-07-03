import psycopg2, os, sys
from dotenv import load_dotenv
from pathlib import Path

load_dotenv(dotenv_path=str(Path(__file__).parent / ".env"))
url = os.getenv("NEON_DATABASE_URL", "")

if not url:
    print("❌ Error: NEON_DATABASE_URL is not set in .env file.")
    sys.exit(1)

try:
    conn = psycopg2.connect(url)
except Exception as e:
    print(f"❌ Error connecting to database: {e}")
    sys.exit(1)
cur = conn.cursor()

# Enable pgvector
cur.execute("CREATE EXTENSION IF NOT EXISTS vector;")
conn.commit()

cur.execute("SELECT extname FROM pg_extension WHERE extname = 'vector';")
row = cur.fetchone()
print("pgvector enabled:", row)

cur.close()
conn.close()
print("Done")
