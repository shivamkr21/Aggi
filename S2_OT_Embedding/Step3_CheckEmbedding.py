import chromadb

chroma_client = chromadb.PersistentClient(
    path=r"C:\Aggi\AI\chroma_db"
)

collection = chroma_client.get_collection(name="medical_docs")

print("Number of documents in collection:", collection.count())
