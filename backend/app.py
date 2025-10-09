from __future__ import annotations
import os
import asyncio
from typing import List, Optional

from fastapi import FastAPI, HTTPException
from pydantic import BaseModel
from dotenv import load_dotenv
import numpy as np

from .rag import load_chunks, get_model, embed_texts, build_index, search
from .storage import init_db, insert_log, set_rating_by_id

try:
    from gigachat import GigaChat  # опционально
except Exception:
    GigaChat = None

app = FastAPI(title="Pangea RAG API", version="1.0.0")

# === Глобальные объекты (пересоздаются при /reload) ===
MODEL = None
INDEX = None
CHUNKS: List[str] = []
EMB = None


class AskRequest(BaseModel):
    question: str
    chat_id: Optional[str] = None
    top_k: Optional[int] = None

class AskResponse(BaseModel):
    answer: str
    context: List[str]
    log_id: int

class FeedbackRequest(BaseModel):
    log_id: int
    rating: int  # -1/0/1


async def _ensure_ready():
    if any(x is None for x in [MODEL, INDEX, CHUNKS, EMB]):
        raise HTTPException(503, detail="Model or index not initialized. Call /reload first.")


def _compose_prompt(question: str, context: List[str]) -> str:
    ctx = "\n---\n".join(context)
    return (
        "Ответь по-русски, опираясь ТОЛЬКО на контекст ниже. Если ответа нет в контексте — скажи об этом.\n\n"
        f"Вопрос: {question}\n\nКонтекст:\n{ctx}"
    )


def _answer_with_gigachat(prompt: str) -> str:
    token = os.getenv("GIGACHAT_AUTH_TOKEN")
    model_name = os.getenv("GIGACHAT_MODEL", "gigachat")
    if not token or GigaChat is None:
        # Фолбэк: без LLM просто вернём контекст
        return "(LLM не настроен)\n\n" + prompt
    try:
        with GigaChat(credentials=token, model=model_name, verify_ssl_certs=False) as giga:
            resp = giga.chat(prompt)
            return resp.choices[0].message.content
    except Exception as e:
        return f"Ошибка GigaChat: {e}\n\n" + prompt


@app.on_event("startup")
async def startup():
    load_dotenv()
    await init_db()
    await reload_state()


@app.get("/health")
async def health():
    return {"status": "ok", "chunks": len(CHUNKS)}


@app.post("/reload")
async def reload_state():
    global MODEL, INDEX, CHUNKS, EMB

    load_dotenv()
    md_path = os.getenv("KNOWLEDGE_PATH", "knowledge/knowledge.md")
    model_name = os.getenv("EMBEDDING_MODEL", "intfloat/multilingual-e5-base")
    chunk_size = int(os.getenv("CHUNK_SIZE", 800))
    overlap = int(os.getenv("CHUNK_OVERLAP", 200))

    CHUNKS = load_chunks(md_path, chunk_size=chunk_size, overlap=overlap)
    MODEL = get_model(model_name)
    EMB = embed_texts(MODEL, CHUNKS)
    INDEX = build_index(EMB)

    return {"status": "reloaded", "chunks": len(CHUNKS), "model": model_name}


@app.post("/ask", response_model=AskResponse)
async def ask(req: AskRequest):
    await _ensure_ready()

    top_k = int(req.top_k or os.getenv("TOP_K", 4))
    q_emb = embed_texts(MODEL, [req.question])
    D, I = search(INDEX, q_emb, top_k)

    selected = [CHUNKS[i] for i in I[0] if i >= 0]
    prompt = _compose_prompt(req.question, selected)
    answer = _answer_with_gigachat(prompt)

    log_id = await insert_log(req.chat_id or "", req.question, answer, selected[0] if selected else "")
    return AskResponse(answer=answer, context=selected, log_id=log_id)


@app.post("/feedback")
async def feedback(req: FeedbackRequest):
    if req.rating not in (-1, 0, 1):
        raise HTTPException(400, detail="rating must be -1, 0 or 1")
    await set_rating_by_id(req.log_id, req.rating)
    return {"status": "ok"}
