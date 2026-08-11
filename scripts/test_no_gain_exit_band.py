"""Self-check for the no-gain-exit band change (2026-08-11).

Was: exit only if held >= 24h AND gain <= 0% -- no downside cutoff at all,
so a stale position could sit arbitrarily negative and this rule would
never touch it (only the ~8% trailing stop eventually caught a real
decline).

Now, at the user's request: held >= 8h (was 24h), and exit on EITHER a
positive gain (stop waiting once it's decided which way it's going) OR a
drop of NO_GAIN_EXIT_MAX_LOSS_PCT (-1.5%) or worse (cut it well before the
full trailing stop would). Only a narrow flat/small-loss band --
(-1.5%, 0%] -- still survives the check and keeps holding.

Run with:
  python scripts/test_no_gain_exit_band.py
No network calls -- client and _submit_closing_order are stubbed.
"""
import sys
import datetime
from pathlib import Path
from types import SimpleNamespace
ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from engine.execution.enhanced import EnhancedExecutor
from engine.config import NO_GAIN_EXIT_HOURS, NO_GAIN_EXIT_MIN_PCT, NO_GAIN_EXIT_MAX_LOSS_PCT

assert NO_GAIN_EXIT_HOURS == 8, f"expected 8h threshold, config has {NO_GAIN_EXIT_HOURS}"
assert NO_GAIN_EXIT_MIN_PCT == 0.0, f"expected 0.0% ceiling, config has {NO_GAIN_EXIT_MIN_PCT}"
assert NO_GAIN_EXIT_MAX_LOSS_PCT == -1.5, f"expected -1.5% cutoff, config has {NO_GAIN_EXIT_MAX_LOSS_PCT}"


class _FakeClient:
    def __init__(self, pos):
        self._pos = pos
    def get_all_positions(self):
        return [self._pos]
    def get_orders(self, filter=None):
        return []  # no resting orders -- goes straight to the close attempt


def _closed(held_hours: float, gain_pct: float) -> bool:
    """Run the real close_no_gain_positions() and report whether it closed
    the one fake position."""
    ex = EnhancedExecutor.__new__(EnhancedExecutor)  # skip __init__ (no broker creds needed)
    now = datetime.datetime.now(datetime.timezone.utc)
    entry_dt = now - datetime.timedelta(hours=held_hours)
    pos = SimpleNamespace(symbol="TEST", qty=10, unrealized_plpc=gain_pct / 100,
                           unrealized_pl=1.23, current_price=10.0)
    ex.client = _FakeClient(pos)
    ex._entry_log = {"TEST": {"filled_at": entry_dt, "strategy": "TestStrat"}}
    ex._no_gain_chase_count = {}
    ex._submit_closing_order = lambda *a, **k: None  # stub -- don't hit the broker
    result = ex.close_no_gain_positions()
    return result["closed_count"] == 1


cases = [
    (7.9,  -5.0, False, "under the 8h threshold — never checked, however bad the P&L"),
    (8.1,   0.0, False, "flat at 8h+ — still in the (-1.5%, 0%] band, keeps holding"),
    (8.1,  -1.0, False, "small loss at 8h+ — still in the band, keeps holding"),
    (8.1,  -1.5, True,  "exactly at the -1.5% cutoff — exits"),
    (8.1,  -2.0, True,  "past the -1.5% cutoff — exits"),
    (8.1,  +2.0, True,  "positive at 8h+ — exits, doesn't wait for more"),
]

for held_hours, gain_pct, expect_closed, label in cases:
    got = _closed(held_hours, gain_pct)
    assert got == expect_closed, (
        f"held={held_hours}h gain={gain_pct}% -> closed={got}, expected {expect_closed} ({label})"
    )

print("OK: no-gain-exit 8h/-1.5% band behaves correctly")
