"""Self-check for disabling VWAPFadeStrategy (2026-08-14, at the user's
request, after a backtest of 89 matched entry/exit VWAPFade trades since
2026-08-03: net-negative at every confidence bucket tested (37% win rate,
-0.85% avg P&L overall), and the 90%+ bucket -- its own ceiling, since the
signal's own confidence formula caps at 0.90 -- was the WORST bucket (22%
win rate). Confidence has no predictive value for this strategy's outcomes,
so a stricter confidence gate can't fix it; disabled outright instead.

Run with:
  python scripts/test_vwap_fade_disabled.py
No network calls -- just checks get_strategy_instances()'s composition.
"""
import sys
from pathlib import Path
ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from engine.config import VWAP_FADE_ENABLED
from engine.equity.strategies import get_strategy_instances, VWAPFadeStrategy

assert VWAP_FADE_ENABLED is False, "disabled per the 2026-08-14 backtest — flip true to re-enable"

for bull in (True, False):
    names = [type(s).__name__ for s in get_strategy_instances(bull_regime=bull)]
    assert "VWAPFadeStrategy" not in names, f"VWAPFadeStrategy must be excluded (bull_regime={bull})"

print("OK: VWAPFadeStrategy is excluded from get_strategy_instances() while VWAP_FADE_ENABLED is False")
