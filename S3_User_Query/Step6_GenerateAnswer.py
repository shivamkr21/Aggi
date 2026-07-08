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
SIMILARITY_THRESHOLD = 57.0   # lowered from 58: the "Types of necrosis" chunk
                               # (coagulative/liquefactive/caseous list) scored
                               # 57.92 and was being cut by the old 58.0 floor.
MIN_CHUNKS = 2                 # minimum chunks needed to even consider RAG mode
MIN_TOP_SCORE = 62.5           # Path 1 — strong single match: top chunk ≥ this
                               # AND at least MIN_CHUNKS found → Medical mode.
COLLECTIVE_SCORE = 60.0        # Path 2 — collective signal: if at least
COLLECTIVE_COUNT = 3           # COLLECTIVE_COUNT chunks each individually score
                               # ≥ COLLECTIVE_SCORE, the corpus covers the topic
                               # even without a dominant single chunk → Medical.
                               # "Hi There" has only 1 chunk ≥ 60% (62.04%),
                               # so it doesn't trigger this path. A genuine
                               # medical query with 7 moderate chunks would have
                               # 3+ above 60% and correctly goes Medical.
PROMPT_CAP = 5

SYSTEM_PROMPT = (
    "You are a medical assistant. Answer the user's query using the context "
    "provided below, which contains excerpts from verified medical textbooks. "
    "Synthesise relevant information from the context to address the query — "
    "even if the context does not answer it exactly, use what is relevant to "
    "provide a useful clinical response. "
    "Do not rely on outside knowledge beyond what is in the provided context. "
    "If the context is completely unrelated to the query, say so briefly."
)

CONVERSATIONAL_SYSTEM_PROMPT = (
    "You are a friendly medical assistant chatbot named Aggi. "
    "Your answers are grounded in verified medical sources. "
    "Respond naturally to greetings and general questions. "
    "If asked what you can do, explain that you answer medical questions based "
    "on verified medical sources. Keep responses short and friendly. "
    "Do not answer specific medical questions from memory -- politely let the "
    "user know those will be answered from the verified medical sources when they ask one."
)

# Reads the key from the environment so it never lives in source/ git history --
# set OPENAI_API_KEY as a system/user environment variable before running this.
client = OpenAI(api_key=os.environ["OPENAI_API_KEY"])


REWRITE_SYSTEM_PROMPT = (
    "You are a query rewriting assistant for a medical AI. "
    "Given a conversation history and a follow-up question, rewrite the follow-up "
    "into a single standalone medical question that can be understood without the "
    "conversation history and also fix obvious spelling mistakes in medical terms. "
    "If the follow-up is unrelated to the conversation history and is a medical term "
    "or condition, treat it as a new topic and rewrite it as a complete standalone medical question. "
    "If the follow-up uses conversational phrasing like 'What do you know about X', "
    "'Tell me about X', or 'What can you tell me about X', rephrase it into a direct "
    "clinical question such as 'What is X and what are its symptoms, causes, and treatment?'. "
    "If the follow-up is a greeting, casual phrase, or non-medical statement "
    "(e.g. 'Hi', 'Hello', 'Thank you', 'Hi Buddy'), return it unchanged. "
    "If the follow-up is already self-contained, return it "
    "unchanged. Output ONLY the rewritten question — no explanation, no punctuation "
    "changes, nothing else."
)


def rewrite_query(query: str, history: list) -> str:
    """Rewrite and spell-correct a query for ChromaDB retrieval.

    Handles three cases:
    - Spelling correction: 'colilithiasis' → 'cholelithiasis'
    - Context-aware follow-ups: 'symptoms' → 'What are the symptoms of cholelithiasis?'
    - New unrelated topic: 'Paragangliomas' → 'What are paragangliomas and how are they treated?'

    Runs on every query including first messages (no early return for empty history)
    so spelling correction always applies.
    """
    messages = [
        {"role": "system", "content": REWRITE_SYSTEM_PROMPT},
        *history[-4:],   # last 2 exchanges for context
        {"role": "user", "content": f"Follow-up: {query}"},
    ]

    response = client.chat.completions.create(
        model=GEN_MODEL,
        messages=messages,
        max_tokens=80,
        temperature=0,
    )
    return response.choices[0].message.content.strip()


def is_medical_mode(chunks) -> bool:
    """Decide whether to use RAG (Medical Reference) or Conversational mode.

    Path 1 — strong single match:
        Top chunk ≥ 62.5% AND at least 2 chunks found.
        The 62.5% threshold sits in the 0.8% gap between the highest observed
        non-medical top score (62.04% — "Hi There") and the lowest genuine
        medical top score (62.86% — MI symptoms).

    Path 2 — collective signal:
        At least 3 chunks each individually scoring ≥ 60%.
        Handles queries where the corpus clearly covers the topic but no single
        chunk dominates (e.g. 7 chunks at 58-61%). Using a COUNT of chunks
        above 60% (not just the top score) prevents "Hi There" from triggering
        this path -- it only has 1 chunk above 60% (62.04%), while a genuine
        medical query with collective coverage would have 3+.
    """
    if not chunks:
        return False
    top_score = max(sim for _, _, sim in chunks)
    if top_score >= MIN_TOP_SCORE and len(chunks) >= MIN_CHUNKS:
        return True
    chunks_above_collective = sum(1 for _, _, sim in chunks if sim >= COLLECTIVE_SCORE)
    if chunks_above_collective >= COLLECTIVE_COUNT:
        return True
    return False


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

    if not is_medical_mode(chunks):
        messages = [{"role": "system", "content": CONVERSATIONAL_SYSTEM_PROMPT}]
        if history:
            messages.extend(history)
        messages.append({"role": "user", "content": query})
        response = client.chat.completions.create(model=GEN_MODEL, messages=messages)
        return response.choices[0].message.content

    user_prompt = build_prompt(query, chunks)
    messages = [{"role": "system", "content": SYSTEM_PROMPT}]
    if history:
        messages.extend(history)
    messages.append({"role": "user", "content": user_prompt})
    response = client.chat.completions.create(model=GEN_MODEL, messages=messages)
    return response.choices[0].message.content


def generate_answer_stream(query: str, retrieval_query: str | None = None,
                           fetch_k: int = FETCH_K, history: list | None = None):
    """Streaming version of generate_answer.

    `query`          — the original user text; used for the LLM prompt so the
                       answer reads naturally relative to what the user typed.
    `retrieval_query`— the rewritten standalone question used for ChromaDB
                       retrieval.  If None, falls back to `query`.  Passing a
                       rewritten version fixes context-blind follow-ups like
                       'symptoms' → 'What are the symptoms of cholelithiasis?'

    Yields dicts: {"type": "citations"|"token"|"done"|"error", "content": ...}
    """
    try:
        rq = retrieval_query or query
        results = QueryVector(rq, fetch_k)
        chunks = select_chunks(results)

        if not is_medical_mode(chunks):
            messages = [{"role": "system", "content": CONVERSATIONAL_SYSTEM_PROMPT}]
            if history:
                messages.extend(history)
            messages.append({"role": "user", "content": query})
        else:
            # Send citation trails before the first token
            citations = [
                f"[{meta['book_id']}] {meta['chapter_title']} > "
                f"{meta['topic_title']} > {meta['subtopic_title']} (page {meta['page']})"
                for _, meta, _ in chunks
            ]
            yield {"type": "citations", "content": citations}

            user_prompt = build_prompt(rq, chunks)
            messages = [{"role": "system", "content": SYSTEM_PROMPT}]
            if history:
                messages.extend(history)
            messages.append({"role": "user", "content": user_prompt})

        stream = client.chat.completions.create(model=GEN_MODEL, messages=messages, stream=True)
        for chunk in stream:
            delta = chunk.choices[0].delta.content
            if delta:
                yield {"type": "token", "content": delta}

        yield {"type": "done", "content": ""}

    except Exception as e:
        yield {"type": "error", "content": "Something went wrong. Please try again."}


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
