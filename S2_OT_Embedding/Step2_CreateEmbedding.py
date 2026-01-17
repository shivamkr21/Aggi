import chromadb
from Att2_EmbeddingModel import embed_text
from Att1_Documents import documents

chroma_client = chromadb.PersistentClient(
    path=r"C:\Aggi\AI\chroma_db"
)

collection = chroma_client.get_collection(name="medical_docs")

embeddings = []
ids = []

for i, doc in enumerate(documents):
    vec = embed_text(doc)
    embeddings.append(vec.tolist())
    ids.append(str(i))

collection.add(
    documents=documents,
    embeddings=embeddings,
    ids=ids
)

print("Chroma indexing complete.")
print("Number of documents in collection:", collection.count())
