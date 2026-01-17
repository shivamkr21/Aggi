from transformers import AutoTokenizer, AutoModel
import torch
import numpy as np

#MODEL_PATH = "./EmbeddingModel/SapBERT-from-PubMedBERT-fulltext"
MODEL_PATH = "./EmbeddingModel/all-mpnet-base-v2"

tokenizer = AutoTokenizer.from_pretrained(MODEL_PATH)
model = AutoModel.from_pretrained(MODEL_PATH)
model.eval()


def mean_pooling(model_output, attention_mask):
    token_embeddings = model_output.last_hidden_state
    input_mask_expanded = attention_mask.unsqueeze(-1).expand(token_embeddings.size()).float()
    return torch.sum(token_embeddings * input_mask_expanded, 1) / torch.clamp(
        input_mask_expanded.sum(1), min=1e-9
    )


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

    embedding = mean_pooling(outputs, inputs["attention_mask"])
    vec = embedding[0].numpy()
    vec = vec / np.linalg.norm(vec)
    return vec