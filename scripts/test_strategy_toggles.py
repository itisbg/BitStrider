"""Self-check for the strategy enable/disable toggles (2026-08-14, at the
user's request: "disable all that are below 37% win rate" — then refined
same day: "don't disable if the number of trade[s] [is] under 10 ... too
early to judge"). Same backtest methodology as VWAP_FADE_ENABLED, across
all 16 strategies:

Below 37% AND n>=10 -> disabled (confidence gating doesn't rescue either,
no winning bucket even at their own ceiling):
  Momentum           n=25  20% win  -1.73% avg
  PreMarketMomentum  n=25  32% win  -1.60% avg

Below 37% but n<10 -> left enabled, too small a sample to judge:
  Sentiment          n=9   22% win
  LiquiditySweep     n=4   25% win
  PMHighBreakout     n=3   33% win
  Technical          n=3    0% win

FloatRotation (n=35, 37% win) sits exactly AT the threshold, not below
it -- also left enabled pending an explicit call on that tie.

Run with:
  python scripts/test_strategy_toggles.py
No network calls -- just checks get_strategy_instances()'s composition.
"""
import sys
from pathlib import Path
ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from engine.config import (
    VWAP_FADE_ENABLED, MOMENTUM_ENABLED, SENTIMENT_ENABLED, LIQUIDITY_SWEEP_ENABLED,
    PRE_MARKET_MOMENTUM_ENABLED, PM_HIGH_BREAKOUT_ENABLED, TECHNICAL_ENABLED,
)
from engine.equity.strategies import get_strategy_instances

# n>=10 and below 37% win rate -> disabled.
DISABLED = {
    "VWAPFadeStrategy":          VWAP_FADE_ENABLED,
    "MomentumStrategy":          MOMENTUM_ENABLED,
    "PreMarketMomentumStrategy": PRE_MARKET_MOMENTUM_ENABLED,
}
for name, enabled in DISABLED.items():
    assert enabled is False, f"{name} expected disabled (False) per the 2026-08-14 backtest"

# n<10 (too early to judge) or at/above the 37% threshold -> stay enabled.
STILL_ENABLED_FLAGS = {
    "SentimentStrategy":      SENTIMENT_ENABLED,
    "LiquiditySweepStrategy": LIQUIDITY_SWEEP_ENABLED,
    "PMHighBreakoutStrategy": PM_HIGH_BREAKOUT_ENABLED,
    "TechnicalStrategy":      TECHNICAL_ENABLED,
}
for name, enabled in STILL_ENABLED_FLAGS.items():
    assert enabled is True, f"{name} expected enabled (True) — sample too small (n<10) to judge"

STILL_ENABLED_UNCONDITIONAL = {"FloatRotationStrategy", "GapBreakoutStrategy", "ORBStrategy",
                                "TrendBreakerStrategy", "VWAPReclaimStrategy"}

for bull in (True, False):
    names = {type(s).__name__ for s in get_strategy_instances(bull_regime=bull)}
    for disabled_name in DISABLED:
        assert disabled_name not in names, f"{disabled_name} must be excluded (bull_regime={bull})"
    for kept_name in set(STILL_ENABLED_FLAGS) | STILL_ENABLED_UNCONDITIONAL:
        assert kept_name in names, f"{kept_name} must still be active (bull_regime={bull})"

print("OK: Momentum/PreMarketMomentum/VWAPFade are excluded (n>=10, below 37% win rate); "
      "Sentiment/LiquiditySweep/PMHighBreakout/Technical stay active (n<10, too early to judge); "
      "FloatRotation and the rest stay active")
