# Deployment Information

## Public URL

**https://agent-production-ac611854.up.railway.app**

## Platform

**Railway** (project `day14-agent`, environment `production`, region US East, 1 replica)

| Service | Nguồn | Ghi chú |
|---|---|---|
| `agent` | Agent Part 6, deploy bằng `railway up` từ thư mục `06-lab-complete/` | Build bằng chính `Dockerfile` multi-stage (build log: `load build definition from Dockerfile`, `[builder 1/4] FROM python:3.11-slim`, `[runtime 4/6] COPY --from=builder /opt/venv`). Cấu hình `railway.toml`: `builder = "DOCKERFILE"`, `healthcheckPath = "/health"` |
| `Redis` | Railway Redis (`railway add --database redis`) | Agent nối qua private network `redis.railway.internal:6379`; rate limit và cost guard lưu ở đây |

## Test Commands

Đặt `BASE` là URL public và `AGENT_API_KEY` là key đã đặt trên Railway. Mỗi lệnh chỉ in mã HTTP nên không lộ key:

```bash
BASE=https://agent-production-ac611854.up.railway.app

curl -sS -o /dev/null -w '%{http_code}\n' "$BASE/health"     # 200
curl -sS -o /dev/null -w '%{http_code}\n' "$BASE/ready"      # 200
curl -sS -o /dev/null -w '%{http_code}\n' -X POST "$BASE/ask" -H 'Content-Type: application/json' -d '{"question":"Hello"}'   # 401
curl -sS -o /dev/null -w '%{http_code}\n' -X POST "$BASE/ask" -H 'Content-Type: application/json' -H "X-API-Key: $AGENT_API_KEY" -d '{"question":"Hello"}'   # 200
```

Kết quả chạy ngày 2026-11-25 (10:23 UTC):

```text
200
200
401
200
```

Body trả về:

```text
$ curl -s $BASE/health
{"status":"ok"}
$ curl -s $BASE/ready
{"status":"ready","redis":"ok"}
$ curl -s -X POST "$BASE/ask" -H "X-API-Key: ***" -H 'Content-Type: application/json' -d '{"question":"What is deployment?"}'
{"question":"What is deployment?","answer":"Deployment là quá trình đưa code từ máy bạn lên server để người khác dùng được.","cost_usd":1.2e-05}
```

### Rate limiting (10 req/phút/key, state trong Redis)

```text
$ for i in {1..12}; do curl -s -o /dev/null -w "%{http_code} " -X POST "$BASE/ask" -H "X-API-Key: $AGENT_API_KEY" -H "Content-Type: application/json" -d "{\"question\":\"test $i\"}"; done
200 200 200 200 200 200 200 200 429 429 429 429
$ curl -i ... (request bị chặn)
HTTP/2 429
retry-after: 39
{"detail":"Rate limit exceeded"}
```

(Trong phút đó đã có 2 request `/ask` thành công ở bước test trước, nên 8 + 2 = 10 rồi tới 429.)

### Script chấm với URL public

```text
$ python check_production_ready.py --public-url https://agent-production-ac611854.up.railway.app
...
     ✅ Public URL: GET https://agent-production-ac611854.up.railway.app/health → 200
  Result: 12/12 tiêu chí đạt · 44/44 checks passed
  🎉 PASS 12/12 — PRODUCTION READY! Deploy nào!
```

### Log trên Railway (structured JSON, Railway tự tách thành field)

```text
[INFO]  ts="2026-11-25T10:23:53.365+00:00" logger="agent" event="ask" user_id=null q_chars=6 cost_usd=0.00001035
[INFO]  ts="2026-11-25T10:23:53.366+00:00" logger="agent" event="request" method="POST" path="/ask" status=200 ms=146.9
[INFO]  ts="2026-11-25T10:23:53.782+00:00" logger="agent" event="request" method="POST" path="/ask" status=429 ms=4.8
```

## Environment Variables Set

Đặt bằng `railway variable set ... --service agent`; **không** ghi trong code hay repo:

| Biến | Giá trị / nguồn |
|---|---|
| `AGENT_API_KEY` | Chuỗi ngẫu nhiên 32 byte (`secrets.token_urlsafe`), truyền qua `--stdin`, không in ra terminal |
| `REDIS_URL` | `${{Redis.REDIS_URL}}`: reference variable tới service Redis (private network) |
| `ENVIRONMENT` | `production` (tắt `/docs`) |
| `RATE_LIMIT_PER_MINUTE` | `10` |
| `DAILY_BUDGET_USD` | `1.0` |
| `LOG_LEVEL` | `INFO` |
| `PORT` | Railway tự cấp; `CMD` của Dockerfile đọc `${PORT:-8000}` |

## Screenshots

- [Deployment dashboard](screenshots/dashboard.png): project `day14-agent`, service `agent` + `Redis` đều Online
- [Service running](screenshots/running.png): deployment ACTIVE, "Deployment successful"
- [Test results](screenshots/test.png): HTTP logs của Railway cho đúng chuỗi test: `/health` 200, `/ready` 200, `/ask` 401, `/ask` 200…, rồi 429 khi vượt rate limit

## Ghi chú

- Railway CLI 5.75 cảnh báo `railway.toml` ("Config as Code") sẽ được thay bằng `.railway/railway.ts`, nhưng file cũ vẫn chạy tới 2026-12-01. Bài giữ `railway.toml` vì đề và script chấm yêu cầu file này.
- Đã viết sẵn `06-lab-complete/render.yaml` (web Docker + Key Value Redis, `AGENT_API_KEY: generateValue: true`) nhưng **chưa deploy lên Render**. Phần "nền tảng còn lại" của Part 3 làm bằng bảng so sánh trong `MISSION_ANSWERS.md`.
