import os

from openai import OpenAI

from Step4_QueryVectorDB import QueryVector

GEN_MODEL = "gpt-4o-mini"
TOP_K = 10

SYSTEM_PROMPT = (
    "You are a medical assistant answering questions from a textbook excerpt. "
    "Answer the question using ONLY the context provided below -- do not rely on "
    "outside knowledge. If the context does not contain the answer, say so plainly "
    "instead of guessing."
)

# Reads the key from the environment so it never lives in source/ git history --
# set OPENAI_API_KEY as a system/user environment variable before running this.
client = OpenAI(api_key=os.environ["OPENAI_API_KEY"])


def build_prompt(query, results):
    documents = results["documents"][0]
    metadatas = results["metadatas"][0]

    context_blocks = []
    for doc, meta in zip(documents, metadatas):
        trail = (
            f"{meta['chapter_title']} > {meta['topic_title']} > "
            f"{meta['subtopic_title']} (page {meta['page']})"
        )
        context_blocks.append(f"[{trail}]\n{doc}")

    context = "\n\n".join(context_blocks)
    return f"Context:\n{context}\n\nQuestion: {query}"


def generate_answer(query: str, top_k: int = TOP_K) -> str:
    results = QueryVector(query, top_k)
    user_prompt = build_prompt(query, results)

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
