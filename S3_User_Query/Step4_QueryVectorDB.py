import sys
from pathlib import Path

# Att2_EmbeddingModel.py lives in S2_OT_Embedding; make it importable here so
# both indexing (Step2) and querying (Step4) embed text with the exact same
# model and pooling logic, keeping query and document vectors in one space.
sys.path.append(str(Path(__file__).resolve().parent.parent / "S2_OT_Embedding"))

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
        n_results=topK,
        # chunk embeddings are needed by MMR to measure chunk-to-chunk similarity
        # (redundancy penalty); query_embedding is attached so callers don't have
        # to re-embed the same string a second time.
        include=["embeddings", "documents", "metadatas", "distances"],
    )
    results["query_embedding"] = query_embedding

#    print("Query:", query)
#    print("\nTop results:\n")

#    documents = results["documents"][0]
#    distances = results["distances"][0]
#
#    for rank, (doc, dist) in enumerate(zip(documents, distances), start=1):
#        print(f"Rank {rank}")
#        similarity = (1 - dist) * 100
#        similarity = round(similarity, 2)
#        similarity = str(similarity)
#        print("Match: " + similarity + "%")
#        print(doc)
#        print("-" * 40)

    return results