"""
API Key authentication (tiêu chí 6).

Client gửi header X-API-Key, phải trùng biến môi trường AGENT_API_KEY.
Thiếu hoặc sai key → 401. auto_error=False để FastAPI không tự trả 403/422.
"""
import secrets

from fastapi import HTTPException, Security
from fastapi.security.api_key import APIKeyHeader

from app.config import settings

api_key_header = APIKeyHeader(name="X-API-Key", auto_error=False)


def verify_api_key(api_key: str | None = Security(api_key_header)) -> str:
    """Dependency cho /ask. Trả về key hợp lệ (dùng làm khoá rate limit)."""
    # Chưa đặt AGENT_API_KEY thì từ chối hết, không "mở cửa" mặc định.
    # compare_digest: so sánh thời gian hằng, tránh timing attack.
    if not settings.agent_api_key or not api_key \
            or not secrets.compare_digest(api_key, settings.agent_api_key):
        raise HTTPException(status_code=401, detail="Invalid or missing API key",
                            headers={"WWW-Authenticate": "ApiKey"})
    return api_key
