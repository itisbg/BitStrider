"""Self-check for the 2026-08-18 fix: _sweep_force_closes no longer chases
EOD/guardrail force-closes into extended hours. Both reasons that land in
_force_close_pending (close_eod_positions "eod:...", close_guardrail_fail_positions
"guardrail:...") are deadline/liquidity driven, not "price moved against the
position" -- once regular hours end, the sweep gives up and lets the position
carry overnight under its GTC trailing stop instead of re-chasing at
escalating slip. check_afterhours_stops remains the only path that can
actually exit a position outside regular hours.

Run with:
  python scripts/test_force_close_no_afterhours_chase.py
No network calls -- broker client is faked.
"""
import sys
from pathlib import Path
from types import SimpleNamespace

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import engine.execution.enhanced as enhanced


class FakePosition:
    def __init__(self, symbol, qty, current_price=5.0):
        self.symbol, self.qty, self.current_price = symbol, qty, current_price


class FakeOrder:
    def __init__(self, symbol, time_in_force):
        self.symbol, self.time_in_force, self.id = symbol, time_in_force, f"{symbol}-order"


class FakeClient:
    def __init__(self, resting_orders=None, fail_submit=False):
        self.positions = []
        self.resting_orders = resting_orders or []
        self.submitted = []   # requests submitted via submit_order
        self.cancelled = []   # order ids cancelled
        self.fail_submit = fail_submit

    def get_all_positions(self):
        return self.positions

    def get_orders(self, *args, **kwargs):
        return self.resting_orders

    def cancel_order_by_id(self, order_id):
        self.cancelled.append(order_id)

    def submit_order(self, req):
        if self.fail_submit:
            raise RuntimeError("broker rejected: insufficient qty available")
        self.submitted.append(req)
        return SimpleNamespace(id="fake-order-id")


# ---- No resting GTC: must give up AND re-arm a fresh GTC trailing stop ----
client = FakeClient(resting_orders=[])
ex = enhanced.EnhancedExecutor(client)
ex._current_market_state = lambda: SimpleNamespace(is_regular_hours=False)
client.positions = [FakePosition("FOO", 10, current_price=5.0)]
ex._force_close_pending["FOO"] = {"reason": "eod:VWAPReclaim", "chase_count": 1}

ex._sweep_force_closes()

assert "FOO" not in ex._force_close_pending, "must give up chasing once regular hours end"
assert len(client.submitted) == 1, f"expected exactly one re-arm order, got {client.submitted}"
rearm = client.submitted[0]
assert isinstance(rearm, enhanced.TrailingStopOrderRequest), f"must re-arm a GTC trailing stop, not {type(rearm).__name__}"
assert rearm.time_in_force == enhanced.TimeInForce.GTC

# ---- Resting GTC already present: must give up WITHOUT touching it ----
client2 = FakeClient(resting_orders=[FakeOrder("BAR", enhanced.TimeInForce.GTC)])
ex2 = enhanced.EnhancedExecutor(client2)
ex2._current_market_state = lambda: SimpleNamespace(is_regular_hours=False)
client2.positions = [FakePosition("BAR", 10, current_price=5.0)]
ex2._force_close_pending["BAR"] = {"reason": "guardrail:low_float", "chase_count": 0}

ex2._sweep_force_closes()

assert "BAR" not in ex2._force_close_pending
assert client2.submitted == [], "existing GTC stop must be left alone, not re-armed"
assert client2.cancelled == [], "existing GTC stop must never be cancelled once giving up"

# ---- Still regular hours: unaffected, keeps chasing as before ----
client3 = FakeClient(resting_orders=[])
ex3 = enhanced.EnhancedExecutor(client3)
ex3._current_market_state = lambda: SimpleNamespace(is_regular_hours=True)
client3.positions = [FakePosition("BAZ", 10, current_price=5.0)]
ex3._force_close_pending["BAZ"] = {"reason": "eod:ORB", "chase_count": 0}

ex3._sweep_force_closes()

assert "BAZ" in ex3._force_close_pending, "regular-hours chase must still be in flight, not given up on"
assert any(isinstance(r, enhanced.LimitOrderRequest) for r in client3.submitted), "must still submit a re-chase limit order during regular hours"
assert all(getattr(r, "extended_hours", False) is not True for r in client3.submitted), "regular-hours re-chase must never be extended_hours"

# ---- Stale non-GTC order still resting: must be cancelled before the re-arm ----
client4 = FakeClient(resting_orders=[FakeOrder("QUX", enhanced.TimeInForce.DAY)])
ex4 = enhanced.EnhancedExecutor(client4)
ex4._current_market_state = lambda: SimpleNamespace(is_regular_hours=False)
client4.positions = [FakePosition("QUX", 10, current_price=5.0)]
ex4._force_close_pending["QUX"] = {"reason": "eod:GapBreakout", "chase_count": 2}

ex4._sweep_force_closes()

assert "QUX" not in ex4._force_close_pending
assert client4.cancelled == ["QUX-order"], "stale DAY order must be cancelled before re-arming, else the GTC submit races it for the same qty"
assert len(client4.submitted) == 1 and isinstance(client4.submitted[0], enhanced.TrailingStopOrderRequest)

# ---- GTC re-arm submission itself fails: must NOT give up -- retry next poll ----
client5 = FakeClient(resting_orders=[], fail_submit=True)
ex5 = enhanced.EnhancedExecutor(client5)
ex5._current_market_state = lambda: SimpleNamespace(is_regular_hours=False)
client5.positions = [FakePosition("ZAP", 10, current_price=5.0)]
ex5._force_close_pending["ZAP"] = {"reason": "guardrail:low_mcap", "chase_count": 0}

ex5._sweep_force_closes()

assert "ZAP" in ex5._force_close_pending, "a failed re-arm must stay pending for the next poll, not be silently dropped unprotected"

print("OK: _sweep_force_closes gives up EOD/guardrail chases the instant regular hours end (re-arming a GTC stop only if none rests, cancelling any stale order first, retrying on a failed re-arm instead of dropping tracking), and still chases normally during regular hours")
