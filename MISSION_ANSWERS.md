# Day 14 Lab — Mission Answers

> Mọi output dưới đây là output thật chạy ngày 2026-11-25 trên MacBook Apple M1 (Docker Desktop 29.9.3, Python 3.11). Key/token đã che bằng `***`.

---

## Part 1: Localhost vs Production

### Exercise 1.1: Anti-patterns trong `01-localhost-vs-production/develop/app.py`

1. **Hardcode secret** (dòng 17–18): `OPENAI_API_KEY = "sk-hardcoded-..."` và `DATABASE_URL = "postgresql://admin:password123@..."`. Push lên GitHub là lộ, và đổi key phải sửa code rồi deploy lại.
2. **Log ra secret** (dòng 34): `print(f"[DEBUG] Using key: {OPENAI_API_KEY}")`. Khi chạy, log in ra nguyên văn `[DEBUG] Using key: sk-hardcoded-fake-key-never-do-this`. Ai đọc được log là có key.
3. **`print()` thay cho logging** (dòng 33, 38): không có level, timestamp hay cấu trúc, nên không lọc hay tìm kiếm được trong log aggregator.
4. **Không có health check** (dòng 42): gọi `GET /health` trả **404**. Platform không biết app còn sống để restart.
5. **Port cố định 8000 và bind `localhost`** (dòng 51–52): trên Railway/Render, `PORT` được inject qua env. Bind `localhost` thì trong container, bên ngoài không gọi vào được.
6. **`reload=True` và `DEBUG = True` cứng** (dòng 21, 53): reloader theo dõi file, tốn tài nguyên; debug mode bật cả trong production.
7. **Không xử lý shutdown / không có lifecycle**: không đóng kết nối, không chờ request đang chạy.
8. **Config không tập trung** (`MAX_TOKENS = 500` hardcode): mỗi môi trường muốn giá trị khác thì phải sửa code.

### Exercise 1.2: Chạy bản basic

```text
$ curl -i -X POST "http://localhost:8000/ask?question=Hello"
HTTP/1.1 200 OK
{"answer":"Tôi là AI agent được deploy lên cloud. Câu hỏi của bạn đã được nhận."}

$ curl -i http://localhost:8000/health
HTTP/1.1 404 Not Found
{"detail":"Not Found"}

$ curl -X POST http://localhost:8000/ask -H "Content-Type: application/json" -d '{"question":"Hello"}'
{"detail":[{"type":"missing","loc":["query","question"],"msg":"Field required","input":null}]} [HTTP 422]
```

Log server (trích):

```text
INFO:     Uvicorn running on http://localhost:8000 (Press CTRL+C to quit)
INFO:     Started reloader process [18918] using WatchFiles
[DEBUG] Got question: Hello
[DEBUG] Using key: sk-hardcoded-fake-key-never-do-this      ← secret lộ trong log
```

Chạy được, nhưng chưa production-ready: lộ secret, không có `/health`, chỉ nghe trên `localhost`.

### Exercise 1.3: So sánh develop và production

Output bản production:

```text
$ curl http://localhost:8000/health
{"status":"ok","uptime_seconds":3.7,"version":"1.0.0","environment":"development","timestamp":"2026-11-25T09:05:16.788881+00:00"} [HTTP 200]
$ curl http://localhost:8000/ready
{"ready":true} [HTTP 200]
$ curl -X POST http://localhost:8000/ask -H "Content-Type: application/json" -d '{"question": "Hello"}'
{"question":"Hello","answer":"Đây là câu trả lời từ AI agent (mock)...","model":"gpt-4o-mini"} [HTTP 200]
```

| Feature | Develop | Production | Tại sao quan trọng? |
|---|---|---|---|
| Config | Hardcode trong code (`OPENAI_API_KEY`, `DEBUG`, port) | `config.py` đọc env (`os.getenv`), có `validate()` fail-fast khi thiếu `AGENT_API_KEY` ở production | Cùng một image chạy dev/staging/prod chỉ bằng đổi env. Secret không nằm trong git. Thiếu config quan trọng thì crash ngay lúc start, không phải lúc có user. |
| Secrets | Có trong code và bị in ra log | Đọc từ env, không log secret (chỉ log `question_length`) | Lộ secret qua git/log là sự cố bảo mật phổ biến nhất. |
| Health check | Không có (`/health` → 404) | `/health` (liveness, uptime) + `/ready` (readiness) | Platform/LB biết khi nào restart container (liveness) và khi nào ngừng gửi traffic (readiness). |
| Logging | `print()` | `logging` với format JSON | Log có cấu trúc thì parse, lọc, đếm được trong Loki/Datadog; có level để tắt debug ở prod. |
| Shutdown | Đột ngột | `lifespan` để dọn dẹp, uvicorn graceful (chờ request đang chạy) | Deploy/scale-in không cắt ngang request của user, không mất dữ liệu. |
| Host/Port | `localhost:8000` cứng | `HOST=0.0.0.0`, `PORT` từ env | Container/cloud cần bind `0.0.0.0` và port do platform cấp. |
| Reload/Debug | `reload=True` luôn bật | `reload` chỉ khi `DEBUG=true` | Reloader tốn tài nguyên và không ổn định ở production. |
| CORS | Không cấu hình | `ALLOWED_ORIGINS` từ env | Chỉ frontend được phép mới gọi được API từ trình duyệt. |

**Phát hiện khi chạy thật (bản production cũng chưa hoàn hảo):** log của bản production **không có dòng JSON nào**, chỉ có `WARNING:root:OPENAI_API_KEY not set — using mock LLM`. Nguyên nhân: `config.py` gọi `logging.warning()` lúc import, việc này tự cấu hình root logger (level WARNING, format mặc định) **trước** khi `app.py` gọi `logging.basicConfig(...)`, nên `basicConfig` sau đó không còn tác dụng. Mọi `logger.info(json.dumps(...))` bị nuốt. Ngoài ra, `signal.signal(SIGTERM, handle_sigterm)` đăng ký lúc import sẽ bị uvicorn ghi đè khi server start. Bài học: cấu hình logging phải chạy đầu tiên (hoặc `basicConfig(force=True)`), và nên kiểm tra log bằng cách chạy thật chứ không chỉ đọc code. Ở Part 6 tôi dùng `basicConfig(..., force=True)` và formatter JSON riêng.

---

## Part 2: Docker

### Exercise 2.1: Dockerfile cơ bản (`02-docker/develop/Dockerfile`)

1. **Base image:** `python:3.11`, bản Debian đầy đủ (có gcc, header, tool build), nên rất nặng.
2. **Working directory:** `/app`. Mọi `COPY`/`RUN`/`CMD` sau đó chạy tương đối từ đây.
3. **Vì sao `COPY requirements.txt` trước:** Docker cache theo từng layer. Đặt requirements + `pip install` trước `COPY app.py` thì khi chỉ sửa code, layer cài thư viện (chậm nhất) được dùng lại từ cache. Nếu `COPY . .` trước thì mỗi lần sửa 1 dòng code là cài lại toàn bộ dependencies.
4. **CMD vs ENTRYPOINT:** `CMD` là lệnh/tham số mặc định, bị ghi đè khi `docker run image <lệnh khác>`. `ENTRYPOINT` là executable cố định, tham số của `docker run` được nối vào sau nó. Hay dùng kết hợp: `ENTRYPOINT ["uvicorn"]` + `CMD ["app:app", "--port", "8000"]`. Cả hai nên viết dạng exec (JSON array) để process là PID 1 và nhận SIGTERM.

### Exercise 2.2: Build và run

```text
$ docker build -f 02-docker/develop/Dockerfile -t my-agent:develop .
$ docker run -p 8000:8000 my-agent:develop
$ curl -X POST "http://localhost:8000/ask?question=What%20is%20Docker%3F"
{"answer":"Container là cách đóng gói app để chạy ở mọi nơi. Build once, run anywhere!"} [HTTP 200]
$ curl http://localhost:8000/health
{"status":"ok","uptime_seconds":3.1,"container":true} [HTTP 200]
```

### Exercise 2.3: Multi-stage build và kích thước image

- **Stage 1 (builder):** `python:3.11-slim` + `apt-get install gcc libpq-dev` để compile được dependency có C extension, rồi `pip install --user` vào `/root/.local`. Stage này không được deploy.
- **Stage 2 (runtime):** `python:3.11-slim` sạch, tạo user non-root `appuser`, chỉ `COPY --from=builder /root/.local`, copy `main.py` + `utils/`, có `HEALTHCHECK`, chạy uvicorn 2 worker.
- **Vì sao nhỏ hơn:** image cuối không chứa gcc, header, apt cache, pip cache và không dựa trên bản Debian full. Chỉ có Python runtime + site-packages + code.

```text
$ docker images | grep my-agent
IMAGE                          ID             DISK USAGE   CONTENT SIZE
my-agent:advanced              a4ee8108f806        265MB         57.5MB
my-agent:develop               7b2abe161907       1.67GB          415MB

$ docker image inspect -f "{{.RepoTags}} {{.Size}}" my-agent:develop my-agent:advanced
[my-agent:develop] 1672169764
[my-agent:advanced] 265493212
```

- Develop: **1672.2 MB** (1 672 169 764 bytes)
- Production: **265.5 MB** (265 493 212 bytes)
- Chênh lệch: **giảm 84.1%** (= (1672.2 − 265.5) / 1672.2). Nếu so content size nén: 415 MB → 57.5 MB, giảm 86.1%.

### Exercise 2.4: Docker Compose stack

```text
                 :80 / :443
Client ──HTTP──▶ ┌────────────┐
                 │   nginx    │  reverse proxy + LB, rate limit 10 r/s/IP,
                 │ (alpine)   │  security headers
                 └─────┬──────┘
                       │ proxy_pass http://agent_backend (agent:8000)
                       ▼                        mạng bridge "internal"
                 ┌────────────┐   REDIS_URL    ┌─────────┐
                 │   agent    │───────────────▶│  redis  │ :6379 (session/rate limit)
                 │ FastAPI x2 │   QDRANT_URL   └─────────┘
                 │  workers   │───────────────▶┌─────────┐
                 └────────────┘                │ qdrant  │ :6333 (vector DB)
                                               └─────────┘
agent depends_on redis + qdrant (condition: service_healthy); chỉ nginx publish port ra host.
```

Services được start và trạng thái:

```text
$ docker compose -p k5-day14-docker ps
SERVICE   IMAGE                   STATUS                    PORTS
agent     k5-day14-docker-agent   Up 20 seconds (healthy)   8000/tcp
nginx     nginx:alpine            Up 20 seconds             0.0.0.0:80->80/tcp, 0.0.0.0:443->443/tcp
qdrant    qdrant/qdrant:v1.9.0    Up 31 seconds (healthy)   6333-6334/tcp
redis     redis:7-alpine          Up 31 seconds (healthy)   6379/tcp
```

Test qua Nginx (cổng 80):

```text
$ curl -i http://localhost/health
HTTP/1.1 200 OK
Server: nginx
X-Frame-Options: DENY
X-Content-Type-Options: nosniff
{"status":"ok","uptime_seconds":19.2,"version":"2.0.0","timestamp":"2026-11-25T09:27:30.755191"}

$ curl -i http://localhost/ask -X POST -H "Content-Type: application/json" -d '{"question": "Explain microservices"}'
HTTP/1.1 200 OK
Server: nginx
{"answer":"Tôi là AI agent được deploy lên cloud. Câu hỏi của bạn đã được nhận."}
```

Các service giao tiếp qua DNS nội bộ của network `internal` (tên service = hostname: `agent`, `redis`, `qdrant`). Chỉ Nginx mở port ra ngoài; agent, redis và qdrant không truy cập trực tiếp từ host được. Lưu ý: `main.py` của stack này chưa thực sự gọi Redis/Qdrant (chỉ nhận `REDIS_URL`/`QDRANT_URL`); compose thể hiện kiến trúc mục tiêu.

---

## Part 3: Cloud Deployment

### Exercise 3.1: Deploy agent Part 6 lên Railway

> Chi tiết, log và ảnh chụp: `DEPLOYMENT.md`, `screenshots/dashboard.png`, `running.png`, `test.png`.

- URL: **https://agent-production-ac611854.up.railway.app**
- Cách làm (deploy luôn agent Part 6, không deploy app mẫu):

  ```bash
  cd 06-lab-complete
  railway init --name day14-agent            # tạo project + link thư mục
  railway add --database redis               # service Redis
  railway add --service agent                # service cho agent
  railway variable set AGENT_API_KEY --stdin --service agent --skip-deploys < key.txt   # key ngẫu nhiên, không in ra
  railway variable set 'REDIS_URL=${{Redis.REDIS_URL}}' --service agent --skip-deploys
  railway variable set ENVIRONMENT=production RATE_LIMIT_PER_MINUTE=10 DAILY_BUDGET_USD=1.0 ...
  railway up --service agent --ci            # build bằng Dockerfile multi-stage (railway.toml: builder = DOCKERFILE)
  railway domain --service agent             # → agent-production-ac611854.up.railway.app
  ```

- 4 lệnh tự kiểm ở CP6 (chỉ in mã HTTP):

  ```text
  $BASE/health                      → 200
  $BASE/ready                       → 200   ({"status":"ready","redis":"ok"})
  POST $BASE/ask (không key)        → 401
  POST $BASE/ask (X-API-Key đúng)   → 200
  ```

- Rate limit trên cloud: `200 ×8 → 429 ×4` (cộng 2 request trước đó = 10/phút), header `retry-after: 39`.
- `check_production_ready.py --public-url …` → `✅ Public URL: GET …/health → 200`, **PASS 12/12 (44/44 checks)**.

### Exercise 3.2: So sánh `railway.toml` và `render.yaml`

| | `railway.toml` (Railway) | `render.yaml` (Render Blueprint) |
|---|---|---|
| Định dạng / phạm vi | TOML, cấu hình **1 service** (build + deploy) | YAML, **Infrastructure as Code cho cả hệ thống**: nhiều service (web + Redis/Key Value) trong 1 file |
| Build | `builder = "DOCKERFILE"` (hoặc NIXPACKS/RAILPACK tự detect) | `runtime: docker` + `dockerfilePath`/`dockerContext` (hoặc `runtime: python` + `buildCommand`) |
| Start / port | Dùng `CMD` của Dockerfile hoặc `startCommand`; Railway inject `$PORT` | Như vậy; Render inject `$PORT` |
| Health check | `healthcheckPath = "/health"`, `healthcheckTimeout` | `healthCheckPath: /health` |
| Restart | `restartPolicyType = "ON_FAILURE"`, `restartPolicyMaxRetries` | Render tự restart, không khai báo trong file |
| Biến môi trường / secret | **Không nằm trong file**: đặt bằng `railway variables` / dashboard; Redis gắn bằng reference `${{Redis.REDIS_URL}}` | **Khai báo trong file**: giá trị thường ghi thẳng (`value:`), secret dùng `generateValue: true` (Render tự sinh) hoặc `sync: false` (nhập tay trên dashboard), Redis nối bằng `fromService: … property: connectionString` |
| Hạ tầng phụ (Redis) | Thêm qua CLI/dashboard (`railway add --database redis`), không khai báo trong `railway.toml` | Khai báo luôn service `type: keyvalue` (Redis) trong cùng file |
| Region / plan | Chọn trên dashboard | `region: singapore`, `plan: free` ngay trong file |
| Deploy | `railway up` đẩy code từ máy lên (không cần GitHub) | Connect GitHub repo → Blueprint, auto-deploy mỗi lần push |

**Vì sao khác:** Railway thiên về "CLI-first", mỗi file mô tả cách build/chạy một service, còn biến và add-on quản lý ở tầng project. Render thiên về GitOps: một Blueprint mô tả toàn bộ hạ tầng và được sync từ git, nên cần cú pháp `generateValue`/`sync: false` để vẫn khai báo được secret mà không commit giá trị thật. Bản tôi viết cho Part 6 có cả hai file (`06-lab-complete/railway.toml`, `06-lab-complete/render.yaml`; Render gồm web service Docker + Key Value `day14-redis`, `REDIS_URL` lấy bằng `fromService`).

### Exercise 3.3: Pipeline Cloud Run (`cloudbuild.yaml` + `service.yaml`)

Khi push lên `main`, Cloud Build chạy 4 bước nối tiếp nhau bằng `waitFor`:

1. **test:** chạy `pytest tests/` trong `python:3.11-slim`; fail thì dừng pipeline.
2. **build:** build image, gắn 2 tag `:$COMMIT_SHA` và `:latest`, dùng `--cache-from :latest` để tận dụng layer cache.
3. **push:** đẩy image lên Container Registry.
4. **deploy:** `gcloud run deploy` image đúng `$COMMIT_SHA` (nên rollback được theo commit) lên `asia-southeast1`, `min-instances=1` (tránh cold start), `max=10`, 512Mi/1 CPU, secret `OPENAI_API_KEY` lấy từ Secret Manager.

`service.yaml` mô tả cùng service dạng IaC (Knative): autoscale 1–10 instance, `containerConcurrency: 80`, liveness `/health`, startup probe `/ready`, `AGENT_API_KEY`/`OPENAI_API_KEY` lấy qua `secretKeyRef`, không hardcode.

---

## Part 4: API Security

### Exercise 4.1: API key (`04-api-gateway/develop/app.py`, chạy với `AGENT_API_KEY=secret-key-123`)

- **Check ở đâu:** dependency `verify_api_key()` (đọc header `X-API-Key` bằng `APIKeyHeader(auto_error=False)`), gắn vào `/ask` bằng `Depends(verify_api_key)`.
- **Sai key:** thiếu header thì **401**; có header nhưng sai thì **403**. (Part 6 yêu cầu cả hai trường hợp đều trả 401; ở Part 6 tôi trả 401 cho cả hai.)
- **Rotate key:** key đọc từ env `AGENT_API_KEY`, nên đổi biến trên platform rồi restart/redeploy, không sửa code. Muốn rotate không downtime thì cho phép tạm 2 key cùng lúc (key cũ + key mới), chuyển client sang key mới rồi gỡ key cũ.

```text
# Không có key
$ curl -i http://localhost:8000/ask -X POST -H "Content-Type: application/json" -d '{"question": "Hello"}'
HTTP/1.1 401 Unauthorized
{"detail":"Missing API key. Include header: X-API-Key: <your-key>"}

# Sai key
$ curl -i http://localhost:8000/ask -X POST -H "X-API-Key: wrong-key" -H "Content-Type: application/json" -d '{"question": "Hello"}'
HTTP/1.1 403 Forbidden
{"detail":"Invalid API key."}

# Có key đúng
$ curl -i http://localhost:8000/ask -X POST -H "X-API-Key: ***" -H "Content-Type: application/json" -d '{"question": "Hello"}'
HTTP/1.1 200 OK
{"question":"Hello","answer":"Agent đang hoạt động tốt! (mock response) Hỏi thêm câu hỏi đi nhé."}
```

### Exercise 4.2: JWT (`04-api-gateway/production`, tài khoản `student`/`demo123`)

**JWT flow (`auth.py`):** `POST /auth/token` kiểm tra username/password, rồi `create_token()` ký payload `{sub, role, iat, exp(+60 phút)}` bằng HS256 với `JWT_SECRET`. Client gửi `Authorization: Bearer <token>`. `verify_token()` verify chữ ký và hạn dùng, rồi lấy `username`/`role` **mà không cần tra database**. Thiếu token thì 401; token hết hạn thì 401; token sai chữ ký thì 403.

```text
$ curl -i -X POST http://localhost:8000/auth/token -H "Content-Type: application/json" -d '{"username": "student", "password": "demo123"}'
HTTP/1.1 200 OK
x-content-type-options: nosniff
x-frame-options: DENY
{"access_token":"eyJ***MASKED***","token_type":"bearer","expires_in_minutes":60,"hint":"Include in header: Authorization: Bearer eyJ***MASKED***"}

# Không có token → 401
$ curl -i -X POST http://localhost:8000/ask -H "Content-Type: application/json" -d '{"question": "Explain JWT"}'
HTTP/1.1 401 Unauthorized
{"detail":"Authentication required. Include: Authorization: Bearer <token>"}

# Có token → 200
$ curl -i -X POST http://localhost:8000/ask -H "Authorization: Bearer $TOKEN" -H "Content-Type: application/json" -d '{"question": "Explain JWT"}'
HTTP/1.1 200 OK
{"question":"Explain JWT","answer":"Đây là câu trả lời từ AI agent (mock)...","usage":{"requests_remaining":9,"budget_remaining_usd":2.1e-05}}
```

(Phát hiện nhỏ: field `budget_remaining_usd` thực ra đang trả `usage.total_cost_usd`, tức **số đã tiêu**, không phải số còn lại. Đây là bug trong code mẫu.)

### Exercise 4.3: Rate limiting

- **Algorithm:** **sliding window log**. Mỗi user có một `deque` timestamp; mỗi request xoá timestamp cũ hơn 60 s rồi đếm. Đây không phải token bucket.
- **Limit:** user thường **10 req/phút** (`rate_limiter_user`), admin 100 req/phút (`rate_limiter_admin`).
- **Bypass cho admin:** trong `/ask`, `limiter = rate_limiter_admin if role == "admin" else rate_limiter_user`. Token có `role=admin` (user `teacher`) dùng limiter 100/phút, tức nới giới hạn chứ không bỏ hẳn.
- **Hạn chế:** state nằm trong RAM của process, nên mỗi worker/instance đếm riêng và mất khi restart. Part 6 chuyển sang Redis sorted set để giải quyết.

Request "Explain JWT" ở trên đã tính 1 lượt, sau đó gọi liên tục 20 lần:

```text
$ for i in {1..20}; do curl -s -o /dev/null -w "%{http_code} " -X POST http://localhost:8000/ask -H "Authorization: Bearer $TOKEN" ...; done
200 200 200 200 200 200 200 200 200 429 429 429 429 429 429 429 429 429 429 429

$ curl -i ... (request bị chặn)
HTTP/1.1 429 Too Many Requests
x-ratelimit-limit: 10
x-ratelimit-remaining: 0
x-ratelimit-reset: 1795598967
retry-after: 59
{"detail":{"error":"Rate limit exceeded","limit":10,"window_seconds":60,"retry_after_seconds":59}}

$ curl http://localhost:8000/me/usage -H "Authorization: Bearer $TOKEN"
{"user_id":"student","date":"2026-11-25","requests":10,"input_tokens":40,"output_tokens":312,"cost_usd":0.000193,"budget_usd":1.0,...}
```

Đúng 10 request thành công (1 + 9), từ request thứ 11 trả 429 kèm `Retry-After`.

### Exercise 4.4: Cost guard

Code: `04-api-gateway/production/cost_guard_redis.py` (có self-check chạy với Redis thật).

```python
MONTHLY_BUDGET_USD = 10.0

def check_budget(user_id: str, estimated_cost: float) -> bool:
    key = f"budget:{user_id}:{datetime.now(timezone.utc):%Y-%m}"   # key theo tháng → tự "reset đầu tháng"
    new_total = r.incrbyfloat(key, estimated_cost)                   # cộng TRƯỚC, atomic trong Redis
    r.expire(key, 32 * 24 * 3600)
    if new_total > MONTHLY_BUDGET_USD:
        r.incrbyfloat(key, -estimated_cost)                          # vượt → hoàn lại rồi từ chối
        return False
    return True
```

**Cách làm:**

- **Track trong Redis:** mọi worker/instance dùng chung một con số, restart không mất.
- **Key theo tháng** `budget:<user>:<YYYY-MM>`: sang tháng mới là key mới, bắt đầu từ 0, nên không cần cron reset. TTL 32 ngày để key cũ tự xoá.
- **"Giữ chỗ" atomic thay vì GET-rồi-INCR:** lời giải mẫu trong CODE_LAB đọc `GET` rồi mới `INCRBYFLOAT`. Hai request song song cùng đọc số cũ nên cùng lọt qua. Tôi đo thực tế với 200 request song song, mỗi request $0.30, budget $10:

  ```text
  check_budget        : 200 request song song × $0.30, chấp nhận 33 → tiêu $9.90 (budget $10)
  check_budget_naive  : 200 request song song × $0.30, chấp nhận 39 → tiêu $11.70 (budget $10)
  self-check OK
  ```

  Bản GET-rồi-INCR vượt budget 17%. `INCRBYFLOAT` là lệnh atomic của Redis, nên cộng trước rồi kiểm tra kết quả (vượt thì trừ lại) không bao giờ để tổng chấp nhận vượt $10.
- **Trong Part 6** (`06-lab-complete/app/cost_guard.py`): `check_budget()` trước khi gọi LLM (đã tiêu ≥ `DAILY_BUDGET_USD` thì **402**), `record_cost()` sau khi gọi, tính theo token thật của request (`INCRBYFLOAT` vào key theo ngày UTC).
- **Rate limit vs cost guard:** rate limit chặn *tốc độ* (chống spam, DoS); cost guard chặn *tổng tiền*. Ví dụ 10 req/phút nhưng mỗi request 2000 token với model đắt thì vẫn đốt hết ngân sách sau vài giờ. Cần cả hai.

---

## Part 5: Scaling & Reliability

### Exercise 5.1: Health checks (`05-scaling-reliability/develop`)

```text
$ curl -i http://localhost:8000/health
HTTP/1.1 200 OK
{"status":"ok","uptime_seconds":3.7,"version":"1.0.0","environment":"development","timestamp":"2026-11-25T09:28:50.884901+00:00","checks":{"memory":{"status":"ok","used_percent":78.1}}}

$ curl -i http://localhost:8000/ready
HTTP/1.1 200 OK
{"ready":true,"in_flight_requests":1}
```

- **`/health` (liveness):** "process còn sống không?" Fail thì platform **restart** container. Không được phụ thuộc Redis/DB, vì Redis chết mà restart agent cũng không chữa được, chỉ gây restart loop.
- **`/ready` (readiness):** "có nên gửi traffic vào không?" Fail (503) thì LB **ngừng route** tới instance nhưng **không restart**. Dùng khi đang khởi động, đang shutdown, hoặc mất Redis. Part 6 làm đúng như vậy: Redis down thì `/ready` 503, còn `/health` vẫn 200.
- (`in_flight_requests: 1` là vì middleware đếm luôn chính request `/ready`.)

### Exercise 5.2: Graceful shutdown

Gửi request rồi ngay lập tức `kill -TERM` process:

```text
# 5.2 Graceful shutdown: gửi request rồi ngay lập tức kill -TERM 47168
request in-flight → {"answer":"Đây là câu trả lời từ AI agent (mock)..."} [HTTP 200]
process exit code: 0
--- server log
INFO:     Shutting down
INFO:     127.0.0.1:61094 - "POST /ask?question=Long%20task HTTP/1.1" 200 OK
INFO:     Waiting for application shutdown.
2026-11-25 16:28:54,048 INFO 🔄 Graceful shutdown initiated...
2026-11-25 16:28:54,048 INFO ✅ Shutdown complete
INFO:     Application shutdown complete.
INFO:     Finished server process [47168]
2026-11-25 16:28:54,049 INFO Received signal 15 — uvicorn will handle graceful shutdown
```

Request vẫn hoàn thành **200**, và `Shutting down` được log **trước** dòng `POST /ask ... 200`: uvicorn ngừng nhận kết nối mới, chờ request in-flight xong, chạy phần shutdown của `lifespan`, rồi thoát với **exit code 0**. Dòng `Received signal 15` của handler tự viết lại xuất hiện **cuối cùng**: uvicorn đã thay handler đó bằng handler của nó lúc start, và chỉ gọi lại handler cũ sau khi tắt xong. Vì vậy ở Part 6 tôi không tự đăng ký SIGTERM mà dùng `lifespan`. Script chấm xác nhận request in-flight vẫn 200, và `docker stop` cho exit 0 trong 0.7 s.

### Exercise 5.3: Stateless design

Bản `production/app.py` lưu session/history ở Redis (`session:<id>`, `setex` TTL 1 giờ, giữ 20 message gần nhất) thay vì dict trong RAM. Lý do: chạy 3 instance thì mỗi instance có RAM riêng. Request 1 vào instance A, request 2 vào B thì B không thấy history; restart hay scale-in cũng làm mất state. Đưa state ra Redis thì instance nào cũng phục vụ được bất kỳ request nào, nên scale ngang và thay thế instance tự do. Part 6 cũng stateless: rate limit (sorted set) và cost (counter) đều nằm trong Redis; script chấm restart agent và vẫn nhận 429.

### Exercise 5.4: Load balancing (3 instance + Nginx, cổng 8080, `POST /chat`)

```text
$ docker compose -p k5-day14-scaling up -d --build --scale agent=3
$ docker compose -p k5-day14-scaling ps
NAME                       STATUS                    PORTS
k5-day14-scaling-agent-1   Up 15 seconds (healthy)   8000/tcp
k5-day14-scaling-agent-2   Up 15 seconds (healthy)   8000/tcp
k5-day14-scaling-agent-3   Up 15 seconds (healthy)   8000/tcp
k5-day14-scaling-nginx-1   Up 15 seconds             0.0.0.0:8080->80/tcp
k5-day14-scaling-redis-1   Up 26 seconds (healthy)   6379/tcp

# 10 request qua Nginx, đếm theo served_by
   3 instance-48985d
   3 instance-627f9c
   4 instance-f71866
```

Nginx (`upstream agent_cluster { server agent:8000; }` + `resolver 127.0.0.11`) phân phối round-robin qua Docker DNS, nên 3 instance nhận 3/3/4 request.

### Exercise 5.5: Test stateless

```text
$ python test_stateless.py
Session ID: 9df24736-f440-4922-a2a1-e3cc822dcead
Request 1: [instance-48985d]  Q: What is Docker?
Request 2: [instance-627f9c]  Q: Why do we need containers?
Request 3: [instance-f71866]  Q: What is Kubernetes?
Request 4: [instance-48985d]  Q: How does load balancing work?
Request 5: [instance-627f9c]  Q: What is Redis used for?
Total requests: 5
Instances used: {'instance-627f9c', 'instance-48985d', 'instance-f71866'}
✅ All requests served despite different instances!
--- Conversation History ---
Total messages: 10
✅ Session history preserved across all instances via Redis!
```

`test_stateless.py` không tự tắt instance, nên tôi làm thêm: tắt 1 instance rồi gửi tiếp vào **cùng session**:

```text
$ docker stop k5-day14-scaling-agent-2
served_by= instance-48985d turn= 7 [HTTP 200]
served_by= instance-f71866 turn= 8 [HTTP 200]
$ curl http://localhost:8080/chat/$SID/history → count = 14
```

5 câu hỏi đi qua cả 3 instance mà history vẫn đủ 10 message. Sau khi tắt instance 2, session tiếp tục trên 2 instance còn lại (200) và history lên 14 message, không mất gì, vì state nằm ở Redis chứ không ở instance. (`turn` lệch +1 do code mẫu đếm sau khi đã append câu hỏi.)

---

## Part 6: Final Project — output `check_production_ready.py`

Chạy đầy đủ (có Docker) trên code cuối cùng trong `06-lab-complete/`, kèm kiểm tra URL public: `python check_production_ready.py --public-url https://agent-production-ac611854.up.railway.app`

```text
✅ 12. Public URL ready (Railway / Render config)
     ✅ Có railway.toml hoặc render.yaml (không chỉ toàn comment)
     ✅ railway.toml: builder + healthcheckPath = "/health"
     ✅ render.yaml: web service + healthCheckPath /health
     ✅ render.yaml: secret không ghi thẳng giá trị (generateValue / sync: false)
     ✅ App lắng nghe theo biến PORT do platform cấp
     ✅ Public URL: GET https://agent-production-ac611854.up.railway.app/health → 200

================================================================
  📋 Checklist Deliverable
     ✅ Dockerfile (multi-stage, < 500 MB)
     ✅ docker-compose.yml (agent + redis)
     ✅ .dockerignore
     ✅ Health check endpoint (GET /health)
     ✅ Readiness endpoint (GET /ready)
     ✅ API Key authentication
     ✅ Rate limiting
     ✅ Cost guard
     ✅ Config từ environment variables
     ✅ Structured logging
     ✅ Graceful shutdown
     ✅ Public URL ready (Railway / Render config)
----------------------------------------------------------------
  Result: 12/12 tiêu chí đạt · 44/44 checks passed
  🎉 PASS 12/12 — PRODUCTION READY! Deploy nào!
================================================================
```

Chi tiết đáng chú ý trong output: image **297 MB**; rate limit 429 sau đúng 5 request (`RATE_LIMIT_PER_MINUTE=5`); restart agent vẫn 429 (state trong Redis); cost guard 402 ở request đầu tiên với budget $0.000001; 23 dòng log JSON hợp lệ; SIGTERM khi đang có request thì request vẫn 200, exit code 0; `docker stop` cho exit code 0 trong 0.7 s.

Thiết kế chính (`06-lab-complete/app/`):

| File | Làm gì |
|---|---|
| `config.py` | Dataclass đọc mọi cấu hình từ env (`PORT`, `ENVIRONMENT`, `AGENT_API_KEY`, `REDIS_URL`, `RATE_LIMIT_PER_MINUTE`, `DAILY_BUDGET_USD`, `LOG_LEVEL`); không có secret mặc định |
| `auth.py` | `APIKeyHeader(auto_error=False)`; thiếu/sai key hoặc server chưa đặt key thì **401**; so sánh bằng `secrets.compare_digest` (chống timing attack) |
| `redis_client.py` | `redis.from_url` không kết nối lúc import, nên app khởi động được khi không có Redis; timeout 2 s để `/ready` trả 503 nhanh |
| `rate_limiter.py` | Sliding window 60 s bằng sorted set (`ZREMRANGEBYSCORE` → `ZCARD` → `ZADD` + `EXPIRE`), key là hash của API key; vượt limit thì **429** + `Retry-After` tính theo request cũ nhất |
| `cost_guard.py` | `INCRBYFLOAT` chi phí theo token vào key theo ngày UTC; vượt `DAILY_BUDGET_USD` thì **402** |
| `main.py` | JSON log formatter (cả log uvicorn trong worker); middleware log mỗi request; `/health`, `/ready`; `/ask` = auth → rate limit → budget → LLM → ghi cost; lỗi Redis → 503 JSON, lỗi lạ → 500 JSON (không lộ stack trace); `lifespan` cho shutdown; `/docs` tắt khi `ENVIRONMENT=production` |
| `Dockerfile` | 2 stage (venv ở builder → runtime slim), user non-root uid 10001, `HEALTHCHECK`, `CMD sh -c "exec uvicorn ... --port ${PORT:-8000} --workers 2"` để uvicorn là PID 1 |

Kiểm tra thêm ngoài script (compose thật): input rỗng thì 422 JSON; body không phải JSON thì 422 JSON; dừng Redis thì `/ready` 503 và `/ask` 503 `{"detail":"Storage unavailable, try again later"}`, log `{"event": "redis_error", ...}`.

---

## AI log

**Claude Code (Claude Opus 5.5)**, một AI agent chạy trong VS Code, làm phần việc nặng: viết code, chạy lệnh, đo đạc, chụp ảnh và viết báo cáo. Tôi điều phối và kiểm duyệt: giao việc, quyết định các bước cần tài khoản hoặc quyền của tôi (tạo tài khoản và đăng nhập Railway, cho phép authorize CLI, dọn ổ đĩa khi đầy), rồi đọc lại kết quả trước khi nộp. Đây là khai báo đầy đủ theo RULES mục 1.

| Phần | AI đã làm gì | Kiểm tra thế nào |
|---|---|---|
| Setup | Fork repo về tài khoản cá nhân, đổi tên theo mẫu trong SUBMISSION.md, clone, tạo venv Python 3.11 | `gh repo view` cho thấy repo PUBLIC, đúng tên |
| Part 6 code | Viết toàn bộ phần TODO trong `06-lab-complete/` (app/*.py, Dockerfile, compose, .dockerignore, .env.example, railway.toml, render.yaml) | `python check_production_ready.py` bản đầy đủ có Docker: **PASS 12/12** ngay lần chạy đầu; thêm test tay trên compose (422, 503 khi tắt Redis, log JSON) |
| Part 1, 2, 4, 5 | Chạy các ví dụ có sẵn, chụp output thật, trả lời câu hỏi | Mọi output dán ở trên là output lệnh thật; số MB lấy từ `docker image inspect`; tự phát hiện 2 bug trong code mẫu (log JSON bị nuốt ở Part 1, `budget_remaining_usd` sai ở Part 4) |
| Part 4.4 | Viết `cost_guard_redis.py` (bản atomic) và so với lời giải mẫu | Self-check `assert` + thí nghiệm 200 request song song trên Redis thật: atomic $9.90 vs mẫu $11.70 |
| Part 7 | Thêm metric `agent_question_length_chars`; chạy load test, eval; sửa `KNOWLEDGE_BASE`; viết CI quality gate; sửa bug `PYTHONUSERBASE` của Dockerfile stack production; chạy Locust, 5 thí nghiệm chaos; chụp ảnh Grafana/Jaeger/Prometheus bằng Safari; viết `BENCHMARK_REPORT.md` | Mọi số trong report đọc lại từ file JSON/CSV/log đã commit bằng script (không gõ tay ước lượng); thời gian alert lấy từ poller ghi `alert_timeline.log`; eval cuối `--min-accuracy 1.0` → 23/23 PASS |
| Deploy | Tôi tạo tài khoản và đăng nhập Railway, rồi giao AI làm tiếp. AI tạo mã đăng nhập CLI và bấm Authorize trong phiên trình duyệt đã đăng nhập, rồi tạo project + Redis + service agent, đặt biến (key sinh ngẫu nhiên, không in ra), `railway up`, tạo domain, chụp 3 ảnh dashboard | 4 lệnh CP6: 200/200/401/200; rate limit 429 trên cloud; build log xác nhận dùng Dockerfile multi-stage; `check_production_ready.py --public-url` PASS 12/12 (44/44) |

Những chỗ phải xử lý trong quá trình làm:

- **Ổ đĩa đầy giữa chừng:** AI dừng lại báo, tôi dọn ổ đĩa rồi cho chạy tiếp.
- **Docker Hub trả 429 (rate limit):** kéo `python:3.11-slim` từ mirror `mirror.gcr.io` rồi gắn lại tag.
- **Stack Part 7 crash loop:** đọc log thấy `ModuleNotFoundError`, xác định nguyên nhân là `HOME=/app`, sửa bằng `PYTHONUSERBASE`.
- **Concurrency 100 cho RPS giảm:** đo latency phía server, kiểm tra `netstat` và thử `127.0.0.1` để loại trừ nguyên nhân trước khi viết kết luận.

Phần kiểm duyệt của tôi: đọc lại code `06-lab-complete/app/`, đối chiếu các con số trong `BENCHMARK_REPORT.md` với file kết quả, và tự chạy lại 4 lệnh CP6 trên URL public.
