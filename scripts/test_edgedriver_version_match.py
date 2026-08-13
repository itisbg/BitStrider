"""Self-check for the msedgedriver version-matching fix (2026-08-13).

Edge auto-updated in the background (151.0.4129.59 -> .78) but the cached
driver picked by _find_existing_edgedriver() was chosen by file mtime only,
with no version check -- so it kept handing back the now-incompatible .59
driver forever. Every scrape cycle then failed with "session not created:
Chrome instance exited" (the classic driver/browser version-mismatch
symptom), confirmed live in ti_capture_scheduler.log every 10 min.

Run with:
  python scripts/test_edgedriver_version_match.py
Creates a few real temp files (mtime-based selection needs real files) --
no network calls, no real Edge/webdriver launched.
"""
import sys
import os
import tempfile
import time
from pathlib import Path
ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from engine.ti.capture_tradeideas import _select_cached_driver

with tempfile.TemporaryDirectory() as tmp:
    old_dir = os.path.join(tmp, "151.0.4129.59")
    new_dir = os.path.join(tmp, "151.0.4129.78")
    os.makedirs(old_dir)
    os.makedirs(new_dir)
    old_driver = os.path.join(old_dir, "msedgedriver.exe")
    new_driver = os.path.join(new_dir, "msedgedriver.exe")
    Path(old_driver).write_text("fake")
    time.sleep(0.05)
    Path(new_driver).write_text("fake")  # newer mtime than old_driver

    candidates = [old_driver, new_driver]

    # Installed Edge is .78, but the newest-by-mtime candidate is ALSO .78
    # here (matches) -> picks it, same as before the fix in the easy case.
    assert _select_cached_driver(candidates, "151.0.4129.78") == new_driver

    # Installed Edge is .59 (older), only the OLDER file matches by version --
    # must not just grab the newest-mtime one blindly.
    assert _select_cached_driver(candidates, "151.0.4129.59") == old_driver

    # The actual bug: Edge auto-updated to a version with NO cached driver at
    # all -- must return None (forces the network-install fallback), not
    # silently hand back a guaranteed-incompatible driver.
    assert _select_cached_driver(candidates, "151.0.4129.99") is None

    # Version couldn't be determined -- best-effort fallback to newest mtime,
    # same as the pre-fix behavior, rather than blocking entirely.
    assert _select_cached_driver(candidates, None) == new_driver

    # No cached candidates at all -> None regardless of version.
    assert _select_cached_driver([], "151.0.4129.78") is None

print("OK: cached msedgedriver selection matches the installed Edge version, never silently reuses a stale one")
