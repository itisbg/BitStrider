"""Self-check for the thin-liquidity rejected-list trading path (2026-08-12,
widened 2026-08-13).

At the user's request: the guardrails in _passes_guardrails() are UNCHANGED
and still reject these symbols exactly as before (counted in [GUARDRAIL
SUMMARY] same as always). This adds a separate, toggleable path
(TRADE_THIN_LIQUIDITY_REJECTS, off by default) that re-admits a rejected
symbol during regular hours, sized at a flat THIN_LIQUIDITY_POSITION_SIZE_PCT
(3%) instead of the normal POSITION_SIZE_PCT -- confidence-scaling included,
not stacked on top of it.

2026-08-13, user request ("no guard rails for ANY scanner during intra day",
refined same day to "only avoid penny stocks, everything else allow"):
widened from avg_volume/low_float only to every guardrail reason EXCEPT
min_price (RVOL, dollar_vol, gap_chase, market cap included; penny stocks
stay hard-blocked) -- the overnight boundary is still fully enforced by
close_guardrail_fail_positions regardless of what got waived at entry, see
engine/execution/enhanced.py.

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
from engine.execution.enhanced import _apply_thin_liquidity_override, _apply_high_confidence_bonus
from engine.config import (
    THIN_LIQUIDITY_POSITION_SIZE_PCT,
    HIGH_CONFIDENCE_BONUS_THRESHOLD, HIGH_CONFIDENCE_BONUS_MULT,
)

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

# Toggle on, regular hours -> every real guardrail reason gets admitted
# EXCEPT min_price (penny stocks stay hard-blocked) and 'other' (not a
# guardrail at all).
scan.TRADE_THIN_LIQUIDITY_REJECTS = True
for reason in ("avg_volume", "low_float", "rvol", "dollar_vol", "low_mcap", "gap_chase"):
    assert scan._should_admit_thin_liquidity(reason, _regular) is True, f"{reason} should be rescued intraday"
for reason in ("min_price", "other", None):
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

# --- _apply_high_confidence_bonus(): 1.5x multiplier above 92% confidence ---

assert HIGH_CONFIDENCE_BONUS_THRESHOLD == 0.92
assert HIGH_CONFIDENCE_BONUS_MULT == 1.5

risk_info = {"dollar_amount": 150.0, "allocation_pct": 7.5, "tier": "NORMAL"}

# At or below threshold -> unchanged, same dict (no copy).
out = _apply_high_confidence_bonus(risk_info, confidence=0.92, equity=2000.0)
assert out is risk_info, "at the threshold (not above it) must not bonus"
out = _apply_high_confidence_bonus(risk_info, confidence=0.85, equity=2000.0)
assert out is risk_info

# Above threshold -> allocation_pct x 1.5 (7.5 -> 11.25, a multiplier not a
# flat point-add), dollar_amount recomputed from equity at the new pct.
out = _apply_high_confidence_bonus(risk_info, confidence=0.93, equity=2000.0)
assert out["allocation_pct"] == 11.25, f"expected 7.5 x 1.5 = 11.25, got {out['allocation_pct']}"
assert out["dollar_amount"] == 225.0, f"expected 11.25% of $2000 = $225, got {out['dollar_amount']}"
assert risk_info["allocation_pct"] == 7.5, "original dict must not be mutated in place"

# Multiplies whatever allocation_pct already is (e.g. a small-account rate),
# doesn't reset to a fixed value.
small_risk_info = {"dollar_amount": 50.0, "allocation_pct": 5.0, "tier": "NORMAL"}
out = _apply_high_confidence_bonus(small_risk_info, confidence=0.99, equity=1000.0)
assert out["allocation_pct"] == 7.5, f"expected 5.0 x 1.5 = 7.5, got {out['allocation_pct']}"

print("OK: thin-liquidity admit path and high-confidence sizing bonus both check out")
