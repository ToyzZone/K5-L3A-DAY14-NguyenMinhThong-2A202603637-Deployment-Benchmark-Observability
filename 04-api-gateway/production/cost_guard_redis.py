"""Exercise 4.4 — cost guard $10/tháng/user trong Redis, kèm self-check."""
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
import sys
import redis

MONTHLY_BUDGET_USD = 10.0
r = redis.from_url(sys.argv[1], decode_responses=True)


def check_budget(user_id: str, estimated_cost: float) -> bool:
    """True nếu còn budget (và đã giữ chỗ estimated_cost), False nếu vượt."""
    key = f"budget:{user_id}:{datetime.now(timezone.utc):%Y-%m}"   # key theo tháng → tự "reset đầu tháng"
    new_total = r.incrbyfloat(key, estimated_cost)                   # cộng TRƯỚC, atomic trong Redis
    r.expire(key, 32 * 24 * 3600)
    if new_total > MONTHLY_BUDGET_USD:
        r.incrbyfloat(key, -estimated_cost)                          # vượt → hoàn lại rồi từ chối
        return False
    return True


def check_budget_naive(user_id: str, estimated_cost: float) -> bool:
    """Lời giải mẫu trong CODE_LAB: GET rồi mới INCR → 2 request song song cùng đọc số cũ."""
    key = f"naive:{user_id}:{datetime.now(timezone.utc):%Y-%m}"
    current = float(r.get(key) or 0)
    if current + estimated_cost > MONTHLY_BUDGET_USD:
        return False
    r.incrbyfloat(key, estimated_cost)
    r.expire(key, 32 * 24 * 3600)
    return True


if __name__ == "__main__":
    r.flushdb()
    assert check_budget("alice", 9.5) and check_budget("alice", 0.4)
    assert not check_budget("alice", 0.2), "9.9 + 0.2 > 10 phải bị chặn"
    assert abs(float(r.get(f"budget:alice:{datetime.now(timezone.utc):%Y-%m}")) - 9.9) < 1e-9
    assert check_budget("bob", 1.0), "budget tách riêng từng user"
    for fn in (check_budget, check_budget_naive):
        with ThreadPoolExecutor(50) as ex:
            ok = sum(ex.map(lambda _: fn("carol", 0.3), range(200)))
        print(f"{fn.__name__:20s}: 200 request song song × $0.30, chấp nhận {ok} → tiêu ${ok*0.3:.2f} (budget $10)")
    print("self-check OK")
