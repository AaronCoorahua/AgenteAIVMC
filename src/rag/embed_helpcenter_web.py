"""
Sube los chunks del Centro de Ayuda web (data/processed/helpcenter_web_chunks.json)
a Pinecone, en el mismo namespace 'helpcenter' que usa el resto del RAG.

Genera antes el archivo con:
    python -m src.ingest.parse_helpcenter_web

Uso: python -m src.rag.embed_helpcenter_web
"""
import os
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
os.chdir(ROOT)

from dotenv import load_dotenv
load_dotenv(ROOT / ".env")

CHUNKS_PATH = ROOT / "data" / "processed" / "helpcenter_web_chunks.json"
NAMESPACE = "helpcenter"


def main():
    api_key = os.getenv("PINECONE_API_KEY")
    index_name = os.getenv("PINECONE_INDEX_NAME", "vmc-bot-rag")
    if not api_key:
        print("Falta PINECONE_API_KEY en .env")
        sys.exit(1)
    if not CHUNKS_PATH.exists():
        print(f"No existe {CHUNKS_PATH}. Ejecuta antes: python -m src.ingest.parse_helpcenter_web")
        sys.exit(1)

    with open(CHUNKS_PATH, encoding="utf-8") as f:
        data = json.load(f)
    chunks = data.get("chunks", [])
    if not chunks:
        print("No hay chunks para subir.")
        sys.exit(1)

    from pinecone import Pinecone
    pc = Pinecone(api_key=api_key)
    index = pc.Index(index_name)

    records = []
    for c in chunks:
        rid = c.get("id", "")
        text = c.get("text", "").strip()
        if not rid or not text:
            continue
        records.append({
            "id": rid,
            "text": text[:30_000],
            "topic": c.get("topic", ""),
            "source_url": c.get("source_url", ""),
            "has_numeric_data": c.get("has_numeric_data", False),
        })

    batch_size = 96  # limite de Pinecone Inference para este indice
    for i in range(0, len(records), batch_size):
        batch = records[i : i + batch_size]
        index.upsert_records(namespace=NAMESPACE, records=batch)
        print(f"Subidos {min(i + batch_size, len(records))}/{len(records)}")

    print(f"Listo. {len(records)} chunks del Centro de Ayuda web en '{index_name}', namespace '{NAMESPACE}'.")


if __name__ == "__main__":
    main()
