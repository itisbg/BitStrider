"""Self-check for the post-loss re-entry cooldown (2026-08-11 hard block,
changed 2026-08-18 to a trailing-buy re-route instead of an outright block).

2026-08-11: the cooldown (self._afterhours_stop_cooldown) was only ever
consulted in _build_scan_targets() -- a one-time snapshot of which symbols to
exclude from that cycle's scan universe. The cooldown itself is set by a
background thread polling every 10s (detect_stopped_out_positions), so a
symbol that closed at a loss and got rescanned within that ~10s window
slipped through: confirmed 2026-08-11, PLUG closed at a loss, STOP-COOLDOWN
logged, then EXECUTE: BUY PLUG fired 6 seconds later anyway, into a second,
bigger loss. Fix at the time: _validate_trade() also checked the cooldown
directly and hard-blocked it, both long and short.

2026-08-18, user request: "instead of blocking the trade enter it with trail
buy" -- _validate_trade no longer rejects a cooling-down symbol at all;
_create_bracket_order's _is_reentry_signal() picks it up instead and routes
it through the same trailing-buy path as a same-day re-entry (REENTRY_TRAIL_PCT)
-- it can't fill while the name is still actively falling, same protection
PLUG needed, without shutting the symbol out entirely.

Run with:
  python scripts/test_reentry_cooldown_gate.py
No network calls -- client/account/signal/positions are all stubbed;
MOMENTUM_FRESHNESS_ENABLED/USE_VIX_ROC_FILTER monkeypatched off so the
unrelated gates ahead of/after the cooldown check don't need live checks.
"""
import sys
import time
from pathlib import Path
from types import SimpleNamespace
ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import engine.execution.enhanced as enhanced
from engine.execution.enhanced import EnhancedExecutor, OrderType, PDTTracker

enhanced.USE_VIX_ROC_FILTER      = False  # unrelated gate ahead of the cooldown check
enhanced.MOMENTUM_FRESHNESS_ENABLED = False  # unrelated gate right after it (LONG only)


class _FakeAsset:
    status   = "active"
    tradable = True


class _FakeClient:
    def get_asset(self, symbol):
        return _FakeAsset()


class _FakePositions:
    total_count = 0
    def has_position(self, symbol):
        return False


def _make_executor(cooldown: dict):
    ex = EnhancedExecutor.__new__(EnhancedExecutor)  # skip __init__ (no broker creds needed)
    ex.pdt = PDTTracker()
    ex._htb_cache = set()
    ex._afterhours_stop_cooldown = cooldown
    ex._entries_today = {}
    ex._entries_today_date = None
    ex._no_history_cache = set()
    ex._entry_log = {}
    ex.client = _FakeClient()
    ex.order_cache = {}
    ex._get_positions = lambda force_refresh=False: _FakePositions()
    return ex


acct = SimpleNamespace(equity=10_000.0, pattern_day_trader=False, daytrade_count=0, buying_power=10_000.0)
signal = SimpleNamespace(symbol="TEST", confidence=0.9, price=10.0, thin_liquidity=False)

# --- get_afterhours_cooldown_symbols() itself: expired entries excluded and pruned ---
ex = _make_executor({"EXPIRED": time.monotonic() - 1, "FRESH": time.monotonic() + 999})
active = ex.get_afterhours_cooldown_symbols()
assert active == {"FRESH"}, f"expected only FRESH to still be cooling down, got {active}"
assert "EXPIRED" not in ex._afterhours_stop_cooldown, "expired entry should have been pruned from the dict"

# --- _validate_trade(): a cooling-down symbol is no longer rejected here ---
for order_type in (OrderType.LONG, OrderType.SHORT):
    ex = _make_executor({"TEST": time.monotonic() + 999})  # unexpired
    valid, reason = ex._validate_trade(signal, acct, order_type)
    assert valid is True, (
        f"{order_type}: a cooling-down symbol must pass _validate_trade now — "
        f"the trailing-buy re-route (_is_reentry_signal) is what handles it, "
        f"not a rejection here — got valid={valid}, reason={reason!r}"
    )

# --- _is_reentry_signal(): a cooling-down symbol counts as a re-entry even on
#     its first entry attempt TODAY (the cooldown can span a day boundary) ---
ex = _make_executor({"TEST": time.monotonic() + 999})
assert ex._is_reentry_signal("TEST") is True, "a cooling-down symbol must be flagged for the trailing-buy path"
assert ex._is_reentry_signal("OTHER") is False, "a symbol with no history at all must not be flagged"

print("OK: post-loss cooldown no longer blocks at _validate_trade; _is_reentry_signal picks it up for the trailing-buy path instead")
