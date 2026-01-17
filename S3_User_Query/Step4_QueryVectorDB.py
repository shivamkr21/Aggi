import chromadb
from Att2_EmbeddingModel import embed_text

chroma_client = chromadb.PersistentClient(
    path=r"C:\Aggi\AI\chroma_db"
)

collection = chroma_client.get_collection(name="medical_docs")

def QueryVector(query: str, topK: int):
    
    query_embedding = embed_text(query).tolist()
    
    results = collection.query(
        query_embeddings=[query_embedding],
        n_results = topK
    )

    print("Query:", query)
    print("\nTop results:\n")

    documents = results["documents"][0]
    distances = results["distances"][0]

    for rank, (doc, dist) in enumerate(zip(documents, distances), start=1):
        print(f"Rank {rank}")
        similarity = (1 - dist) * 100
        similarity = round(similarity, 2)
        similarity = str(similarity)
        print("Match: " + similarity + "%")
        print(doc)
        print("-" * 40)