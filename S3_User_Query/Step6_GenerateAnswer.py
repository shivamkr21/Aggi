import os

from openai import OpenAI

from Step4_QueryVectorDB import QueryVector

GEN_MODEL = "gpt-4o-mini"

# Retrieval is split into three knobs (tuned from the score-distribution data
# gathered in Step5_Top_K_Embedding.py against this corpus + SapBERT model):
#
#   1. FETCH_K            -- cast a wide net from Chroma so the right chunk
#                            still surfaces even when it isn't ranked #1
#                            (we saw a correct match land as low as #6).
#   2. SIMILARITY_THRESHOLD -- drop candidates below this score. SapBERT scores
#                            are "compressed" (medical-adjacent noise can still
#                            score in the high 50s), so this won't perfectly
#                            separate signal from noise -- but it reliably
#                            trims the bottom of the noise floor (pure-noise
#                            queries topped out around 56-59%) while keeping
#                            every genuine match we observed (lowest genuine
#                            top-hit was ~58.4%).
#   3. PROMPT_CAP         -- of whatever clears the threshold, send at most
#                            this many to the LLM, to bound token cost/context
#                            dilution and let rank do the final quality call.
FETCH_K = 10
SIMILARITY_THRESHOLD = 58.0
PROMPT_CAP = 5

SYSTEM_PROMPT = (
    "You are a medical assistant answering questions from a textbook excerpt. "
    "Answer the question using ONLY the context provided below -- do not rely on "
    "outside knowledge. If the context does not contain the answer, say so plainly "
    "instead of guessing."
)

# Reads the key from the environment so it never lives in source/ git history --
# set OPENAI_API_KEY as a system/user environment variable before running this.
client = OpenAI(api_key=os.environ["OPENAI_API_KEY"])


def select_chunks(results, threshold: float = SIMILARITY_THRESHOLD, cap: int = PROMPT_CAP):
    """Filter the wide FETCH_K candidate pool down to the chunks actually
    worth handing to the LLM: drop anything below the similarity threshold,
    then keep only the top `cap` of whatever remains (results already arrive
    ranked best-first from Chroma, so this is just a slice)."""
    documents = results["documents"][0]
    metadatas = results["metadatas"][0]
    distances = results["distances"][0]

    selected = []
    for doc, meta, dist in zip(documents, metadatas, distances):
        similarity = round((1 - dist) * 100, 2)
        if similarity >= threshold:
            selected.append((doc, meta, similarity))

    return selected[:cap]


def build_prompt(query, chunks):
    context_blocks = []
    for doc, meta, _similarity in chunks:
        trail = (
            f"{meta['chapter_title']} > {meta['topic_title']} > "
            f"{meta['subtopic_title']} (page {meta['page']})"
        )
        context_blocks.append(f"[{trail}]\n{doc}")

    context = "\n\n".join(context_blocks)
    return f"Context:\n{context}\n\nQuestion: {query}"


def generate_answer(query: str, fetch_k: int = FETCH_K) -> str:
    results = QueryVector(query, fetch_k)
    chunks = select_chunks(results)

    if not chunks:
        return (
            "I couldn't find sufficiently relevant material in the source "
            "textbooks to answer this question."
        )

    user_prompt = build_prompt(query, chunks)

    response = client.chat.completions.create(
        model=GEN_MODEL,
        messages=[
            {"role": "system", "content": SYSTEM_PROMPT},
            {"role": "user", "content": user_prompt},
        ],
    )

    return response.choices[0].message.content


def main():
    query = input("Enter your question: ").strip()
    if not query:
        print("No question entered.")
        return

    answer = generate_answer(query)

    print("\n=== ANSWER ===\n")
    print(answer)
    query = input("")
    


if __name__ == "__main__":
    main()
