# Benchmark & Observability Report — Day 14

**Họ tên:** Nguyễn Minh Thông
**Ngày chạy:** 2026-11-25 (giờ trong log/timeline là UTC)
**Môi trường:** MacBook Apple M1, macOS 27.2 · Docker Desktop 29.9.3 (VM 8 CPU / 7.7 GB RAM) · Python 3.11 · load generator chạy cùng máy với agent

Nguồn số liệu (đều có trong repo):

| Phần | File |
|---|---|
| Load test, eval | `reports/*.json` (bản gốc ở `06-lab-complete/benchmark-observability/develop/reports/`) |
| Locust scale | `benchmark-results/scale_1_stats.csv`, `scale_3_stats.csv` |
| Thời điểm alert đổi trạng thái | `benchmark-results/alert_timeline.log` (poll `/api/v1/alerts` mỗi 5s), `benchmark-results/chaos_experiments.log` (mốc T0 của từng thí nghiệm + số PromQL) |
| Log ↔ trace | `benchmark-results/trace_log_lines.jsonl` |
| Ảnh | `screenshots/grafana-dashboard.png`, `jaeger-slow-trace.png`, `jaeger-error-trace.png`, `prometheus-alert-firing.png` (+ `grafana-chaos-AB.png`, `grafana-exp-E-v1.png`) |

---

## 1. SLO của agent

| SLI | SLO | Lý do chọn ngưỡng này |
|---|---|---|
| p95 latency `/ask` | < **500** ms | LLM mock mất 120–180 ms, cả request ~190–240 ms ở p50–p95 khi tải bình thường (bảng 2.2, `MOCK_SLOW_RATE=0`). 500 ms chừa ~2× headroom cho retrieval + mạng, vẫn là mức người dùng chat thấy "nhanh". Alert đặt lỏng hơn (p95 > 1 s trong 2 phút) để không báo động vì spike ngắn. |
| Error rate `/ask` | < **2** % | Upstream LLM mock lỗi ~1% → SLO 2% chừa error budget gấp đôi mức lỗi nền. Alert ở 5% trong 1 phút = vi phạm rõ rệt, không phải dao động. |
| Accuracy trên golden dataset | ≥ **85** % và **0 regression** so với baseline | Khớp quality gate trong CI (`.github/workflows/quality-gate.yml`, `--min-accuracy 0.85 --baseline`). Bản phát hành hiện tại đạt 100% (23/23); ngưỡng 85% để 1–2 câu dao động không chặn mọi PR, còn "không regression" chặn việc làm hỏng câu đang đúng. |

---

## 2. Benchmark hiệu năng (develop, `load_test.py`)

### 2.1 Concurrency vs latency (agent v2, 300 request mỗi mức, SLO p95 ≤ 500 ms & error ≤ 2%)

| Concurrency | RPS | p50 (ms) | p95 (ms) | p99 (ms) | Error rate | SLO |
|---|---|---|---|---|---|---|
| 1 | 4.29 | 195.6 | 224.1 | 1688.9 | 1.00% | ✅ PASS |
| 10 | 35.82 | 192.6 | 529.1 | 1709.8 | 1.00% | ❌ FAIL (p95) |
| 50 | 102.4 | 198.6 | 261.6 | 1712.1 | 0.33% | ✅ PASS |
| 100 | 57.4 | 409.9 | 4159.5 | 4816.7 | 0.00% | ❌ FAIL (p95) |

**Nhận xét:** RPS **không** tăng tuyến tính.

- **1 → 50:** gần tuyến tính (4.3 → 35.8 → 102.4 req/s). Theo Little's law `RPS ≈ concurrency / latency`: latency gần như không đổi (~0.2 s) vì agent là async (`asyncio.sleep` mô phỏng chờ LLM), một process phục vụ được nhiều request đang chờ cùng lúc.
- **c=100:** RPS **giảm** còn 57.4 và p95 vọt lên 4.16 s. Tôi kiểm tra thêm: log JSON của agent ghi thời gian xử lý từng request *bên trong* app chỉ p50 = 200 ms, p95 = 247 ms ở c=100 (gần như c=1). Vậy ~4 s còn lại nằm **ngoài handler**: request phải xếp hàng trước khi một process uvicorn duy nhất (1 event loop) kịp accept/parse, cộng với load generator cũng là 1 process Python chạy cùng máy. Đã loại trừ 2 nguyên nhân: không có SYN bị drop (`netstat -s` "listen queue overflow" vẫn 0) và không phải do DNS (`127.0.0.1` thay `localhost` cho kết quả như nhau). Bằng chứng nằm trong `evidence/c100_server_vs_client.txt` và hai file `evidence/probe_c100*.json`. Nút thắt ở mức này là **một process / một event loop**, nên muốn lên c=100 thì scale ngang (thêm worker/instance), không phải tối ưu code LLM.
- **c=10 FAIL dù c=50 PASS:** không phải vì tải. Với 3% request chậm (~1.7 s), p95 nằm sát biên của nhóm chậm: p95 = 529 ms (giữa ~220 ms của nhóm nhanh và ~1.7 s của nhóm chậm) cho thấy ở lần chạy c=10 tỉ lệ request chậm ngẫu nhiên chạm sát 5% nên p95 bị nội suy lên (report JSON không lưu từng request nên đây là suy luận từ phân bố, không phải số đếm). Đây chính là lý do SLO cần đủ mẫu và đo trong cửa sổ dài, không kết luận từ 1 lần chạy 300 request.

### 2.2 Tail latency (agent v2, `--requests 300 --concurrency 20`)

| `MOCK_SLOW_RATE` | avg (ms) | p95 (ms) | p99 (ms) |
|---|---|---|---|
| 0 | 195.0 | 240.7 | 261.1 |
| 0.03 (mặc định) | 235.0 | 223.2 | 1699.7 |
| 0.1 | 372.7 | 1690.9 | 1713.7 |

**Nhận xét:**

- **Nhạy nhất là p99, rồi tới p95.** Từ 0 → 0.03: avg chỉ +40 ms, nhưng p99 nhảy từ 261 ms lên 1700 ms (×6.5), vì 3% request chậm nằm trọn trong top 1%–5%. Lên 0.1 (> 5% request chậm): p95 cũng lên 1691 ms, trong khi avg mới 373 ms.
- **Average che giấu nhóm user bị chậm:** ở 0.03, avg 235 ms trông ổn nhưng cứ 100 người thì vài người chờ ~1.7 s. SLO theo average sẽ "PASS" trong khi trải nghiệm của nhóm đó tệ. p95/p99 nói thẳng "x% user chờ lâu hơn N ms", đúng thứ cần cam kết. Average còn bị 1 request cực chậm kéo lệch, còn percentile thì không.

---

## 3. Benchmark chất lượng (develop, `eval/run_eval.py`, 23 câu)

| Chỉ số | Agent v1 | Agent v2 | Agent v2 (sau khi sửa KB) |
|---|---|---|---|
| Accuracy | 4.3% (1/23) | 91.3% (21/23) | **100% (23/23)** |
| Avg tokens / câu | 56.7 | 108.8 | 116.5 |
| Tổng cost (USD) | 0.000398 | 0.000757 | 0.000815 |
| Cost / câu đúng (USD) | 0.00039795 | 0.00003607 | 0.00003543 |
| Quality gate | FAIL (< 80%) | PASS (≥ 80%, 0 regression vs v1) | PASS (`--min-accuracy 1.0`, 0 regression vs v2) |

**Những câu v2 trả lời sai ban đầu và cách sửa:**

| ID | Keyword bị thiếu | Đã sửa gì |
|---|---|---|
| `security-02` "JWT khác gì API key…" | `token`, `hết hạn`, `chữ ký` | Retrieval không tìm được tài liệu nào (KB chưa có gì về JWT) → LLM trả câu generic. Thêm entry keyword `["jwt", "api key"]`: API key cố định; JWT là token có chữ ký số, chứa `exp` (thời điểm hết hạn), server chỉ cần verify chữ ký. |
| `scaling-03` "Kubernetes HPA scale dựa trên…" | `cpu`, `memory`, `custom metrics` | Thêm entry `["hpa", "kubernetes", "autoscal"]`: HPA scale số pod theo CPU, memory hoặc custom metrics (RPS, độ dài hàng đợi). |

Trước khi thêm, tôi đã kiểm tra không có câu hỏi nào khác trong golden set chứa các keyword mới (`grep` dataset), để entry mới không "cướp" top-1 retrieval của câu đang đúng. Kết quả eval cuối: 2 câu được cải thiện, **0 regression**. Đã chép KB sang `production/utils/mock_llm.py` để stack Docker cũng đạt 100%.

**Quality gate trong CI:** chạy v1 với `--baseline reports/eval_v2.json` → in `Câu bị regression : 20`, `Quality gate ... ❌ FAIL`, **exit code 1**. Job `.github/workflows/quality-gate.yml` chạy trên mọi PR đụng tới `benchmark-observability/`: bật agent v2 → `run_eval.py --min-accuracy 0.85 --baseline reports/eval_v2_fixed.json`. Exit code ≠ 0 làm step fail, job fail. Bật "Require status checks to pass" cho nhánh `main` thì PR không merge được.

**Trade-off chất lượng / chi phí / latency giữa v1 và v2:**

- **Chi phí:** v2 tốn **1.9× token/câu** (108.8 vs 56.7) vì prompt nhét thêm tài liệu retrieve, nên tổng cost cao hơn 1.9×. Nhưng tính trên **mỗi câu đúng** thì v2 rẻ hơn **~11×** ($0.000036 vs $0.000398), vì v1 gần như không trả lời đúng câu nào. "Cost / câu" khiến v1 trông rẻ; "cost / câu đúng" cho thấy v1 là trả tiền cho câu trả lời vô dụng.
- **Latency:** v2 thêm bước retrieval ~20 ms và prompt dài hơn, nên LLM chậm hơn (latency mock = base + 0.2 ms × input token). p95 trong eval: v1 182 ms vs v2 336 ms (mẫu 23 câu quá nhỏ để kết luận latency, xem load test).
- **Kết luận:** v2 đáng với mức chi phí tăng thêm. Sửa KB tăng thêm ~7% token để đạt 100%.

---

## 4. Observability stack (production)

> **Sửa lỗi stack trước khi chạy:** `production/Dockerfile` gốc làm agent crash loop (`ModuleNotFoundError: No module named 'uvicorn'`). User `agent` được tạo với `useradd -d /app`, nên `HOME=/app` và Python tìm user-site ở `/app/.local`, trong khi package được copy vào `/home/agent/.local`. Sửa bằng 1 dòng `ENV PYTHONUSERBASE=/home/agent/.local` (đây chính là bẫy CODE_LAB Step 8 cảnh báo). Unit test alert rules: `promtool test rules` → `SUCCESS`.

### 4.1 Screenshot

- [x] Dashboard Grafana khi đang có tải: `screenshots/grafana-dashboard.png` (30 user, 2 instance; thấy cả 2 đợt Locust 100 user lúc scale_1/scale_3 ở đầu đồ thị)
- [x] Một trace chậm trong Jaeger: `screenshots/jaeger-slow-trace.png`
- [x] Một trace lỗi trong Jaeger (event `exception`): `screenshots/jaeger-error-trace.png`
- [x] Trang Alerts của Prometheus, **2 alert cùng firing** (`AgentHighErrorRate` value 0.193, `AgentHighLatencyP95` value 1.89 s): `screenshots/prometheus-alert-firing.png`

### 4.2 Exercise 7.4: đọc dashboard (Locust 30 user, 2 instance)

Số từ Prometheus API lúc 09:47:11 UTC (cùng PromQL với panel); ảnh Grafana chụp ngay trước đó hiển thị p95 = 359 ms, error 0.79%, 18.5 req/s.

1. **p95 vs avg:** p95 (1m) = 495 ms, avg (1m) = 257 ms, p99 (1m) = 1866 ms. Trên cửa sổ 5m: p95 300 ms, avg 243 ms. p95/p99 cách xa avg vì ~3% request chậm (+1.5 s): đó là tail latency. avg bị kéo nhẹ, còn p99 thì phản ánh thẳng nhóm chậm. p95 ở cửa sổ 1m dao động mạnh (359 → 495 ms trong vài giây) vì nó nằm sát biên nhóm chậm 3%.
2. **Requests/s theo instance:** 172.19.0.4 = 9.06 req/s, 172.19.0.8 = 8.96 req/s, nên Nginx chia **đều** (round-robin).
3. **Input tokens/s (1408) > output tokens/s (749):** prompt v2 = system prompt + tài liệu retrieve + câu hỏi, còn câu trả lời chỉ là 1 đoạn ngắn. RAG làm input phình ra. Đó là lý do giá input/output khác nhau và cần theo dõi riêng.
4. **`curl http://localhost:8080/metrics` → `404`:** `nginx.conf` có `location = /metrics { return 404; }`. Metric lộ thông tin nội bộ (route, lưu lượng, version, cost), và ai cũng gọi được thì có thể spam. Prometheus scrape thẳng từng container trong mạng Docker nội bộ (`dns_sd_configs`), không đi qua Nginx.

### 4.3 Thí nghiệm "phá hệ thống"

Locust chạy nền 30 user suốt thí nghiệm. Thời gian lấy từ `alert_timeline.log` (poller 5s, giờ UTC); T0 là lúc `docker compose up -d` tạo lại agent xong với cấu hình mới.

| # | Thay đổi | Alert nào firing | Thời gian từ lúc đổi → firing | Panel nào phản ánh rõ nhất |
|---|---|---|---|---|
| A | `MOCK_ERROR_RATE=0.2` | `AgentHighErrorRate` (value 19.3%) | T0 09:49:16 → pending 09:49:37 → firing **09:50:39 ≈ 83 s** (≈ 20 s để rate[1m] vượt 5% + `for: 1m`) | Error rate (đỏ 20.45%), Requests/s theo status (503 tăng), LLM calls error |
| B | `MOCK_SLOW_RATE=0.3` (giữ A) | `AgentHighLatencyP95` (value 1.89 s) | T0 09:50:54 → pending 09:51:17 → firing **09:53:19 ≈ 145 s** (`for: 2m`) | Latency p95 (1.89 s, đỏ); đồ thị p50/p95/p99/avg: p95, p99 sát 1.9 s, avg chỉ ≈ 0.55 s (đọc trên đồ thị `grafana-chaos-AB.png`); LLM latency p95 |
| C | `LLM_MODEL=mock-llm-large` | `LLMCostBurnRateHigh` | T0 09:54:23 → pending 09:54:42 → firing **09:56:45 ≈ 142 s** (`for: 2m`) | Cost burn rate: **$34.3/giờ** vs ~$2.3/giờ với model small (≈ 15×); LLM latency p95 0.50 s. p95 /ask 743 ms < 1 s nên alert latency không bật |
| D | `docker pause` agent-1 | `AgentInstanceDown` (172.19.0.4:8000) | T0 09:57:33 → pending 09:57:47 → firing **09:58:20 ≈ 47 s** (`for: 30s`) | Instances up 2 → 1. Nginx trả **28 lỗi 504** trong lúc pause, nhưng error rate phía agent chỉ 1.7% (xanh) |
| E | `AGENT_VERSION=v1` | **Không có** (`alerts now: none` lúc 10:04:23) | — | Retrieval hit ratio: 100% → **No data** sau 5 phút; Tokens/s input 1355 → 686, output 720 → 373; cost $2.29 → $1.18/giờ; bảng "Phiên bản đang chạy" đổi sang v1 (`grafana-exp-E-v1.png`) |

Các quan sát khác:

- **Alert chi phí đặt quá thấp cho tải thật.** Với model small, 30 user đã tốn ~$2.3–2.4/giờ, vượt ngưỡng $2/giờ, nên `LLMCostBurnRateHigh` **đã firing từ trước thí nghiệm C**: điều kiện đúng (`activeAt`, bắt đầu pending) từ 09:41:17 khi chạy Locust 100 user, và lần poll đầu tiên lúc 09:49:04 đã thấy nó ở trạng thái firing (`alert_timeline.log`). Alert bật cả khi hệ thống bình thường thì thành nhiễu và người ta sẽ tắt nó. Nên đặt ngưỡng theo budget thực tế, hoặc theo **cost / request**, hoặc so với baseline (vd > 3× trung bình 1 ngày), thay vì một con số tuyệt đối.
- **Vì sao `for` quan trọng:** mỗi lần tạo lại container, target cũ (IP cũ) bị scrape lỗi vài giây trước khi DNS SD (`refresh_interval: 10s`) xoá nó. `AgentInstanceDown` lên `pending` rồi tự về `inactive` sau 5 s, **không firing** nhờ `for: 30s`. Nếu `for: 0s` thì mỗi lần deploy là một lần báo động giả.
- **Vì sao 504 không hiện trên metric agent (D):** request bị kẹt ở container đang pause. Nginx chờ hết `proxy_read_timeout 10s` rồi trả 504. Agent chưa bao giờ xử lý xong request đó nên không đếm. Metric chỉ phản ánh những gì *chính service đó* thấy, nên production cần thêm metric ở load balancer (status code của Nginx) hoặc synthetic check từ bên ngoài.
- **Vì sao `pause` mà không `stop`:** container bị stop biến mất khỏi Docker DNS, Prometheus coi như target không tồn tại (không phải "down"). Pause thì vẫn còn trong DNS nhưng không trả lời, nên `up == 0`.

**Thí nghiệm E không làm alert nào firing. Phát hiện loại sự cố này trong production bằng cách nào?**

Đổi sang v1 làm accuracy rơi từ 100% xuống ~4% (eval ở mục 3), nhưng latency, error rate và instance đều "xanh", thậm chí cost còn giảm. Metric hạ tầng chỉ đo *hệ thống có chạy không*, không đo *câu trả lời có đúng không*. Cần:

1. **Eval/quality gate trước deploy:** chặn ngay từ CI (mục 3). Đổi `AGENT_VERSION` mà chạy qua pipeline thì đã bị chặn bởi exit code 1.
2. **Metric chất lượng ở runtime:** retrieval hit ratio (panel đã có, rơi về no data/0 là dấu hiệu rõ), kèm alert khi hit ratio < ngưỡng; theo dõi `agent_info{version}` để thấy version lạ; tỉ lệ câu trả lời "generic".
3. **Online eval:** định kỳ chạy golden set vào production (synthetic probe), hoặc LLM-as-judge / feedback 👍👎 của người dùng trên một mẫu traffic, rồi alert khi điểm giảm.
4. **Canary:** so sánh chất lượng version mới với version cũ trên một phần traffic trước khi rollout 100%.

### 4.4 Debug bằng trace

Trace chậm nhất có span LLM chậm (`screenshots/jaeger-slow-trace.png`):

- `trace_id`: `b7c1cc8b52e44403cb67e29791585be4`
- Tổng thời gian: **2281.5 ms** (`POST /ask`)
- Span chiếm nhiều thời gian nhất: **`chat mock-llm-small` 2241.5 ms (98.2%)**; `agent.run` 2258.1 ms; `agent.retrieve_context` 16.3 ms (0.7%). Input 74 / output 37 token.
- Dòng log tương ứng (`docker compose logs agent | grep b7c1cc8b…`):

```json
{"ts": "2026-11-25T09:42:56.161780+00:00", "level": "INFO", "service": "observable-agent", "instance": "7f4fe843982c", "event": "llm_call", "trace_id": "b7c1cc8b52e44403cb67e29791585be4", "span_id": "c0ef4384660a2a6b", "request_id": "1b9409bb693f", "model": "mock-llm-small", "agent_version": "v2", "input_tokens": 74, "output_tokens": 37, "cost_usd": 3.3e-05, "llm_ms": 193.6}
{"ts": "2026-11-25T09:42:56.181359+00:00", "level": "INFO", "service": "observable-agent", "instance": "7f4fe843982c", "event": "http_request", "trace_id": "b7c1cc8b52e44403cb67e29791585be4", "span_id": "c0ef4384660a2a6b", "request_id": "1b9409bb693f", "method": "POST", "route": "/ask", "status": 200, "ms": 2278.3}
```

**Đọc trace + log cùng nhau thì kết luận khác với chỉ nhìn trace:**

- Trace nói span chậm nhất là `chat mock-llm-small`, tức "LLM chậm".
- Nhưng log của **cùng request** ghi `llm_ms: 193.6`. Trong `app/main.py`, `llm_ms = result.latency_s`, là thời gian mock *yêu cầu* ngủ, **đã gồm** phần +1500 ms của nhóm `MOCK_SLOW_RATE`. Vậy request này **không** thuộc nhóm slow: mock chỉ xin ngủ 194 ms, còn `asyncio.sleep` được "đánh thức" muộn ~2 s. Một request slow-rate thật chỉ tới ~1.7–1.75 s (bảng 2.2), còn trace này là 2.28 s.
- Trace thứ hai cùng instance `7f4fe843982c`, cách 2 s: `d6cacb36fc10ceee577ce6b9c89a7bcf` dài 2288.9 ms, nhưng `agent.run` chỉ 224 ms và middleware app log `"ms": 234.5`. Ở trace này ~2 s nằm **ngoài** code agent.
- Hai trace rơi vào 09:42:53–56, ~20 s sau khi Locust 100 user bắt đầu ramp lúc chạy scale_3, trên container vừa được tạo, mỗi container giới hạn `cpus: 0.5`.
- **Kết luận (giả thuyết, chưa kiểm chứng bằng metric CPU):** event loop của instance bị nghẽn khoảng 2 s (CPU throttle khi tải dồn lên container 0.5 CPU), nên coroutine nào đang `await` lúc đó cũng bị trễ. Trace chỉ ra *span nào* bị kéo dài, còn log `llm_ms` cho biết *upstream không chậm*.
- Bài học: span `chat` dài **không** đồng nghĩa với LLM chậm. Cần thêm metric saturation (event-loop lag, CPU throttling của container) để phân biệt.

Trace lỗi (`screenshots/jaeger-error-trace.png`): `dc81836bad2dd3ad91760e445aaac80c`, `POST /ask` 503, 127 ms. Span `chat mock-llm-small` có event `exception` (`utils.mock_llm.LLMError: Upstream LLM error: 503 Service Unavailable (mock)`, kèm stacktrace). Log `{"event": "llm_error", "trace_id": "dc81836b…", ...}` khớp trace_id (`benchmark-results/trace_log_lines.jsonl`).
---

## 5. Benchmark scaling (production, Locust headless, 100 users, ramp 10/s, 2 phút)

Số của dòng `POST /ask` trong `*_stats.csv`:

| Số instance | RPS | p50 (ms) | p95 (ms) | p99 (ms) | Failures |
|---|---|---|---|---|---|
| 1 | 60.15 | 190 | 230 | 1700 | 74 / 7169 (1.03%) |
| 3 | 58.94 | 190 | 610 | 1700 | 76 / 7048 (1.08%) |

**Giải thích:** Scale từ 1 lên 3 instance **không cải thiện** RPS hay latency.

1. **RPS bị giới hạn bởi load generator, không phải agent.** Locust là closed-loop: 100 user, mỗi user chờ `between(0.5, 2)` ≈ 1.25 s giữa 2 request. RPS tối đa ≈ 100 / (1.25 + ~0.23) ≈ 67 req/s (tính cả `/health`), khớp với Aggregated đo được 66.2 và 64.6 req/s. Ngay cả 1 instance cũng chưa bão hoà, nên thêm instance không có gì để tăng.
2. **Latency bị chi phối bởi LLM upstream.** Panel "LLM latency p95" phẳng ~195–199 ms suốt cả hai lần chạy. Mỗi request phải chờ LLM ~120–180 ms (+1.5 s với 3% request chậm) bất kể có bao nhiêu instance. p50 = 190 ms và p99 = 1700 ms giống hệt nhau ở cả hai cấu hình.
3. **p95 610 ms ở 3 instance là nhiễu, không phải tệ đi.** p95 nằm đúng biên của nhóm ~3% request chậm + ~1% lỗi, nên chỉ cần lệch vài chục request là p95 nhảy từ ~230 lên ~600 ms (Locust còn làm tròn percentile theo bucket). Ngoài ra, 3 container × `cpus: 0.5` cùng chạy trên VM Docker cạnh Locust, Prometheus, Grafana và Jaeger cũng có thể góp phần.

**Nút thắt:** upstream LLM (latency cố định mỗi call) và chính tải đầu vào. Scale ngang chỉ có ích khi instance bị bão hoà (CPU, event loop, connection), như trường hợp c=100 ở mục 2.1 với 1 process. Muốn thấy lợi ích scale thì cần tăng user / giảm think time cho tới khi 1 instance bão hoà.

---

## 6. Kết luận

- 3 điều học được về benchmark & observability:
  1. **Đo đúng chỗ trước khi kết luận.** Ở c=100, client thấy p95 4.2 s, nhưng log server cho thấy handler chỉ 247 ms. Thiếu số đo phía server thì đã đi tối ưu nhầm code LLM. Tương tự, scale 3 instance không nhanh hơn vì nút thắt nằm ở upstream và load generator.
  2. **Percentile và cửa sổ đo quan trọng hơn con số trung bình.** 3% request chậm đủ biến p99 từ 261 ms thành 1.7 s trong khi avg chỉ tăng 40 ms. Một lần chạy 300 request thì p95 dao động quanh biên nhóm chậm (c=10 FAIL, c=50 PASS), nên SLO phải đánh giá trên cửa sổ dài, nhiều mẫu.
  3. **Metric hạ tầng xanh ≠ agent đúng.** Đổi sang v1 rớt accuracy 96 điểm % mà không alert nào kêu; lỗi 504 ở Nginx cũng không hiện trên metric agent. Cần eval/quality gate, metric chất lượng (hit ratio) và giám sát ở tầng LB / synthetic check bên ngoài.
- Nếu đưa agent này lên production thật, sẽ thêm:
  - Alert **retrieval hit ratio < 80%** và alert khi `agent_info` có version ngoài danh sách cho phép.
  - **Online eval:** synthetic probe chạy golden set mỗi 15 phút vào production, alert khi accuracy < 85%.
  - Metric và alert ở **Nginx** (tỉ lệ 5xx/504 của LB) + **blackbox/synthetic check** từ ngoài, để bắt lỗi agent không thấy (thí nghiệm D).
  - Alert cost theo **cost / request** hoặc **% budget ngày** thay vì $/giờ tuyệt đối (đang báo động giả ở tải 30 user); thêm alert **burn rate error budget** (multi-window, 1h/6h) thay cho ngưỡng 5% cố định.
  - **Saturation:** alert khi `agent_in_flight_requests` hoặc event-loop lag cao, kèm CPU throttling của container (dấu hiệu ở trace `b7c1cc8b…` / `d6cacb36…`, mục 4.4).

---

## Phụ lục: trả lời câu hỏi Exercise 7.1 và câu hỏi thảo luận

**7.1.** Counter: `http_requests_total{method="POST",route="/ask",status="200"} 1.0` (chỉ tăng). Gauge: `agent_in_flight_requests 0.0` (lên/xuống). Histogram: `http_request_duration_seconds_bucket{le="0.2",...}`, `_sum`, `_count`. Nếu label `route` dùng URL thật thì mỗi URL bot quét (`/wp-admin`, `/.env`, `/a1b2c3`…) tạo 1 time series mới cho mỗi tổ hợp method × status × bucket histogram (~14 series/URL). Hàng nghìn URL thành hàng chục nghìn series, Prometheus phình RAM và chậm, đó là một kiểu DoS từ chính monitoring. Dùng `route.path` (template) hoặc `unmatched` thì số series là hằng số. Đã thêm metric `agent_question_length_chars` (Histogram, buckets 20…2000, không dùng label) vào `develop/app.py`; `/metrics` đã hiện `agent_question_length_chars_sum 40.0` sau 1 câu hỏi 40 ký tự.

**Thảo luận:**

1. **Dashboard xanh nhưng trả lời sai:** observability hạ tầng không phát hiện được (đã thấy ở thí nghiệm E). Cần eval/golden set trong CI, online eval, feedback người dùng, metric chất lượng như hit ratio.
2. **Pull model:** Prometheus tự quyết nhịp scrape, biết ngay target nào chết (`up == 0`), service không cần biết địa chỉ monitoring. Với Cloud Run scale về 0 thì pull kém phù hợp: instance ngắn hạn, không địa chỉ cố định, scale-to-zero thì không còn gì để scrape. Nên dùng push (OTLP metrics tới collector, Pushgateway) hoặc metric managed của nền tảng.
3. **Label `user_id` với 100.000 user:** số series ×100.000 (nhân thêm method, status và bucket histogram), nên lên hàng triệu series. Prometheus hết RAM, query chậm hoặc OOM. Per-user nên để ở log/trace hoặc hệ thống analytics, không để ở metric.
4. **Trace mọi request:** tốn CPU/network của app và tốn storage của backend trace. Sampling 10% giảm chi phí 10×, đổi lại có thể bỏ sót đúng request hiếm (lỗi, request chậm p99). Giải pháp là tail-based sampling: giữ 100% trace lỗi/chậm và chỉ sample trace bình thường.
5. **`for: 0s`:** mọi spike ngắn đều báo động, kể cả lúc deploy (đã thấy `AgentInstanceDown` pending 5 s mỗi lần tạo lại container), dẫn tới alert fatigue. **`for: 30m`:** sự cố thật kéo dài 29 phút vẫn im lặng, và error budget bị đốt hết trước khi ai được báo. Cần cân bằng theo mức nghiêm trọng (1–5 phút cho error rate), hoặc dùng multi-window burn-rate alert.
