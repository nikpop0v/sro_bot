from __future__ import annotations
import os
from typing import List, Optional, Literal
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo
import numpy as np
from fastapi import FastAPI, HTTPException, Query
from fastapi.responses import StreamingResponse
from pydantic import BaseModel
from dotenv import load_dotenv


from .rag import load_chunks, get_model, embed_texts, build_index, search
from .storage import init_db, insert_log, set_rating_by_id, fetch_logs, fetch_logs_between

try:
    from gigachat import GigaChat
except Exception:
    GigaChat = None

PROMPT_TEXT: str = ""

def load_prompt_file(path: str) -> str:
    try:
        with open(path, "r", encoding="utf-8-sig") as f:
            txt = f.read().strip()
            if txt:
                return txt
    except Exception:
        pass
    return "Отвечай кратко. Если ответа нет в контексте — напиши 'Нет ответа.'"

app = FastAPI(title="SRO BOT API", version="1.0.0")

# === Глобальные объекты ===
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
    rating: Literal[-2, -1, 0, 1, 2]  # пятибалльная шкала


async def _ensure_ready():
    if any(x is None for x in [MODEL, INDEX, CHUNKS, EMB]):
        raise HTTPException(503, detail="Model or index not initialized. Call /reload first.")


def _compose_prompt(question: str, context: list[str]) -> str:
    ctx = "\n---\n".join(context) if context else "(пусто)"
    return (
        f"{PROMPT_TEXT}\n\n"
        f"### ВОПРОС\n{question}\n\n"
        f"### КОНТЕКСТ\n{ctx}"
    )


def _answer_with_gigachat(prompt: str) -> str:
    token = os.getenv("GIGACHAT_AUTH_TOKEN")
    model_name = os.getenv("GIGACHAT_MODEL", "gigachat")
    if not token or GigaChat is None:
        return "(LLM не настроен)\n\n" + prompt
    try:
        with GigaChat(credentials=token, model=model_name, verify_ssl_certs=False) as giga:
            resp = giga.chat(prompt)
            return resp.choices[0].message.content
    except Exception as e:
        return f"Ошибка GigaChat: {e}\n\n" + prompt


@app.on_event("startup")
async def startup():
    from dotenv import load_dotenv
    load_dotenv()
    global PROMPT_TEXT
    PROMPT_TEXT = load_prompt_file(os.getenv("PROMPT_PATH", "prompts/system.txt"))
    await init_db()
    await reload_state()

def _compute_range(period: str) -> tuple[str, str]:
    """
    Возвращает (start_iso, end_iso) в UTC (tzinfo=None).
    Если IANA-базы нет, считаем границы по UTC, чтобы не падать.
    """
    tzname = os.getenv("TIMEZONE", "Europe/Moscow")


    tz = None
    try:
        tz = ZoneInfo(tzname)
    except Exception:
        tz = None

    if tz is not None:
        now_local = datetime.now(tz)
        if period == "today":
            start_local = now_local.replace(hour=0, minute=0, second=0, microsecond=0)
        elif period == "week":
            start_local = now_local - timedelta(days=7)
        elif period == "month":
            start_local = now_local - timedelta(days=30)
        else:
            raise HTTPException(400, detail="period must be one of: today, week, month")

        end_local = now_local
        start_utc = start_local.astimezone(timezone.utc).replace(tzinfo=None).isoformat()
        end_utc   = end_local.astimezone(timezone.utc).replace(tzinfo=None).isoformat()
        return start_utc, end_utc


    now_utc = datetime.utcnow()
    if period == "today":
        start_utc_dt = now_utc.replace(hour=0, minute=0, second=0, microsecond=0)
    elif period == "week":
        start_utc_dt = now_utc - timedelta(days=7)
    elif period == "month":
        start_utc_dt = now_utc - timedelta(days=30)
    else:
        raise HTTPException(400, detail="period must be one of: today, week, month")
    return start_utc_dt.isoformat(), now_utc.isoformat()

@app.get("/health")
async def health():
    return {"status": "ok", "chunks": len(CHUNKS)}


@app.post("/reload")
async def reload_state():
    global MODEL, INDEX, CHUNKS, EMB, PROMPT_TEXT

    PROMPT_TEXT = load_prompt_file(os.getenv("PROMPT_PATH", "prompts/system.txt"))

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
    # защита на случай обхода валидации
    if req.rating not in (-2, -1, 0, 1, 2):
        raise HTTPException(400, detail="rating must be one of -2,-1,0,1,2")
    await set_rating_by_id(req.log_id, req.rating)
    return {"status": "ok"}


@app.get("/export")
async def export(
    limit: int = 1000,
    period: str | None = Query(default=None, description="today | week | month"),
):
    if period:
        start_iso, end_iso = _compute_range(period)
        rows = await fetch_logs_between(start_iso, end_iso, limit=5000)
    else:
        rows = await fetch_logs(limit)

    import io, csv
    buf = io.StringIO()
    fieldnames = ["id", "ts", "chat_id", "query", "answer", "top_context", "rating"]
    writer = csv.DictWriter(buf, fieldnames=fieldnames)
    writer.writeheader()
    for r in rows:
        writer.writerow({k: (r.get(k, "") or "") for k in fieldnames})
    buf.seek(0)
    return StreamingResponse(
        iter([buf.getvalue()]),
        media_type="text/csv",
        headers={"Content-Disposition": 'attachment; filename="logs.csv"'},
    )

