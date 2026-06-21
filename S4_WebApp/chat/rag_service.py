"""
Thin bridge between the Django chat app and the existing RAG pipeline that
lives in S3_User_Query (Step6_GenerateAnswer.py). Keeping all the retrieval /
generation logic in one place (S3) means the web app and any future CLI/batch
tools share the exact same behaviour -- this module just makes that code
importable from here and adapts our stored Message rows into the
{"role", "content"} history shape generate_answer() expects.
"""

import sys
from pathlib import Path

# S3_User_Query imports Step4_QueryVectorDB, which in turn reaches into
# S2_OT_Embedding for the embedding model -- so both need to be on sys.path
# before we import anything from Step6.
PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
for folder in ("S2_OT_Embedding", "S3_User_Query"):
    path = str(PROJECT_ROOT / folder)
    if path not in sys.path:
        sys.path.append(path)

from Step6_GenerateAnswer import generate_answer, generate_answer_stream  # noqa: E402

# How many prior turns (user+assistant pairs) to replay back to the LLM as
# conversation memory. Kept small on purpose -- every turn we resend eats
# into the context window alongside the retrieved textbook chunks, and older
# turns are rarely relevant to a fresh follow-up question anyway.
HISTORY_TURNS = 3


def build_history(messages):
    """Convert recent stored Message rows into the {"role", "content"} list
    generate_answer() expects, capped to the most recent HISTORY_TURNS
    user+assistant pairs (i.e. up to 2 * HISTORY_TURNS messages)."""
    recent = list(messages)[-(HISTORY_TURNS * 2):]
    return [{"role": m.role, "content": m.content} for m in recent]


def answer_question(query, history_messages):
    history = build_history(history_messages)
    return generate_answer(query, history=history)


def answer_question_stream(query, history_messages):
    history = build_history(history_messages)
    yield from generate_answer_stream(query, history=history)
