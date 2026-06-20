"""
Maximal Marginal Relevance (MMR) selection.

Instead of naively taking the top-K most similar chunks (which causes
redundancy when multiple books cover the same topic), MMR iteratively
picks chunks that are both relevant to the query AND different from
what has already been selected.

Each round scores every remaining candidate as:

    score = λ × relevance  −  (1 − λ) × redundancy

where:
    relevance  = cosine similarity between the chunk and the query
    redundancy = cosine similarity between the chunk and the
                 most similar chunk already selected
    λ (lambda) = balance knob:  1.0 → pure relevance (= normal top-K)
                                0.0 → pure diversity
                                0.5 → equal balance (default)

The chunk with the highest score is selected each round, and the
process repeats until `k` chunks have been chosen.
"""

import numpy as np


def _cosine_sim(a, b):
    a, b = np.array(a, dtype=float), np.array(b, dtype=float)
    denom = np.linalg.norm(a) * np.linalg.norm(b)
    if denom == 0:
        return 0.0
    return float(np.dot(a, b) / denom)


def mmr_select(query_embedding, candidates, k, lam=0.5):
    """
    Select up to `k` diverse, relevant chunks from `candidates` using MMR.

    Parameters
    ----------
    query_embedding : list[float]
        The embedding vector of the user's query.
    candidates : list of (doc, meta, similarity, chunk_embedding)
        The threshold-filtered pool to select from.
        - doc            : str   — the paragraph text
        - meta           : dict  — citation metadata
        - similarity     : float — pre-computed query similarity (%)
        - chunk_embedding: list  — the chunk's vector from ChromaDB
    k : int
        How many chunks to return (your PROMPT_CAP).
    lam : float
        λ — relevance/diversity trade-off (0.5 by default).

    Returns
    -------
    list of (doc, meta, similarity)   — ready to pass to build_prompt()
    """
    if not candidates:
        return []

    remaining = list(range(len(candidates)))
    selected_indices = []
    selected_embeddings = []

    for _ in range(min(k, len(candidates))):
        best_score = -float("inf")
        best_idx = None

        for i in remaining:
            _, _, sim, chunk_emb = candidates[i]
            relevance = sim / 100.0  # convert % back to 0-1 range

            if selected_embeddings:
                redundancy = max(
                    _cosine_sim(chunk_emb, sel_emb)
                    for sel_emb in selected_embeddings
                )
            else:
                redundancy = 0.0

            score = lam * relevance - (1 - lam) * redundancy

            if score > best_score:
                best_score = score
                best_idx = i

        _, _, _, chosen_emb = candidates[best_idx]
        selected_indices.append(best_idx)
        selected_embeddings.append(chosen_emb)
        remaining.remove(best_idx)

    return [
        (candidates[i][0], candidates[i][1], candidates[i][2])
        for i in selected_indices
    ]
