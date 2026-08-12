"""Self-check for the protect_positions() qty_available sign bug (2026-08-12).

Alpaca mirrors qty_available's sign to qty for shorts -- a fully-free -26
share short position reports qty_available="-26", not "+26". The old check
(`if qty_available <= 0: continue`) is only correct for longs; for shorts
it's true unconditionally, so every open short (confirmed live: ACHR, CORZ,
IREN, MARA, ONON, SE, WULF -- all 7 open shorts in the account) was being
skipped every single cycle, leaving the entire short book with zero
trailing-stop protection. 0 (not sign) is what actually means "fully
reserved by another order" on either side.

Run with:
  python scripts/test_protect_positions_shorts.py
No network calls -- client is stubbed, submit_order just records what would
have been submitted.
"""
import sys
from pathlib import Path
from types import SimpleNamespace
ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import engine.execution.enhanced as enhanced
from engine.execution.enhanced import EnhancedExecutor

enhanced.get_dynamic_tier = lambda sym, price: {"ts": 8.0, "tier": "NORMAL", "tp": None, "atr_pct": 0}


class _FakeClient:
    def __init__(self, positions):
        self._positions = positions
        self.submitted = []

    def get_all_positions(self):
        return self._positions

    def get_orders(self):
        return []  # nothing already covered

    def submit_order(self, req):
        self.submitted.append(req.symbol)
        return SimpleNamespace(id="fake-order-id")


def _pos(symbol, qty, qty_available, price=10.0):
    return SimpleNamespace(symbol=symbol, qty=str(qty), qty_available=str(qty_available),
                            current_price=price, avg_entry_price=price)


def _make_executor(positions):
    ex = EnhancedExecutor.__new__(EnhancedExecutor)  # skip __init__ (no broker creds needed)
    ex.client = _FakeClient(positions)
    ex._pdt_overnight_forced = set()
    ex._pdt_stop_blocked = {}
    return ex


positions = [
    _pos("LONG_FREE",  qty=100, qty_available=100),  # long, free -> protect
    _pos("LONG_RESV",  qty=100, qty_available=0),     # long, fully reserved -> skip
    _pos("SHORT_FREE", qty=-26, qty_available=-26),   # short, free -> protect (this was the bug)
    _pos("SHORT_RESV", qty=-26, qty_available=0),      # short, fully reserved -> skip
]
ex = _make_executor(positions)
ex.protect_positions()

assert "LONG_FREE" in ex.client.submitted, "free long should get a trailing stop"
assert "LONG_RESV" not in ex.client.submitted, "fully-reserved long should be skipped"
assert "SHORT_FREE" in ex.client.submitted, "free short should get a trailing stop -- this was skipped unconditionally before the fix"
assert "SHORT_RESV" not in ex.client.submitted, "fully-reserved short should be skipped"

print("OK: protect_positions() arms trailing stops for free shorts, not just longs")
