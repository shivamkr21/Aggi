import json

import chromadb

from Att2_EmbeddingModel import embed_text

CHUNKS_DIR  = "../S1_OT_Chunking/Hierarchy Chunks/7 Parson"
TOTAL_PARTS = 10

chroma_client = chromadb.PersistentClient(
    path=r"C:\Aggi\AI\chroma_db"
)

collection = chroma_client.get_collection(name="medical_docs")


def embed_file(chunks_path: str):
    """Load one hierarchical-chunks JSON, embed every paragraph, and upsert
    into the ChromaDB collection.  Returns the number of paragraphs indexed."""
    with open(chunks_path, "r", encoding="utf-8") as f:
        data = json.load(f)

    if not data["chapters"]:
        print(f"  [SKIP] No chapters found in {chunks_path}")
        return 0

    # Lookup tables to walk a paragraph's parent chain back to its chapter,
    # so each embedded chunk carries the full hierarchy trail as metadata.
    chapter_titles = {c["chapter_id"]: c["title"] for c in data["chapters"]}
    topic_index    = {t["topic_id"]: (t["title"], t["chapter_id"]) for t in data["topics"]}
    subtopic_index = {s["subtopic_id"]: (s["title"], s["topic_id"]) for s in data["subtopics"]}
    book_id        = data["chapters"][0]["book_id"]

    def build_metadata(paragraph):
        subtopic_title, topic_id  = subtopic_index[paragraph["subtopic_id"]]
        topic_title,    chapter_id = topic_index[topic_id]
        return {
            "book_id":        book_id,
            "chapter_id":     chapter_id,
            "chapter_title":  chapter_titles[chapter_id],
            "topic_id":       topic_id,
            "topic_title":    topic_title,
            "subtopic_id":    paragraph["subtopic_id"],
            "subtopic_title": subtopic_title,
            "page":           paragraph["page"],
        }

    documents  = []
    ids        = []
    embeddings = []
    metadatas  = []

    for paragraph in data["paragraphs"]:
        text = paragraph["text"]
        vec  = embed_text(text)

        documents.append(text)
        ids.append(paragraph["paragraph_id"])
        embeddings.append(vec.tolist())
        metadatas.append(build_metadata(paragraph))

    # upsert so re-running after re-chunking overwrites existing entries
    # instead of erroring on duplicate ids.
    collection.upsert(
        documents=documents,
        embeddings=embeddings,
        ids=ids,
        metadatas=metadatas,
    )

    return len(documents)


def main():
    total_indexed = 0

    for part in range(1, TOTAL_PARTS + 1):
        path = f"{CHUNKS_DIR}/P{part}_Hierarchical_Chunks.json"
        print(f"\n[{part}/{TOTAL_PARTS}] Embedding {path} ...")
        count = embed_file(path)
        total_indexed += count
        print(f"  Paragraphs embedded : {count}")
        print(f"  Collection total    : {collection.count()}")

    print(f"\nAll done. Total paragraphs embedded this run: {total_indexed}")
    print(f"Collection size: {collection.count()}")


if __name__ == "__main__":
    main()
