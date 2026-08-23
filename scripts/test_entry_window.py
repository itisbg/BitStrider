"""Self-check for the entry-time window (2026-08-18, at the user's request:
restrict new entries to regular hours only, 09:30-16:00 ET -- was 07:30-20:00;
pre/post-market LiquiditySweep fires were walking the entry re-chase price
20-30% in thin extended-hours books before ever filling). New entries (either
direction) only submitted within [ENTRY_WINDOW_START_ET, ENTRY_WINDOW_END_ET].
Existing positions are unaffected -- every close_*/check_*_stop path runs
from the orchestrator main loop regardless of this window.

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

2026-08-22, user request ("Trading hours 9.25am ET to 3.50PM ET"): start
moved 09:30 -> 09:25 to line up with the TI-scraper's own 09:25 ET
kickstart run.

engine/config.py asserts ENTRY_WINDOW_END_ET <= EOD_CLOSE_TIME at import
time so the two can't drift apart silently again.

Run with:
  python scripts/test_entry_window.py
No network calls -- exercises the pure function _within_entry_window directly.
"""
import sys
import datetime
from pathlib import Path
import pytz
ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from engine.orchestrator import _within_entry_window
from engine.config import ENTRY_WINDOW_START_ET, ENTRY_WINDOW_END_ET, EOD_CLOSE_TIME

assert ENTRY_WINDOW_START_ET == "09:25"
assert ENTRY_WINDOW_END_ET   == "15:50"
assert EOD_CLOSE_TIME        == "15:50"
assert ENTRY_WINDOW_END_ET  <= EOD_CLOSE_TIME, "entries must never still be open once the EOD close sweep starts"

ET = pytz.timezone("America/New_York")


def _at(h: int, m: int) -> datetime.datetime:
    return ET.localize(datetime.datetime(2026, 8, 14, h, m, 0))


# Inside the window.
assert _within_entry_window(_at(9, 25)) is True, "exactly at open boundary -> inside (inclusive)"
assert _within_entry_window(_at(12, 0)) is True
assert _within_entry_window(_at(15, 50)) is True, "exactly at close boundary -> inside (inclusive)"

# Outside the window.
assert _within_entry_window(_at(9, 24)) is False, "one minute before open -> outside"
assert _within_entry_window(_at(15, 51)) is False, "one minute after close -> outside"
assert _within_entry_window(_at(15, 55)) is False, "AXTI incident time -> must stay outside"
assert _within_entry_window(_at(16, 0)) is False, "old 16:00 boundary -> now outside"
assert _within_entry_window(_at(7, 30)) is False, "old pre-market start -> now outside"
assert _within_entry_window(_at(3, 0)) is False, "overnight -> outside"
assert _within_entry_window(_at(23, 59)) is False, "late night -> outside"

print("OK: entry window is inclusive of both boundaries (09:25-15:50 ET), rejects everything outside it, and never extends past EOD_CLOSE_TIME")
