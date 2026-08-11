"""Self-check for the post-loss re-entry cooldown race fix (2026-08-11).

Bug: the cooldown (self._afterhours_stop_cooldown) was only ever consulted
in _build_scan_targets() -- a one-time snapshot of which symbols to exclude
from that cycle's scan universe. The cooldown itself is set by a background
thread polling every 10s (detect_stopped_out_positions), so a symbol that
closed at a loss and got rescanned within that ~10s window slipped through:
confirmed 2026-08-11, PLUG closed at a loss, STOP-COOLDOWN logged, then
EXECUTE: BUY PLUG fired 6 seconds later anyway, into a second, bigger loss.

Fix: _validate_trade() -- the live, last-mile gate every order already
passes through right before submission -- now also checks the cooldown
directly, for both long and short.

Run with:
  python scripts/test_reentry_cooldown_gate.py
No network calls -- client/account/signal are stubbed; USE_VIX_ROC_FILTER
is monkeypatched off so the unrelated VIX gate ahead of this one doesn't
need a live check.
"""
import sys
import time
from pathlib import Path
from types import SimpleNamespace
ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import engine.execution.enhanced as enhanced
from engine.execution.enhanced import EnhancedExecutor, OrderType, PDTTracker

enhanced.USE_VIX_ROC_FILTER = False  # unrelated gate ahead of the cooldown check — skip it


def _make_executor(cooldown: dict):
    ex = EnhancedExecutor.__new__(EnhancedExecutor)  # skip __init__ (no broker creds needed)
    ex.pdt = PDTTracker()
    ex._htb_cache = set()
    ex._afterhours_stop_cooldown = cooldown
    return ex


acct = SimpleNamespace(equity=10_000.0, pattern_day_trader=False, daytrade_count=0, buying_power=10_000.0)
signal = SimpleNamespace(symbol="TEST", confidence=0.9, price=10.0)

# --- get_afterhours_cooldown_symbols() itself: expired entries excluded and pruned ---
ex = _make_executor({"EXPIRED": time.monotonic() - 1, "FRESH": time.monotonic() + 999})
active = ex.get_afterhours_cooldown_symbols()
assert active == {"FRESH"}, f"expected only FRESH to still be cooling down, got {active}"
assert "EXPIRED" not in ex._afterhours_stop_cooldown, "expired entry should have been pruned from the dict"

# --- _validate_trade(): blocks a cooling-down symbol, both long and short ---
for order_type in (OrderType.LONG, OrderType.SHORT):
    ex = _make_executor({"TEST": time.monotonic() + 999})  # unexpired
    valid, reason = ex._validate_trade(signal, acct, order_type)
    assert valid is False, f"{order_type}: cooling-down symbol should be blocked, got valid={valid}"
    assert reason and "cooldown" in reason.lower(), f"{order_type}: expected a cooldown reason, got {reason!r}"

print("OK: post-loss re-entry cooldown blocks both long and short at _validate_trade")
