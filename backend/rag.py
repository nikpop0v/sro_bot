from __future__ import annotations
import os
from typing import List, Tuple
import numpy as np
import faiss
from sentence_transformers import SentenceTransformer


def load_chunks(md_path: str, chunk_size: int = 800, overlap: int = 200) -> List[str]:
    with open(md_path, "r", encoding="utf-8") as f:
        text = f.read()
    chunks = []
    start = 0
    while start < len(text):
        end = min(start + chunk_size, len(text))
        chunks.append(text[start:end])
        start += max(1, chunk_size - overlap)
    return chunks


def get_model(name: str) -> SentenceTransformer:
    # e5-base хорошо работает для рус/eng; можно заменить через .env
    model = SentenceTransformer(name)
    return model


def embed_texts(model: SentenceTransformer, texts: List[str]) -> np.ndarray:
    # Нормализованные эмбеддинги для косинусного сходства
    emb = model.encode(texts, batch_size=32, convert_to_numpy=True, show_progress_bar=False, normalize_embeddings=True)
    return emb.astype("float32")


def build_index(embeddings: np.ndarray) -> faiss.Index:
    # Для нормализованных векторов используем inner-product (эквивалент косинуса)
    dim = embeddings.shape[1]
    index = faiss.IndexFlatIP(dim)
    index.add(embeddings)
    return index


def search(index: faiss.Index, query_vec: np.ndarray, top_k: int) -> Tuple[np.ndarray, np.ndarray]:
    D, I = index.search(query_vec, top_k)
    return D, I
