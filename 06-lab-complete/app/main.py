"""
Production AI Agent — Part 6 Final Project.

Luồng /ask: X-API-Key (401) → rate limit (429) → cost guard (402) → mock LLM → ghi cost.
Chạy: uvicorn app.main:app --workers 2   ·   Kiểm tra: python check_production_ready.py
"""
import json
import logging
import time
from contextlib import asynccontextmanager
from datetime import datetime, timezone

import redis
from fastapi import Depends, FastAPI, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field

from app.auth import verify_api_key
from app.config import settings
from app.cost_guard import check_budget, record_cost
from app.rate_limiter import check_rate_limit
from app.redis_client import r, redis_healthy
from utils.mock_llm import ask as llm_ask


# ── Structured logging: mỗi dòng log là 1 JSON object ──
class JsonFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        entry = {
            "ts": datetime.now(timezone.utc).isoformat(timespec="milliseconds"),
            "lvl": record.levelname,
            "logger": record.name,
            "event": record.getMessage(),
            **getattr(record, "data", {}),
        }
        if record.exc_info:
            entry["exc"] = self.formatException(record.exc_info)
        return json.dumps(entry, ensure_ascii=False, default=str)


_handler = logging.StreamHandler()
_handler.setFormatter(JsonFormatter())
logging.basicConfig(level=settings.log_level, handlers=[_handler], force=True)
# Log của uvicorn trong worker cũng ra JSON; access log tắt vì middleware bên dưới đã log từng request
for name in ("uvicorn", "uvicorn.error"):
    for h in logging.getLogger(name).handlers:
        h.setFormatter(JsonFormatter())
logging.getLogger("uvicorn.access").disabled = True
logger = logging.getLogger("agent")


def log(event: str, level: int = logging.INFO, **data) -> None:
    logger.log(level, event, extra={"data": data})


# ── Graceful shutdown ──
# uvicorn tự xử lý SIGTERM: ngừng nhận kết nối mới, chờ request đang chạy xong,
# rồi chạy phần sau `yield` và thoát với exit code 0. Không cần tự đăng ký signal handler.
@asynccontextmanager
async def lifespan(app: FastAPI):
    log("startup", environment=settings.environment, redis_ok=redis_healthy())
    yield
    log("shutdown", reason="SIGTERM / stop — in-flight requests drained")
    r.close()


app = FastAPI(
    title="Production AI Agent",
    lifespan=lifespan,
    docs_url=None if settings.environment == "production" else "/docs",
    redoc_url=None,
)


@app.middleware("http")
async def log_requests(request: Request, call_next):
    start = time.perf_counter()
    status = 500
    try:
        response = await call_next(request)
        status = response.status_code
        return response
    finally:
        log("request", method=request.method, path=request.url.path, status=status,
            ms=round((time.perf_counter() - start) * 1000, 1))


# ── Xử lý lỗi: trả JSON rõ ràng, không lộ stack trace ra client ──
@app.exception_handler(redis.RedisError)
async def redis_error(request: Request, exc: redis.RedisError):
    log("redis_error", logging.ERROR, path=request.url.path, error=type(exc).__name__)
    return JSONResponse(status_code=503, content={"detail": "Storage unavailable, try again later"})


@app.exception_handler(Exception)
async def unhandled_error(request: Request, exc: Exception):
    logger.error("unhandled_error", exc_info=exc, extra={"data": {"path": request.url.path}})
    return JSONResponse(status_code=500, content={"detail": "Internal server error"})


class AskRequest(BaseModel):
    question: str = Field(..., min_length=1, max_length=2000)
    user_id: str | None = Field(default=None, max_length=100)


def _tokens(text: str) -> int:
    # ponytail: ước lượng ~4 ký tự / token; dùng tokenizer thật (tiktoken) khi gọi LLM thật
    return max(1, len(text) // 4)


@app.get("/health")
def health():
    """Liveness: process còn sống là 200, không phụ thuộc Redis."""
    return {"status": "ok"}


@app.get("/ready")
def ready():
    """Readiness: chỉ nhận traffic khi Redis kết nối được."""
    if not redis_healthy():
        return JSONResponse(status_code=503, content={"status": "not ready", "redis": "down"})
    return {"status": "ready", "redis": "ok"}


@app.post("/ask")
def ask(body: AskRequest, api_key: str = Depends(verify_api_key)):
    check_rate_limit(api_key)
    check_budget()
    answer = llm_ask(body.question)
    cost = record_cost(_tokens(body.question), _tokens(answer))
    log("ask", user_id=body.user_id, q_chars=len(body.question), cost_usd=round(cost, 8))
    return {"question": body.question, "answer": answer, "cost_usd": round(cost, 8)}
