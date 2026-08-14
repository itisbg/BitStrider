"""Self-check for the entry-time window (2026-08-14, at the user's request:
"restrict trading to 7.30am ET to 8pm ET for buying"). New entries (either
direction) only submitted within [ENTRY_WINDOW_START_ET, ENTRY_WINDOW_END_ET].
Existing positions are unaffected -- every close_*/check_*_stop path runs
from the orchestrator main loop regardless of this window.

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
from engine.config import ENTRY_WINDOW_START_ET, ENTRY_WINDOW_END_ET

assert ENTRY_WINDOW_START_ET == "07:30"
assert ENTRY_WINDOW_END_ET   == "20:00"

ET = pytz.timezone("America/New_York")


def _at(h: int, m: int) -> datetime.datetime:
    return ET.localize(datetime.datetime(2026, 8, 14, h, m, 0))


# Inside the window.
assert _within_entry_window(_at(7, 30)) is True, "exactly at open boundary -> inside (inclusive)"
assert _within_entry_window(_at(12, 0)) is True
assert _within_entry_window(_at(20, 0)) is True, "exactly at close boundary -> inside (inclusive)"

# Outside the window.
assert _within_entry_window(_at(7, 29)) is False, "one minute before open -> outside"
assert _within_entry_window(_at(20, 1)) is False, "one minute after close -> outside"
assert _within_entry_window(_at(3, 0)) is False, "overnight -> outside"
assert _within_entry_window(_at(23, 59)) is False, "late night -> outside"

print("OK: entry window is inclusive of both boundaries (07:30-20:00 ET), rejects everything outside it")
