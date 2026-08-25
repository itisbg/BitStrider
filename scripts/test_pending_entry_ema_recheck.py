"""Self-check for check_pending_entries_ema (2026-08-24, user request):
"every minute check for the placed orders again for ema condition if the
orders are not place already... when the order is place the ema delta is
met, but next minute the order doesn't execute but the ema delta condition
is not met anymore" -- a resting trailing-buy entry can sit unfilled for a
while; this re-checks the EMA7 alignment gate every minute and cancels the
order if it no longer holds.

Run with:
  python scripts/test_pending_entry_ema_recheck.py
No network calls -- client/orders/bars are all stubbed.
"""
import sys
from pathlib import Path
from types import SimpleNamespace
ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import pandas as pd
import engine.execution.enhanced as enhanced
from engine.execution.enhanced import EnhancedExecutor


class _FakeOrder:
    def __init__(self, status, side):
        self.status = status
        self.side = side


class _FakeClient:
    def __init__(self, orders):
        self._orders = orders          # {order_id: _FakeOrder}
        self.cancelled = []

    def get_order_by_id(self, order_id):
        return self._orders[order_id]

    def cancel_order_by_id(self, order_id):
        self.cancelled.append(order_id)


def _make_executor(orders):
    ex = EnhancedExecutor.__new__(EnhancedExecutor)  # skip __init__ (no broker creds needed)
    ex.client = _FakeClient(orders)
    ex.order_cache = {sym: oid for sym, oid in [("RISING", "o1"), ("FALLING", "o2"), ("FILLED", "o3"), ("SHORTFALL", "o4")]}
    return ex


# get_bars stub: RISING/FALLING symbols get monotonic series, direction by name
_orig_get_bars = enhanced.get_bars
def _stub_get_bars(symbol, period, interval):
    if symbol == "RISING":
        return pd.DataFrame({"close": list(range(1, 40))})       # slope positive
    if symbol == "FALLING":
        return pd.DataFrame({"close": list(range(40, 1, -1))})   # slope negative
    if symbol == "SHORTFALL":
        return pd.DataFrame({"close": list(range(40, 1, -1))})   # slope negative -- correct for a short
    return pd.DataFrame()
enhanced.get_bars = _stub_get_bars

try:
    orders = {
        "o1": _FakeOrder(status="new", side="buy"),               # RISING, buy, EMA7 rising -> aligned, keep
        "o2": _FakeOrder(status="accepted", side="buy"),          # FALLING, buy, EMA7 falling -> NOT aligned, cancel
        "o3": _FakeOrder(status="filled", side="buy"),            # FILLED already -- must be skipped, no re-check
        "o4": _FakeOrder(status="held", side="sell"),             # SHORTFALL, sell (short), EMA7 falling -> aligned for a short, keep
    }
    ex = _make_executor(orders)
    ex.check_pending_entries_ema()

    assert ex.client.cancelled == ["o2"], f"expected only FALLING's order (o2) cancelled, got {ex.client.cancelled}"
    assert "FALLING" not in ex.order_cache, "cancelled symbol must be evicted from order_cache"
    assert "RISING" in ex.order_cache, "aligned long must not be cancelled"
    assert "SHORTFALL" in ex.order_cache, "aligned short must not be cancelled"
    assert "FILLED" in ex.order_cache, "already-filled order must be left alone (not re-checked, not evicted)"

    print("OK: check_pending_entries_ema cancels only the resting order whose EMA7 alignment has since failed")
finally:
    enhanced.get_bars = _orig_get_bars
