"""Self-check for the thin-liquidity rejected-list trading path (2026-08-12).

At the user's request: the low_float/avg_volume guardrails in
_passes_guardrails() are UNCHANGED and still reject these symbols exactly as
before (counted in [GUARDRAIL SUMMARY] same as always). This adds a
separate, toggleable path (TRADE_THIN_LIQUIDITY_REJECTS, off by default) that
re-admits a symbol rejected for ONLY those two reasons, sized at a flat
THIN_LIQUIDITY_POSITION_SIZE_PCT (3%) instead of the normal POSITION_SIZE_PCT
-- confidence-scaling included, not stacked on top of it. min_price, RVOL,
dollar_vol, market cap, and gap_chase rejections are never rescued.

Run with:
  python scripts/test_thin_liquidity_admit.py
No network calls -- both pieces are pure functions, no broker/client needed.
"""
import sys
from pathlib import Path
from types import SimpleNamespace
ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import engine.equity.scan as scan
from engine.equity.strategies import Signal
from engine.execution.enhanced import _apply_thin_liquidity_override
from engine.config import THIN_LIQUIDITY_POSITION_SIZE_PCT

# NOTE: TRADE_THIN_LIQUIDITY_REJECTS's live value is a deployment decision
# (started False 2026-08-12, flipped True same day at the user's request) —
# not asserted here. Both directions are exercised explicitly below via
# scan.TRADE_THIN_LIQUIDITY_REJECTS regardless of what's currently live.
assert THIN_LIQUIDITY_POSITION_SIZE_PCT == 3.0

# --- _should_admit_thin_liquidity(): the scan-side gate ---

_regular  = SimpleNamespace(is_regular_hours=True)
_extended = SimpleNamespace(is_regular_hours=False)

# Toggle off -> never admits, regardless of reason or session.
scan.TRADE_THIN_LIQUIDITY_REJECTS = False
for reason in ("avg_volume", "low_float", "min_price", "rvol", "dollar_vol", "low_mcap", "gap_chase", "other", None):
    assert scan._should_admit_thin_liquidity(reason, _regular) is False, f"toggle off should never admit ({reason})"

# Toggle on, regular hours -> only avg_volume/low_float get admitted; everything
# else guardrail-related still isn't rescued.
scan.TRADE_THIN_LIQUIDITY_REJECTS = True
assert scan._should_admit_thin_liquidity("avg_volume", _regular) is True
assert scan._should_admit_thin_liquidity("low_float", _regular) is True
for reason in ("min_price", "rvol", "dollar_vol", "low_mcap", "gap_chase", "other", None):
    assert scan._should_admit_thin_liquidity(reason, _regular) is False, f"should not rescue {reason} even with the toggle on"

# 2026-08-13: toggle on, but OUTSIDE regular hours -> never admits, even for
# avg_volume/low_float. An entry opened outside regular hours is an overnight
# hold from the moment it fills (NRGV, 2026-08-12: admitted at 16:02 ET,
# 2 min after close, sat failing its guardrail all night).
assert scan._should_admit_thin_liquidity("avg_volume", _extended) is False, "must not admit outside regular hours"
assert scan._should_admit_thin_liquidity("low_float", _extended) is False, "must not admit outside regular hours"
assert scan._should_admit_thin_liquidity("avg_volume", None) is False, "no market_state -> fail closed, no admit"

scan.TRADE_THIN_LIQUIDITY_REJECTS = False  # restore default for anything else in-process

# --- _apply_thin_liquidity_override(): the sizing-side override ---

def _sig(thin=False):
    return Signal("TEST", "buy", 10.0, 0.90, "test reason", "TestStrat", thin_liquidity=thin)

# Not flagged -> risk_info passes through untouched.
risk_info = {"dollar_amount": 150.0, "allocation_pct": 7.5, "tier": "NORMAL"}
out = _apply_thin_liquidity_override(risk_info, _sig(thin=False), equity=2000.0)
assert out is risk_info, "unflagged signal should return the exact same dict, not a copy"

# Flagged -> flat 3% of equity, overriding whatever dollar_amount/allocation_pct
# confidence-scaling had already produced (150.0 here), not stacked on top.
out = _apply_thin_liquidity_override(risk_info, _sig(thin=True), equity=2000.0)
assert out["dollar_amount"] == 60.0, f"expected 3% of $2000 = $60, got {out['dollar_amount']}"
assert out["allocation_pct"] == 3.0
assert risk_info["dollar_amount"] == 150.0, "original dict must not be mutated in place"

print("OK: thin-liquidity admit path — toggle gates by reason correctly, sizing override is a flat 3% override")
