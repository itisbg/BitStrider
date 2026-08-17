"""Self-check for the 2026-08-17 fix: close_eod_positions and
close_guardrail_fail_positions now run every minute through their close
window (schedule.every(1).minutes) instead of once per day, so a position
opened AFTER the first post-close-time tick (ASST/NUAI, opened 15:57 ET,
12 min after both jobs had already run-and-parked for the day) still gets
caught. This checks the idempotency side of that change: a rerun must not
resubmit a close for a symbol it already closed, but must still catch a
symbol that shows up later.

Run with:
  python scripts/test_eod_close_rerun.py
No network calls / no broker connection -- everything (broker client,
volume/float/mcap lookups) is faked.
"""
import datetime
import sys
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import pytz

import engine.execution.enhanced as enhanced
from engine.config import MIN_AVG_DAILY_VOLUME_REGULAR_HOURS, MIN_FLOAT_SHARES, MIN_MARKET_CAP

ET = pytz.timezone("America/New_York")
FIXED_NOW = ET.localize(datetime.datetime(2026, 8, 17, 15, 50))  # inside both close windows


class _FixedDateTime(datetime.datetime):
    @classmethod
    def now(cls, tz=None):
        return FIXED_NOW


class FakePosition:
    def __init__(self, symbol, qty, current_price=5.0, unrealized_pl=1.0):
        self.symbol, self.qty, self.current_price, self.unrealized_pl = symbol, qty, current_price, unrealized_pl


class FakeClient:
    def __init__(self):
        self.positions = []
        self.submitted = []  # symbols a closing order was submitted for, in order

    def get_all_positions(self):
        return self.positions

    def get_orders(self, *args, **kwargs):
        return []

    def cancel_order_by_id(self, order_id):
        pass

    def submit_order(self, req):
        self.submitted.append(req.symbol)


today = datetime.date.today()

with patch("engine.execution.enhanced.datetime.datetime", _FixedDateTime):
    # ---- close_eod_positions: reruns must not double-submit, but must catch new arrivals ----
    client = FakeClient()
    ex = enhanced.EnhancedExecutor(client)
    client.positions = [FakePosition("FOO", 10)]
    ex._entry_log["FOO"] = {"date": today, "strategy": "VWAPReclaim"}  # in EOD_CLOSE_STRATEGIES

    s1 = ex.close_eod_positions()
    assert client.submitted == ["FOO"], client.submitted
    assert "FOO" not in ex._entry_log  # popped on close, same as before this change

    s2 = ex.close_eod_positions()  # FOO still "open" (fake fill never happened) -- must not resubmit
    assert client.submitted == ["FOO"], f"duplicate resubmit: {client.submitted}"

    client.positions.append(FakePosition("BAR", 5))
    ex._entry_log["BAR"] = {"date": today, "strategy": "VWAPReclaim"}
    ex.close_eod_positions()  # a symbol that shows up later must still get caught
    assert client.submitted == ["FOO", "BAR"], client.submitted

    # ---- close_guardrail_fail_positions: same rerun contract, via the per-symbol _guardrail_eod_closed set ----
    client2 = FakeClient()
    ex2 = enhanced.EnhancedExecutor(client2)
    client2.positions = [FakePosition("THIN1", 10), FakePosition("GOOD1", 10)]

    def fake_daily_bars(sym):
        import pandas as pd
        vol = (MIN_AVG_DAILY_VOLUME_REGULAR_HOURS - 1) if sym.startswith("THIN") else 2_000_000
        return pd.DataFrame({"volume": [vol, vol]})

    with patch.object(enhanced, "get_daily_volume_bars", fake_daily_bars), \
         patch.object(enhanced, "_get_float_shares", lambda sym: 500_000_000), \
         patch.object(enhanced, "_get_market_cap", lambda sym: 500_000_000):

        ex2.close_guardrail_fail_positions()
        assert client2.submitted == ["THIN1"], client2.submitted  # GOOD1 passes guardrails, left alone

        ex2.close_guardrail_fail_positions()  # THIN1 still "open" -- must not re-cancel/resubmit
        assert client2.submitted == ["THIN1"], f"duplicate resubmit: {client2.submitted}"

        client2.positions.append(FakePosition("THIN2", 10))
        ex2.close_guardrail_fail_positions()  # a symbol that shows up later must still get caught
        assert client2.submitted == ["THIN1", "THIN2"], client2.submitted

print("OK: both EOD close jobs are safe to rerun every minute -- no duplicate closes, new arrivals still caught")
