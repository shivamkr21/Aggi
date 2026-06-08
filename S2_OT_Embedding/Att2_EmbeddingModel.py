from pathlib import Path

from transformers import AutoTokenizer, AutoModel
import torch
import numpy as np

# Anchored on this file's location (not the working directory) so the model
# loads correctly whether this module is run directly or imported from
# elsewhere (e.g. S3_User_query).
MODEL_PATH = Path(__file__).resolve().parent / "EmbeddingModel" / "SapBERT-from-PubMedBERT-fulltext"
#MODEL_PATH = Path(__file__).resolve().parent / "EmbeddingModel" / "all-mpnet-base-v2"

tokenizer = AutoTokenizer.from_pretrained(MODEL_PATH)
model = AutoModel.from_pretrained(MODEL_PATH)
model.eval()


def cls_pooling(model_output):
    # SapBERT (a bare PubMedBERT encoder, not a sentence-transformers model)
    # is trained and intended to be used via its [CLS] token representation --
    # the first token of the last hidden state -- rather than mean pooling.
    return model_output.last_hidden_state[:, 0, :]


def embed_text(text: str):
    inputs = tokenizer(
        text,
        return_tensors="pt",
        padding=True,
        truncation=True,
        max_length=512
    )

    with torch.no_grad():
        outputs = model(**inputs)

    embedding = cls_pooling(outputs)
    vec = embedding[0].numpy()
    vec = vec / np.linalg.norm(vec)
    return vec