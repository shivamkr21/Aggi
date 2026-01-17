import chromadb

chroma_client = chromadb.PersistentClient(
    path=r"C:\Aggi\AI\chroma_db"
)

collection = chroma_client.get_or_create_collection(
    name="medical_docs",
    metadata={"hnsw:space": "cosine"}
)

print("Number of documents:", collection.count())
