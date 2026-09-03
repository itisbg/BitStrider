"""Self-check for the entry-time window (2026-08-18, at the user's request:
restrict new entries to regular hours only, 09:30-16:00 ET -- was 07:30-20:00;
pre/post-market LiquiditySweep fires were walking the entry re-chase price
20-30% in thin extended-hours books before ever filling). New entries (either
direction) only submitted within the entry window. Existing positions are
unaffected -- every close_*/check_*_stop path runs from the orchestrator main
loop regardless of this window.

2026-08-18, same day, two follow-ups:
  1. "no new buys after 2:45" (2:45 PM CDT = 15:45 ET) -- end tightened
     16:00 -> 15:45. The old 16:00 boundary left a window AFTER
     EOD_CLOSE_TIME started flattening positions where entries still fired
     anyway. Confirmed live: AXTI shorted fresh at 15:55 ET, brand-new risk
     opened in the exact window the bot should only be winding down.
  2. "change the eod close time and no trades time to 3:50pm ET... after
     this no new entry positions only keep the existing positions overnight
     if they meet guardrails, and exit only" -- refined again same day,
     15:45 -> 15:50 for both ENTRY_WINDOW_END_ET and EOD_CLOSE_TIME.

2026-09-01, user request ("time for entry 9:14AM to 11:00AM and 2:45 PM to
3:50PM ET"): the entry window is now TWO disjoint segments -- 09:14-11:00 and
14:45-15:50 -- separated by a midday break (11:00-14:45) during which the
book is hard-flatted and no new entry/re-entry orders are allowed
(in_lunch_break). Universe discovery deliberately keeps running through the
break so the afternoon segment trades on a warm universe.

engine/config.py asserts the segment ordering (START < BREAK_START <=
BREAK_END <= END) and LUNCH_FLAT_TIME_ET == ENTRY_WINDOW_BREAK_START_ET at
import time so the pieces can't drift apart silently.

Run with:
  python scripts/test_entry_window.py
No network calls -- exercises the pure membership functions directly.
"""
import sys
import datetime
from pathlib import Path
import pytz
ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from engine.orchestrator import _within_discovery_window, _within_entry_window
from engine.utils.market import in_lunch_break
from engine.config import (
    DISCOVERY_WINDOW_START_ET,
    ENTRY_WINDOW_START_ET,
    ENTRY_WINDOW_BREAK_START_ET,
    ENTRY_WINDOW_BREAK_END_ET,
    ENTRY_WINDOW_END_ET,
    EOD_CLOSE_TIME,
    LUNCH_FLAT_TIME_ET,
)

assert DISCOVERY_WINDOW_START_ET == "08:55"
assert ENTRY_WINDOW_START_ET == "09:14"
assert ENTRY_WINDOW_BREAK_START_ET == "11:00"
assert ENTRY_WINDOW_BREAK_END_ET == "14:45"
assert ENTRY_WINDOW_END_ET   == "15:50"
assert EOD_CLOSE_TIME        == "15:50"
assert LUNCH_FLAT_TIME_ET    == ENTRY_WINDOW_BREAK_START_ET, "lunch flat must fire exactly when the morning entry segment ends"
assert ENTRY_WINDOW_END_ET  <= EOD_CLOSE_TIME, "entries must never still be open once the EOD close sweep starts"

ET = pytz.timezone("America/New_York")


def _at(h: int, m: int) -> datetime.datetime:
    return ET.localize(datetime.datetime(2026, 8, 14, h, m, 0))


# Discovery warms up well before trading and keeps running through the lunch
# break so the afternoon segment trades on a warm universe.
assert _within_discovery_window(_at(8, 54)) is False, "one minute before discovery -> outside"
assert _within_discovery_window(_at(8, 55)) is True, "exactly at discovery boundary -> inside"
assert _within_discovery_window(_at(9, 24)) is True, "discovery runs before entry window"
assert _within_discovery_window(_at(12, 0)) is True, "discovery keeps running through the lunch break"
assert _within_discovery_window(_at(14, 45)) is True, "discovery still active when the afternoon segment opens"

# Inside the morning entry segment (09:14-11:00, inclusive both ends).
assert _within_entry_window(_at(9, 14)) is True, "exactly at 09:14 open boundary -> inside (inclusive)"
assert _within_entry_window(_at(10, 0)) is True
assert _within_entry_window(_at(11, 0)) is True, "exactly at 11:00 segment-1 close -> inside (inclusive)"

# Inside the afternoon entry segment (14:45-15:50, inclusive both ends).
assert _within_entry_window(_at(14, 45)) is True, "exactly at 14:45 segment-2 open -> inside (inclusive)"
assert _within_entry_window(_at(15, 0)) is True
assert _within_entry_window(_at(15, 50)) is True, "exactly at 15:50 close boundary -> inside (inclusive)"

# The midday break is outside both segments.
assert _within_entry_window(_at(11, 1)) is False, "one minute into the lunch break -> outside"
assert _within_entry_window(_at(12, 0)) is False
assert _within_entry_window(_at(14, 44)) is False, "one minute before afternoon open -> outside"

# Outside the window entirely.
assert _within_entry_window(_at(9, 13)) is False, "one minute before 09:14 open -> outside"
assert _within_entry_window(_at(15, 51)) is False, "one minute after close -> outside"
assert _within_entry_window(_at(15, 55)) is False, "AXTI incident time -> must stay outside"
assert _within_entry_window(_at(16, 0)) is False, "old 16:00 boundary -> now outside"
assert _within_entry_window(_at(7, 30)) is False, "pre-market -> outside"
assert _within_entry_window(_at(3, 0)) is False, "overnight -> outside"
assert _within_entry_window(_at(23, 59)) is False, "late night -> outside"

# Lunch-break membership mirrors the gap exactly.
assert in_lunch_break(_at(10, 59)) is False, "one minute before lunch -> not in break"
assert in_lunch_break(_at(11, 0)) is True, "break starts at 11:00 (inclusive)"
assert in_lunch_break(_at(12, 0)) is True
assert in_lunch_break(_at(14, 44)) is True
assert in_lunch_break(_at(14, 45)) is False, "break ends at 14:45 (exclusive -- afternoon opens)"
assert in_lunch_break(_at(15, 0)) is False

print("OK: entry window = [09:14-11:00] + [14:45-15:50] ET, lunch break [11:00-14:45) is fully flat, discovery runs through the break, nothing extends past EOD_CLOSE_TIME")
