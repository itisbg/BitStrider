"""Self-check for the regular-hours vs pre/after-hours liquidity floor split
(2026-08-11, at the user's request).

MIN_FLOAT_SHARES (200M) and MIN_AVG_DAILY_VOLUME (1M) were justified entirely
by an after-hours incident (BIOA 2026-07-31) but applied around the clock,
which also silently rejected real, liquid, actively-traded names during
regular hours (HTZ 161.9M float, BTDR 145.6M, IHRT 136.4M all got rejected
2026-08-11 despite not being remotely BIOA-like). Regular hours now use the
looser MIN_FLOAT_SHARES_REGULAR_HOURS / MIN_AVG_DAILY_VOLUME_REGULAR_HOURS;
pre/after-market keep the original floors unchanged.

Run with:
  python scripts/test_liquidity_floor_split.py
No network calls -- exercises the real _effective_liquidity_floors() against
a stubbed market_state.
"""
import sys
from pathlib import Path
from types import SimpleNamespace
ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from engine.equity.scan import _effective_liquidity_floors
from engine.config import (
    MIN_FLOAT_SHARES, MIN_FLOAT_SHARES_REGULAR_HOURS,
    MIN_AVG_DAILY_VOLUME, MIN_AVG_DAILY_VOLUME_REGULAR_HOURS,
)

# Sanity: regular-hours floors are actually looser, not accidentally inverted.
assert MIN_FLOAT_SHARES_REGULAR_HOURS < MIN_FLOAT_SHARES, "regular-hours float floor should be lower"
assert MIN_AVG_DAILY_VOLUME_REGULAR_HOURS < MIN_AVG_DAILY_VOLUME, "regular-hours volume floor should be lower"

# Regular hours -> loosened floors.
min_float, min_vol = _effective_liquidity_floors(SimpleNamespace(is_regular_hours=True))
assert min_float == MIN_FLOAT_SHARES_REGULAR_HOURS
assert min_vol == MIN_AVG_DAILY_VOLUME_REGULAR_HOURS

# Pre/after-hours -> original, stricter (BIOA-driven) floors.
min_float, min_vol = _effective_liquidity_floors(SimpleNamespace(is_regular_hours=False))
assert min_float == MIN_FLOAT_SHARES
assert min_vol == MIN_AVG_DAILY_VOLUME

# market_state=None -> fail toward the stricter floors, not a crash.
min_float, min_vol = _effective_liquidity_floors(None)
assert min_float == MIN_FLOAT_SHARES
assert min_vol == MIN_AVG_DAILY_VOLUME

print("OK: regular-hours liquidity floors are looser; pre/after-hours and missing market_state stay strict")
