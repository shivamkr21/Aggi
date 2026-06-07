import json

import chromadb

from Att2_EmbeddingModel import embed_text

CHUNKS_PATH = "../S1_OT_Chunking/Sample_Test_Hierarchical_Chunks.json"

chroma_client = chromadb.PersistentClient(
    path=r"C:\Aggi\AI\chroma_db"
)

collection = chroma_client.get_collection(name="medical_docs")

with open(CHUNKS_PATH, "r", encoding="utf-8") as f:
    data = json.load(f)

# Lookup tables to walk a paragraph's parent chain back to its chapter, so each
# embedded chunk can carry the full hierarchy trail (ids + readable titles) as
# metadata -- this enables filtered retrieval (e.g. by chapter_id) and lets
# answers cite "Chapter X -> Topic Y -> Subtopic Z, page N" instead of raw text.
chapter_titles = {c["chapter_id"]: c["title"] for c in data["chapters"]}
topic_index = {t["topic_id"]: (t["title"], t["chapter_id"]) for t in data["topics"]}
subtopic_index = {s["subtopic_id"]: (s["title"], s["topic_id"]) for s in data["subtopics"]}
book_id = data["chapters"][0]["book_id"] if data["chapters"] else ""


def build_metadata(paragraph):
    subtopic_title, topic_id = subtopic_index[paragraph["subtopic_id"]]
    topic_title, chapter_id = topic_index[topic_id]

    return {
        "book_id": book_id,
        "chapter_id": chapter_id,
        "chapter_title": chapter_titles[chapter_id],
        "topic_id": topic_id,
        "topic_title": topic_title,
        "subtopic_id": paragraph["subtopic_id"],
        "subtopic_title": subtopic_title,
        "page": paragraph["page"],
    }


documents = []
ids = []
embeddings = []
metadatas = []

for paragraph in data["paragraphs"]:
    text = paragraph["text"]
    vec = embed_text(text)

    documents.append(text)
    ids.append(paragraph["paragraph_id"])
    embeddings.append(vec.tolist())
    metadatas.append(build_metadata(paragraph))

# upsert (not add) so re-running after re-extracting the PDF overwrites
# existing entries instead of erroring on duplicate ids.
collection.upsert(
    documents=documents,
    embeddings=embeddings,
    ids=ids,
    metadatas=metadatas,
)

print("Chroma indexing complete.")
print("Number of documents in collection:", collection.count())
