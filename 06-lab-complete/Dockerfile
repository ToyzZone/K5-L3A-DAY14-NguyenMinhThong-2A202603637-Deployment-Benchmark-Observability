# ── Stage 1: builder — cài dependencies vào virtualenv riêng ──
FROM python:3.11-slim AS builder
RUN python -m venv /opt/venv
ENV PATH="/opt/venv/bin:$PATH"
# Copy requirements trước source: đổi code không làm mất cache layer pip install
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# ── Stage 2: runtime — chỉ mang theo venv + code, không có pip cache / build tools ──
FROM python:3.11-slim AS runtime
ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PATH="/opt/venv/bin:$PATH" \
    PORT=8000
RUN useradd --create-home --uid 10001 agent
WORKDIR /app
COPY --from=builder /opt/venv /opt/venv
COPY --chown=agent:agent app/ ./app/
COPY --chown=agent:agent utils/ ./utils/
USER agent
EXPOSE 8000
HEALTHCHECK --interval=30s --timeout=3s --start-period=10s --retries=3 \
    CMD python -c "import os, urllib.request as u; u.urlopen('http://127.0.0.1:%s/health' % os.getenv('PORT', '8000'), timeout=2)" || exit 1
# exec → uvicorn thay thế sh và là PID 1, nhận SIGTERM trực tiếp khi `docker stop`
CMD ["sh", "-c", "exec uvicorn app.main:app --host 0.0.0.0 --port ${PORT:-8000} --workers 2"]
