"""
Rate limiting (tiêu chí 7) — sliding window 60 giây bằng Redis sorted set.

Mỗi request là 1 phần tử trong sorted set `ratelimit:<key>`, score = timestamp.
State nằm trong Redis nên mọi worker / instance dùng chung và còn nguyên sau khi
restart agent.
"""
import hashlib
import time
import uuid

from fastapi import HTTPException

from app.config import settings
from app.redis_client import r

WINDOW_SECONDS = 60


def check_rate_limit(key: str) -> None:
    # Hash API key: không lưu key thô làm tên key trong Redis
    rkey = "ratelimit:" + hashlib.sha256(key.encode()).hexdigest()[:16]
    now = time.time()

    pipe = r.pipeline()
    pipe.zremrangebyscore(rkey, 0, now - WINDOW_SECONDS)  # bỏ request ngoài cửa sổ
    pipe.zcard(rkey)                                      # đếm request trong 60s
    _, count = pipe.execute()

    if count >= settings.rate_limit_per_minute:
        oldest = r.zrange(rkey, 0, 0, withscores=True)
        retry_after = int(oldest[0][1] + WINDOW_SECONDS - now) + 1 if oldest else WINDOW_SECONDS
        raise HTTPException(status_code=429, detail="Rate limit exceeded",
                            headers={"Retry-After": str(max(retry_after, 1))})

    pipe = r.pipeline()
    pipe.zadd(rkey, {f"{now}-{uuid.uuid4().hex[:8]}": now})
    pipe.expire(rkey, WINDOW_SECONDS)
    pipe.execute()
    # ponytail: check-then-add không atomic — vài request song song có thể lọt quá limit 1–2 cái.
    #           Cần chặt tuyệt đối thì gom vào 1 Lua script.
