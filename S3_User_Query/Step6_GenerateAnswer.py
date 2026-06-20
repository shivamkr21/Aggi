import os

from openai import OpenAI

from Att_MMR import mmr_select
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
FETCH_K = 20              # wider net -- gives MMR more to work with
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
    """Filter and diversify the FETCH_K candidate pool using MMR.

    Step 1 — similarity threshold: drop any chunk that scores below
    `threshold` against the query (noise filter, same as before).

    Step 2 — MMR: from whatever survives the threshold, pick the `cap`
    chunks that are most relevant to the query AND least redundant with
    each other. This is the key change from the old top-slice: instead of
    blindly taking the top-N similar chunks (which fills the prompt with
    near-duplicate content when multiple books cover the same topic), MMR
    actively penalises redundancy so the final set is both relevant
    and diverse.
    """
    documents = results["documents"][0]
    metadatas = results["metadatas"][0]
    distances = results["distances"][0]
    embeddings = results["embeddings"][0]

    candidates = []
    for doc, meta, dist, emb in zip(documents, metadatas, distances, embeddings):
        similarity = round((1 - dist) * 100, 2)
        if similarity >= threshold:
            candidates.append((doc, meta, similarity, emb))

    return mmr_select(results["query_embedding"], candidates, k=cap)


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


def generate_answer(query: str, fetch_k: int = FETCH_K, history: list | None = None) -> str:
    """Answer a question grounded in retrieved textbook context.

    `history` is the optional "conversation memory" -- a list of prior turns,
    each shaped like {"role": "user"|"assistant", "content": "..."}, ordered
    oldest-first. The LLM API itself is stateless, so to make follow-up
    questions like "explain that more" work, the *caller* (e.g. the Django
    chat app) is responsible for storing past turns and replaying them here;
    this function just slots them into the message list ahead of the new
    question so the model has that context. Pass None / [] for a one-off,
    single-turn question (e.g. the CLI usage in main() below).
    """
    results = QueryVector(query, fetch_k)
    chunks = select_chunks(results)

    if not chunks:
        return (
            "I couldn't find sufficiently relevant material in the source "
            "textbooks to answer this question."
        )

    user_prompt = build_prompt(query, chunks)

    messages = [{"role": "system", "content": SYSTEM_PROMPT}]
    if history:
        messages.extend(history)
    messages.append({"role": "user", "content": user_prompt})

    response = client.chat.completions.create(
        model=GEN_MODEL,
        messages=messages,
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
