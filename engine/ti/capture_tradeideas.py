"""
Trade Ideas — Screenshot + Universe Updater
============================================
Navigates to three Trade Ideas TIPro scan pages with Selenium Chrome,
captures screenshots, extracts ticker symbols, and optionally persists
results into data/universe.json so the universe is kept current.

Pages scraped
-------------
  HIGH_SHORT_FLOAT      https://www.trade-ideas.com/TIPro/highshortfloat/
  MARKET_SCOPE_360      https://www.trade-ideas.com/TIPro/marketscope360/
  UNUSUAL_OPTIONS_VOL   https://www.trade-ideas.com/TIPro/unusualoptionsvolume/
                        Unusual-options-volume tickers → tier 1 (directional conviction)

Usage
-----
  # Single run — screenshot + show extracted tickers
  python scripts/capture_tradeideas.py

  # Single run AND persist results to data/universe.json
  python scripts/capture_tradeideas.py --update-config

  # Loop every 5 minutes AND persist results to data/universe.json
  python scripts/capture_tradeideas.py --loop 300 --update-config

  # Use your existing Chrome profile (already logged in to Trade-Ideas)
  python scripts/capture_tradeideas.py --chrome-profile "Default" --update-config

Requirements
------------
  pip install selenium webdriver-manager pillow
"""

from __future__ import annotations

import argparse
import logging
import re
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

# ── optional PIL for timestamp overlay ──────────────────────────
try:
    from PIL import Image, ImageDraw
    PIL_OK = True
except ImportError:
    PIL_OK = False

# ── Selenium ─────────────────────────────────────────────────────
try:
    from selenium import webdriver
    from selenium.webdriver.edge.options import Options as EdgeOptions
    from selenium.webdriver.edge.service import Service as EdgeService
    from selenium.webdriver.common.action_chains import ActionChains
    from selenium.webdriver.common.by import By
    from selenium.webdriver.common.keys import Keys
    from selenium.webdriver.support.ui import WebDriverWait
    from selenium.webdriver.support import expected_conditions as EC
    from selenium.common.exceptions import SessionNotCreatedException, TimeoutException
    from webdriver_manager.microsoft import EdgeChromiumDriverManager
    SELENIUM_OK = True
except ImportError:
    SELENIUM_OK = False

# ── Paths ────────────────────────────────────────────────────────
SCRIPT_DIR = Path(__file__).parent
REPO_ROOT   = SCRIPT_DIR.parent.parent
# Running this file directly (`python engine/ti/capture_tradeideas.py`) only
# puts SCRIPT_DIR on sys.path, not REPO_ROOT, so the `engine.*` imports used
# below (_is_valid_ti_ticker, _patch_config, ...) fail with "No module named
# 'engine'" even though the scrape itself succeeds. Fix at the source instead
# of re-inserting REPO_ROOT before every import site.
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))
OUTPUT_DIR  = REPO_ROOT / "screenshots"
CONFIG_FILE = REPO_ROOT / "engine" / "config.py"
TI_UNUSUAL_OPTIONS_FILE = REPO_ROOT / "data" / "ti_unusual_options.json"
TI_PRIMARY_FILE = REPO_ROOT / "data" / "ti_primary.json"

# ── TI login credentials (optional, for auto-login) ────────────────
# 2026-08-24, user request: this script always ran on whatever session was
# already sitting in the Edge profile -- if that session ever expired there
# was no recovery, just a near-empty scrape (see MIN_VALID_TICKERS below)
# until someone noticed and logged back in by hand. Loaded here (not via
# engine.config) because this script also runs standalone via Task Scheduler
# (run_ti_capture_task.ps1), outside main.py's own load_dotenv() call.
import os as _os
try:
    from dotenv import load_dotenv as _load_dotenv
    _load_dotenv(REPO_ROOT / ".env")
except ImportError:
    pass
TI_EMAIL    = _os.environ.get("TI_LOGIN", "")
TI_PASSWORD = _os.environ.get("TI_PASSWORD", "")

# ── Trade Ideas scan URLs ────────────────────────────────────────
SCANS: dict[str, dict] = {
    "highshortfloat": {
        "url":    "https://www.trade-ideas.com/TIPro/highshortfloat/",
        "label":  "high_short_float",
        "target": "PRIORITY_2_ESTABLISHED",   # squeeze / short-float candidates
    },
    "marketscope360": {
        "url":    "https://www.trade-ideas.com/TIPro/marketscope360/",
        "label":  "market_scope_360",
        "target": "PRIORITY_1_MOMENTUM",      # momentum leaders
    },
    "unusualoptionsvolume": {
        "url":    "https://www.trade-ideas.com/TIPro/unusualoptionsvolume/",
        "label":  "unusual_options_volume",
        "target": "PRIORITY_1_MOMENTUM",   # directional-conviction tickers → tier 1
    },
    "toplists": {
        "url":    "https://www.trade-ideas.com/TIPro/toplists/",
        "label":  "toplists",
        "target": "PRIORITY_1_MOMENTUM",   # Explore Stock Groups top list tickers
    },
}

# Words to exclude from ticker extraction (common UI/nav/HTML words)
_IGNORE = {
    "A", "AN", "AND", "OR", "NOT", "THE", "FOR", "ALL", "NEW", "NO", "PM", "AM",
    "NA", "GO", "BE", "IN", "ON", "TO", "AT", "BY", "IF", "IS", "IT", "AS", "OF",
    "MY", "US", "UP", "DO", "SO", "ME", "HE", "WE", "VS",
    # UI / nav words visible on Trade Ideas pages
    "MIN", "PRE", "POST", "EST", "USD", "ETF", "ETH", "BTC",
    "HIGH", "LOW", "BUY", "SELL", "OPEN", "CLOSE", "MARKET", "PRICE",
    "FLOAT", "SHORT", "CHANGE", "VOLUME", "SCAN", "TRADE", "IDEAS", "SCOPE",
    "MON", "TUE", "WED", "THU", "FRI", "SAT", "SUN",
    "JAN", "FEB", "MAR", "APR", "MAY", "JUN", "JUL", "AUG", "SEP", "OCT", "NOV", "DEC",
    "NAS", "DOW", "EPS", "RSI", "SMA", "EMA", "ATR", "ADX", "MACD",
    "HOLLY", "PRO", "MY", "COPY", "WAVE", "DEEP", "DIVE", "PLAY",
    "TGT",
    "UNUSUAL", "OPTIONS", "SECTORS", "EXPLORE", "GROUPS", "TRADING",
    "COMPETITION", "WATCHLISTS", "SETTINGS", "DASHBOARDS", "CHANNELS",
    "MOMENTUM", "WAVES", "STOCK", "SCOPE", "BIGGEST", "GAINERS", "LOSERS",
    "DELAYED", "LIVE", "ALERT", "ALERTS", "FILTER", "FILTERS",
    # unusualoptionsvolume UI words
    "CALL", "PUT", "CALLS", "PUTS", "SWEEP", "SWEEP", "FLOW", "FLOWS",
    "STRIKE", "EXPIRY", "EXPIRATION", "PREMIUM", "CONTRACT", "CONTRACTS",
    "OI", "BULLISH", "BEARISH", "NEUTRAL",
    "RACE", "CENTRAL", "LEADER", "LEADERS", "LAGGARD", "LAGGARDS",
    "WINNER", "WINNERS", "LOSER", "LOSERS", "RANK", "RANKED",
    # Index / non-tradeable symbols seen in TI page text
    "DJI", "NASD", "ADR", "TX", "TI", "LLC", "SWING", "SMART",
    "SP", "NDX", "RUT", "VIX", "DJIA",
}

_TICKER_RE = re.compile(r'\b([A-Z]{2,5})\b')

# ── Secondary blocklist used when applying scraped tickers to the live universe ──
# Words that survive the broad regex above but are never real tradeable tickers.
_TI_SCRAPE_GARBAGE: set[str] = {
    "TI", "NASD", "SWING", "SMART", "CBD", "LLC", "DJI", "SPY", "ARTL",  # known artifacts
    "BUY", "SELL", "SHORT", "LONG", "ALL", "NEW", "TOP", "HOT",            # action words
    "NYSE", "AMEX", "OTC", "ETF", "ADR",                                   # exchange/type labels
    "HIGH", "LOW", "OPEN", "CLOSE", "VOL", "RVOL", "FLOAT",               # column headers
    "BF", "NOTE",                                                          # feeds with no data
    "AI", "CA", "AZ", "CO",                                                # state/generic abbrevs
    "SPDR", "SSGA", "IVV", "VOO", "VTI",                                  # ETF brand names / broad index ETFs
}


def _is_valid_ti_ticker(sym: str) -> bool:
    """Return False for obvious scraper garbage: too short, too long, non-alpha, or block-listed."""
    if not sym or not isinstance(sym, str):
        return False
    s = sym.strip().upper()
    if not s:
        return False
    # Must be 1–5 uppercase letters (optionally ending in one digit for share classes)
    if not re.fullmatch(r"[A-Z]{1,5}[0-9]?", s):
        return False
    if s in _IGNORE or s in _TI_SCRAPE_GARBAGE:
        return False
    from engine.never_trade import is_never_trade  # noqa: E402
    if is_never_trade(s):
        return False
    return True


# How long to wait (seconds) for page to render
TABLE_WAIT_SEC = 20
PAGE_LOAD_SEC  = 15
RENDER_GRACE_SEC = 2
DROPDOWN_REFRESH_SEC = 2
LOGIN_WAIT_SEC = 15  # how long to wait for the password field to clear after submitting auto-login


# ── Persistent Edge driver singleton ─────────────────────────────
# The Edge window stays open across scrape cycles so the TI login session is
# preserved. A new window is only created if the driver is dead/missing.
_edge_driver: Optional["webdriver.Edge"] = None


def _installed_edge_version() -> Optional[str]:
    """Full version string (e.g. '151.0.4129.78') of the installed Edge, or
    None if it can't be determined. See _find_existing_edgedriver below for
    why this matters.

    2026-08-13: `msedge.exe --version` looked like the obvious way to get
    this and is what a first pass used -- confirmed live it's NOT reliable:
    Edge is a single-instance app, so a `--version` invocation from a fresh
    process can hand off to an already-running instance over IPC and exit
    rc=0 with EMPTY stdout instead of printing anything, silently returning
    None every time and making the whole version-match check a no-op.
    Reading the versioned install subdirectory Edge creates next to
    msedge.exe (Application/<version>/, e.g. Application/151.0.4129.78/)
    needs no subprocess at all and isn't subject to that IPC handoff."""
    import re, os
    for env_var in ("ProgramFiles(x86)", "ProgramFiles"):
        base = os.environ.get(env_var)
        if not base:
            continue
        app_dir = os.path.join(base, "Microsoft", "Edge", "Application")
        if not os.path.isdir(app_dir):
            continue
        versions = [d for d in os.listdir(app_dir) if re.fullmatch(r"\d+\.\d+\.\d+\.\d+", d)]
        if versions:
            return max(versions, key=lambda v: tuple(int(p) for p in v.split(".")))
    return None


def _find_existing_edgedriver() -> Optional[str]:
    """Locate msedgedriver.exe — checks repo .drivers/ first, then ~/.wdm cache."""
    import glob, os

    # 1) Repo-local driver (committed or manually placed) — highest priority
    repo_driver = REPO_ROOT / ".drivers" / "msedgedriver.exe"
    if repo_driver.is_file():
        return str(repo_driver)

    # 2) webdriver_manager cache. The installed folder is "edgedriver" (confirmed
    # 2026-08-06: 5 valid cached versions sitting there, up to 151.0.4129.59) —
    # this used to look under "msedgedriver" instead, a name that has never
    # existed, so this cache-hit path never fired even once. Every call fell
    # through to EdgeChromiumDriverManager().install(), which hits the network
    # to re-verify/re-fetch the driver on every single cycle and every retry —
    # exactly the kind of thing that's fine when the network cooperates and
    # hangs or fails when it doesn't. Check both names in case a different
    # webdriver_manager version ever reverts to the old one.
    candidates = []
    for folder in ("edgedriver", "msedgedriver"):
        wdm_root = os.path.expandvars(rf"%USERPROFILE%\.wdm\drivers\{folder}")
        for pattern in (
            os.path.join(wdm_root, "**", "msedgedriver.exe"),
            os.path.join(wdm_root, "**", "win64", "msedgedriver.exe"),
            os.path.join(wdm_root, "**", "win32", "msedgedriver.exe"),
        ):
            candidates.extend(glob.glob(pattern, recursive=True))
    candidates = [c for c in candidates if os.path.isfile(c)]
    if not candidates:
        return None

    # 2026-08-13: picking the newest-by-mtime candidate silently kept handing
    # back a driver for an Edge version that no longer exists once Edge
    # auto-updates in the background (confirmed live: browser auto-updated
    # 151.0.4129.59 -> .78, cache still only had .59) -- every launch then
    # failed with "session not created: Chrome instance exited", the classic
    # driver/browser version-mismatch symptom, in a loop with nothing to
    # break it since the stale driver just kept winning the mtime race
    # forever. Filter to drivers whose cache path actually matches the
    # currently-installed Edge version; if none match, return None so the
    # caller falls through to EdgeChromiumDriverManager().install(), which
    # fetches (and re-caches) a version-correct driver over the network.
    return _select_cached_driver(candidates, _installed_edge_version())


def _select_cached_driver(candidates: list, installed_version: Optional[str]) -> Optional[str]:
    """Pure selection logic split out of _find_existing_edgedriver for
    testability: given real cached-driver file paths and the currently-
    installed Edge version (or None if unknown), pick which cached driver
    (if any) is safe to reuse."""
    import os
    if not candidates:
        return None
    if installed_version:
        matching = [c for c in candidates if installed_version in c]
        if matching:
            return max(matching, key=lambda p: os.path.getmtime(p))
        return None  # every cached driver is for a stale Edge version
    return max(candidates, key=lambda p: os.path.getmtime(p))  # version unknown — best-effort fallback


def _is_driver_alive(driver: "webdriver.Edge") -> bool:
    """Return True if the Edge WebDriver session is still responsive."""
    try:
        _ = driver.title   # any property access pings the driver
        return True
    except Exception:
        return False


# Default CDP remote debugging port — Edge will listen here so the next
# script run can re-attach without needing a new login.
_REMOTE_DEBUG_PORT = 9222


def _try_attach_edge(port: int) -> Optional["webdriver.Edge"]:
    """
    Try to attach to an already-running Edge instance that was started with
    --remote-debugging-port=<port>.  Returns the driver if successful, or
    None if no Edge is listening on that port.
    """
    try:
        opts = EdgeOptions()
        opts.add_experimental_option("debuggerAddress", f"localhost:{port}")
        existing = _find_existing_edgedriver()
        if existing:
            service = EdgeService(existing)
        else:
            service = EdgeService(EdgeChromiumDriverManager().install())
        import subprocess as _sp2, sys as _sys2
        if _sys2.platform == "win32":
            service.creation_flags = _sp2.CREATE_NO_WINDOW
        driver = webdriver.Edge(service=service, options=opts)
        _ = driver.title   # verify connection is live
        print(f"[INFO ] Re-attached to existing Edge session on port {port}.")
        return driver
    except Exception as _e:
        print(f"[INFO ] No live Edge on port {port} ({type(_e).__name__}: {_e}) — will open a new window.")
        return None


def _iter_own_automation_edge(chrome_profile: Optional[str]):
    """Yield psutil.Process objects for our own automation's main msedge.exe
    (never its renderer/GPU/utility children, never a window the user opened
    by hand). Identified by Selenium's own '--test-type=webdriver' marker
    plus the *absence* of '--type=' — shared by _kill_orphaned_automation_edge()
    and _automation_edge_present() so both scan the same way.
    """
    try:
        import psutil
    except ImportError:
        return
    # Every automation launch now uses --profile-directory=Default under the
    # dedicated _automation_user_data_dir() (2026-08-06 fix for Chromium's
    # "non-default data directory" DevTools restriction) — matching on
    # --profile-directory={chrome_profile} here stopped working the moment
    # that landed, since the real cmdline never contains "TIAutomation"
    # anymore. That silently broke zombie detection: _get_driver()'s retry
    # loop saw nothing "of ours" to kill and gave up after one attempt,
    # leaving every failed window stuck forever colliding with the next
    # attempt (confirmed 2026-08-06: exactly this). Match on the dedicated
    # user-data-dir instead — unique to our own automation regardless of the
    # chrome_profile label.
    # psutil's cmdline() is argv-parsed (quotes stripped), unlike the raw
    # WMI/CreateProcess string — match the bare path, not a quoted one.
    marker = f"--user-data-dir={_automation_user_data_dir(chrome_profile)}" if chrome_profile else None
    for proc in psutil.process_iter(["pid", "name", "cmdline"]):
        try:
            if proc.info["name"] != "msedge.exe":
                continue
            cmdline = proc.info["cmdline"] or []
            joined = " ".join(cmdline)
            if "--test-type=webdriver" not in joined or "--type=" in joined:
                continue  # not our automation's main process
            if marker and marker not in joined:
                continue
            yield proc
        except Exception:
            continue


def _automation_edge_present(chrome_profile: Optional[str]) -> bool:
    """True if our own automation Edge is currently running for this profile.

    _get_driver()'s CDP-reattach step used to run unconditionally on every
    cycle "in case a browser is still alive" — but every normal cycle ends
    with the browser fully quit(), so 100% of the time there is nothing to
    attach to, and the attempt still pays the cost of spawning its own
    msedgedriver.exe service process to find that out. Whether Selenium
    cleanly tears that down on a failed connection isn't guaranteed, making
    this a plausible slow leak across many cycles (2026-08-06). Check first
    with a cheap local process scan — no service spawn, no network/CDP call —
    so reattach is only ever attempted when there's real reason to.
    """
    return next(_iter_own_automation_edge(chrome_profile), None) is not None


def _kill_orphaned_automation_edge(chrome_profile: Optional[str]) -> bool:
    """Kill our own previously-launched Edge if it's still holding the profile
    lock but no longer answering on the CDP port (e.g. wedged after the
    machine slept). See _iter_own_automation_edge() for how "ours" is
    identified — this can never touch a window the user opened by hand,
    only our own zombie.
    """
    killed = False
    for proc in _iter_own_automation_edge(chrome_profile):
        try:
            print(f"[WARN ] Killing unresponsive automation Edge (pid {proc.pid}) that's holding the profile lock")
            for child in proc.children(recursive=True):
                try:
                    child.kill()
                except Exception:
                    pass
            proc.kill()
            killed = True
        except Exception:
            continue
    return killed


def _automation_user_data_dir(chrome_profile: str) -> str:
    """Return a dedicated, non-default --user-data-dir for the automation Edge.

    Chromium now refuses to enable remote debugging (which Selenium/msedgedriver
    requires) when --user-data-dir is the browser's real default profile
    location — confirmed 2026-08-06 via msedgedriver's own verbose log:
    "DevTools remote debugging requires a non-default data directory." The
    browser launches and even loads pages, but no DevTools port ever opens,
    so msedgedriver times out and reports "session not created"/"Chrome
    instance exited" — this was the actual root cause of every scrape failure
    today, not a profile lock or slow extensions (those were red herrings).

    One-time migration: if this dedicated dir doesn't exist yet, seed it by
    copying the old default-location profile folder (chrome_profile) so the
    existing Trade Ideas login/cookies carry over — no re-login needed.
    """
    import os, shutil

    new_root = Path(os.path.expandvars(r"%LOCALAPPDATA%\TI_Automation\EdgeUserData"))
    new_profile = new_root / "Default"
    if not new_profile.is_dir():
        old_profile = Path(os.path.expandvars(r"%LOCALAPPDATA%\Microsoft\Edge\User Data")) / chrome_profile
        if old_profile.is_dir():
            print(f"[INFO ] Seeding dedicated automation profile from '{chrome_profile}' (one-time, preserves TI login)")
            shutil.copytree(old_profile, new_profile)
        else:
            new_profile.mkdir(parents=True, exist_ok=True)
    return str(new_root)


def _create_edge_driver(chrome_profile: Optional[str] = None, remote_debug_port: int = 0) -> "webdriver.Edge":
    """Spawn a new visible Edge window and return the driver (never headless)."""
    import os, subprocess as _sp, sys as _sys

    os.environ.setdefault("WDM_LOG", "0")
    os.environ.setdefault("WDM_LOG_LEVEL", "0")
    logging.getLogger("WDM").setLevel(logging.ERROR)
    logging.getLogger("webdriver_manager").setLevel(logging.ERROR)

    opts = EdgeOptions()
    opts.add_argument("--start-maximized")
    opts.add_argument("--no-sandbox")
    opts.add_argument("--disable-dev-shm-usage")
    opts.add_argument("--disable-blink-features=AutomationControlled")
    # Chromium 111+ added an origin check on DevTools connections that
    # silently refuses the driver's handshake without this — a well-known
    # cause of exactly "session not created"/"chrome not reachable" even
    # though the browser itself launches and runs fine (2026-08-06).
    opts.add_argument("--remote-allow-origins=*")
    opts.add_argument("--log-level=3")
    opts.add_argument("--disable-logging")
    opts.add_argument("--silent")
    opts.add_experimental_option("excludeSwitches", ["enable-automation"])
    opts.add_experimental_option("useAutomationExtension", False)
    # Start on a blank page, not this profile's default New Tab Page. A
    # fresh profile's NTP is the full MSN/Bing-powered one — several
    # external image/widget fetches, some still loading many seconds in
    # (confirmed via msedgedriver's own verbose log, 2026-08-06). That's
    # slower to "settle" than msedgedriver's internal session-ready wait
    # tolerates: the browser was still visibly alive and rendering when
    # msedgedriver gave up, killed it, and reported the result as "Chrome
    # instance exited" — the real first scan page navigation happens moments
    # later via driver.get() anyway, so nothing here needs the NTP at all.
    opts.add_argument("about:blank")
    # No "detach" option: scrape_tradeideas() explicitly quits this window at
    # the end of every cycle now, so nothing should be left dangling for
    # Selenium to detach from in the first place.

    # Do NOT pass --remote-debugging-port as a raw Chromium argument here.
    # msedgedriver manages its own internal debug port automatically when it
    # launches a fresh browser, and manually forcing this flag on top of that
    # conflicts with its own handshake — confirmed 2026-08-06 via
    # msedgedriver's own verbose log: the browser launches and runs fine, but
    # nothing ever ends up listening on the forced port, so msedgedriver
    # polls "http://localhost:{port}/json/version" every ~2s forever and
    # eventually gives up with "chrome not reachable". This was the actual
    # root cause of nearly every scrape failure today, not a profile lock —
    # this flag is only correct for _try_attach_edge()'s attach-to-an-
    # externally-launched-browser case, which uses Selenium's debuggerAddress
    # option instead, a different (and correct) mechanism.

    if chrome_profile:
        opts.add_argument(f"--user-data-dir={_automation_user_data_dir(chrome_profile)}")
        opts.add_argument("--profile-directory=Default")

    existing = _find_existing_edgedriver()
    if existing:
        print(f"[INFO ] Using cached msedgedriver: {existing}")
        service = EdgeService(existing)
    else:
        service = EdgeService(EdgeChromiumDriverManager().install())

    if _sys.platform == "win32":
        service.creation_flags = _sp.CREATE_NO_WINDOW

    try:
        driver = webdriver.Edge(service=service, options=opts)
    except SessionNotCreatedException as e:
        if chrome_profile:
            # The generic "locked/busy" framing was accurate for the original
            # profile-lock bug, but this except branch fires for *any*
            # SessionNotCreatedException — masking the real reason if the
            # cause has since changed (2026-08-06: verified true even after
            # the driver-cache and retry fixes, still failing consistently
            # with this same message, and the actual exception text was
            # never being surfaced anywhere to check). Always show it.
            _first_line = str(e).splitlines()[0] if str(e) else repr(e)
            raise RuntimeError(
                f"Edge profile '{chrome_profile}' is locked/busy — close Edge first. "
                f"[{_first_line}]"
            ) from e
        raise e

    driver.set_page_load_timeout(45)
    driver.execute_script("Object.defineProperty(navigator, 'webdriver', {get: () => undefined})")
    print("[INFO ] Edge browser opened. Stays open across scrape cycles within this process.")
    return driver


_DRIVER_CREATE_TIMEOUT_SEC = 30


def _create_edge_driver_with_timeout(
    chrome_profile: Optional[str] = None, remote_debug_port: int = 0
) -> "webdriver.Edge":
    """Run _create_edge_driver() with a hard timeout.

    The underlying webdriver.Edge(...) constructor has no timeout of its own
    and can hang indefinitely if the msedgedriver<->Edge handshake stalls
    (confirmed 2026-08-06: near-zero CPU for 3+ minutes, needed a human to
    close the browser). Nothing in the normal retry path can rescue that
    hang — it happens before scrape_tradeideas()'s own try/finally cleanup
    even starts, so the browser never gets closed for the next run. Treat a
    timeout the same as "locked/busy": kill whatever got spawned and raise,
    so _get_driver()'s existing kill-and-retry-once path also covers this.
    """
    import threading as _th

    result: dict = {}

    def _run() -> None:
        try:
            result["driver"] = _create_edge_driver(
                chrome_profile=chrome_profile, remote_debug_port=remote_debug_port
            )
        except Exception as exc:
            result["error"] = exc

    t = _th.Thread(target=_run, daemon=True)
    t.start()
    t.join(_DRIVER_CREATE_TIMEOUT_SEC)

    if t.is_alive():
        print(f"[WARN ] Edge driver creation hard-timeout ({_DRIVER_CREATE_TIMEOUT_SEC}s) — clearing and retrying")
        # _kill_orphaned_automation_edge() only targets msedge.exe — it has to
        # stay that narrow since a real msedge.exe could be the user's own
        # window. msedgedriver.exe is different: it's never anything but our
        # own automation, so it's always safe to clear here. This matters
        # because it's msedgedriver — not the browser — that the still-live
        # background thread above is actually blocked waiting on; killing
        # only the browser leaves that thread hung forever, piling up one
        # more leaked, still-running thread on every retry (confirmed
        # 2026-08-06: exactly this, needed a human to end the process).
        try:
            import psutil
            for proc in psutil.process_iter(["pid", "name"]):
                if proc.info.get("name") == "msedgedriver.exe":
                    try:
                        proc.kill()
                    except Exception:
                        pass
        except ImportError:
            pass
        _kill_orphaned_automation_edge(chrome_profile)
        raise RuntimeError(
            f"Edge profile '{chrome_profile}' is locked/busy — close Edge first."
            if chrome_profile else "Edge driver creation timed out"
        )

    if "error" in result:
        raise result["error"]
    return result["driver"]


def _get_driver(
    chrome_profile: Optional[str] = None,
    remote_debug_port: int = _REMOTE_DEBUG_PORT,
) -> "webdriver.Edge":
    """
    Return the persistent Edge driver.
    1. If already alive in-process → reuse it.
    2. Else try to re-attach to an existing Edge on *remote_debug_port*.
    3. Else spawn a new Edge window (with remote debugging enabled so it can
       be re-attached on the next script run without a fresh login).
    """
    global _edge_driver
    if _edge_driver is not None and _is_driver_alive(_edge_driver):
        return _edge_driver

    if _edge_driver is not None:
        print("[WARN ] Edge session lost — attempting re-attach before reopening.")

    # Step 2: try CDP re-attach, with a couple of retries — but only if a
    # matching automation Edge is actually running. Every normal cycle ends
    # with the browser fully quit(), so unconditionally attempting this
    # every cycle (as before) mostly just spawns and abandons a throwaway
    # msedgedriver.exe service process to learn there's nothing to attach to.
    # Right after the machine wakes from sleep, though, the old window can
    # genuinely still be alive — worth waiting for in that real case, rather
    # than racing it into Step 3's fresh-launch and colliding over the
    # profile lock.
    if remote_debug_port > 0 and _automation_edge_present(chrome_profile):
        for attempt in range(3):
            _edge_driver = _try_attach_edge(remote_debug_port)
            if _edge_driver is not None:
                break
            if attempt < 2:
                time.sleep(5)

    # Step 3: open a fresh Edge window. If a previous automation window is
    # wedged (dead on CDP but still holding the --profile-directory lock),
    # clear it out first — it can never be the user's own Edge, see
    # _kill_orphaned_automation_edge's docstring — then retry.
    #
    # A single 3s-wait retry (the old behavior) wasn't enough: confirmed
    # 2026-08-06 that a killed process's own just-spawned browser can become
    # the *next* attempt's blocker in a fast, tight loop — proc.kill() doesn't
    # guarantee Windows finishes releasing the profile's lock file within 3s,
    # especially under heavy load. A few attempts with a growing wait gives
    # the OS more room to actually finish tearing the old process down.
    if _edge_driver is None:
        _KILL_RETRY_WAITS = (3, 6, 10)
        last_exc: Optional[Exception] = None
        for wait_sec in _KILL_RETRY_WAITS:
            try:
                _edge_driver = _create_edge_driver_with_timeout(
                    chrome_profile=chrome_profile,
                    remote_debug_port=remote_debug_port,
                )
                last_exc = None
                break
            except RuntimeError as exc:
                if "locked/busy" not in str(exc):
                    raise
                last_exc = exc
                print(f"[WARN ] {exc} — checking for a wedged automation Edge to clear.")
                if not _kill_orphaned_automation_edge(chrome_profile):
                    break  # nothing of ours to clear — no point retrying
                time.sleep(wait_sec)
        if _edge_driver is None and last_exc is not None:
            raise last_exc

    return _edge_driver


# ── Ticker extraction ─────────────────────────────────────────────
def _extract_tickers(driver: "webdriver.Edge") -> list[str]:
    """
    Extract ticker symbols from the loaded Trade Ideas heatmap page.
    Primary: body.innerText scan (works for React/JS-rendered heatmaps).
    Fallback: href link pattern + data-symbol attributes.
    Returns a de-duped ordered list of up to 50 tickers.
    """
    found: list[str] = []

    # Strategy 1: body.innerText — most reliable for JS-rendered heatmap tiles
    try:
        body_text = driver.execute_script("return document.body.innerText;") or ""
        for m in _TICKER_RE.finditer(body_text):
            t = m.group(1)
            if t not in _IGNORE:
                found.append(t)
    except Exception:
        pass

    # Strategy 2: data-symbol / data-ticker / data-code attributes
    try:
        attrs = driver.execute_script("""
            var r = [];
            document.querySelectorAll('[data-symbol],[data-ticker],[data-code]').forEach(function(el){
                var v = el.getAttribute('data-symbol') || el.getAttribute('data-ticker') || el.getAttribute('data-code');
                if (v) r.push(v.toUpperCase().trim());
            });
            return r;
        """) or []
        for t in attrs:
            if _TICKER_RE.fullmatch(t) and t not in _IGNORE:
                found.append(t)
    except Exception:
        pass

    # Strategy 3: href links containing /stock/TICKER
    try:
        for anchor in driver.find_elements(By.TAG_NAME, "a"):
            href = anchor.get_attribute("href") or ""
            m = re.search(r'/stock/([A-Z]{1,5})(?:[/?]|$)', href)
            if m:
                candidate = m.group(1)
                if _TICKER_RE.fullmatch(candidate) and candidate not in _IGNORE:
                    found.append(candidate)
    except Exception:
        pass

    # De-dup preserving order, max 50
    seen: set[str] = set()
    clean: list[str] = []
    for t in found:
        if t not in seen and t not in _IGNORE:
            seen.add(t)
            clean.append(t)
    return clean[:50]


# ── Screenshot helper ─────────────────────────────────────────────
def _save_screenshot(driver: "webdriver.Chrome", label: str) -> Path:
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    ts       = datetime.now().strftime("%Y%m%d_%H%M%S")
    out_path = OUTPUT_DIR / f"tradeideas_{label}_{ts}.png"
    driver.save_screenshot(str(out_path))

    if PIL_OK:
        try:
            img  = Image.open(out_path)
            draw = ImageDraw.Draw(img)
            stamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S") + f"  |  {label}"
            draw.rectangle([(0, 0), (len(stamp) * 7 + 8, 18)], fill=(0, 0, 0, 200))
            draw.text((4, 2), stamp, fill=(255, 255, 255))
            img.save(out_path)
        except Exception:
            pass

    print(f"[OK   ] screenshot → {out_path}")
    return out_path


# ── Config patcher ────────────────────────────────────────────────
# Minimum number of valid tickers a scrape must return before we trust it.
# A login/redirect page produces very few tokens that pass validation; real
# scan pages return dozens.  Set to 5 as a conservative floor.
_MIN_SCRAPE_TICKERS = 5


def _patch_config(list_name: str, new_tickers: list[str]) -> int:
    """
    Add *new_tickers* to data/universe.json (TTL-managed) instead of patching
    config.py source code.  list_name determines the tier:
      PRIORITY_1_MOMENTUM   → tier 1 (TTL 14 days)
      PRIORITY_2_ESTABLISHED → tier 2 (TTL 30 days)
    Returns the number of *new* tickers inserted.

    Applies the full _is_valid_ti_ticker filter + a minimum-count guard so
    login-page scrapes (no TI session) don't pollute universe.json.
    """
    import sys as _sys
    _sys.path.insert(0, str(REPO_ROOT))
    from engine.equity.universe import add_tickers  # noqa: E402

    # Apply the same validation used in _apply_tradeideas_results so both write
    # paths (in-memory list and universe.json) are consistent.
    clean = [t for t in new_tickers if _is_valid_ti_ticker(t)]

    if len(clean) < _MIN_SCRAPE_TICKERS:
        print(
            f"[WARN ] _patch_config({list_name}): only {len(clean)} valid ticker(s) "
            f"after filtering (need ≥{_MIN_SCRAPE_TICKERS}) — skipping write to "
            f"universe.json (possible login-page scrape or empty scan)"
        )
        return 0

    tier = 1 if "PRIORITY_1" in list_name else 2
    added = add_tickers(clean, tier=tier)
    if added:
        print(f"[UNI  ] {added} new ticker(s) added to universe.json (tier {tier}): {clean[:5]}{'…' if len(clean)>5 else ''}")
    return added


# ── High-short-float set patcher ────────────────────────────────
def _patch_high_short_float(new_tickers: list[str]) -> int:
    """
    Merge *new_tickers* into the HIGH_SHORT_FLOAT_STOCKS set in config.py.
    Returns the number of tickers added.
    """
    src = CONFIG_FILE.read_text(encoding="utf-8")

    # Extract current set members
    m = re.search(
        r'HIGH_SHORT_FLOAT_STOCKS\s*=\s*\{([^}]*)\}',
        src, re.DOTALL
    )
    if not m:
        print("[WARN ] Could not locate HIGH_SHORT_FLOAT_STOCKS in config.py — skipping")
        return 0

    existing = set(re.findall(r'"([A-Z]{1,5})"', m.group(1)))
    to_add   = [t for t in new_tickers if t not in existing]
    if not to_add:
        return 0

    new_members = sorted(existing | set(to_add))
    # Rebuild the set block (up to 6 per line for readability)
    lines = []
    chunk = []
    for ticker in new_members:
        chunk.append(f'"{ticker}"')
        if len(chunk) == 6:
            lines.append("    " + ", ".join(chunk) + ",")
            chunk = []
    if chunk:
        lines.append("    " + ", ".join(chunk) + ",")
    new_block = "HIGH_SHORT_FLOAT_STOCKS  = {\n" + "\n".join(lines) + "\n}"

    new_src = re.sub(
        r'HIGH_SHORT_FLOAT_STOCKS\s*=\s*\{[^}]*\}',
        new_block,
        src,
        flags=re.DOTALL,
    )
    CONFIG_FILE.write_text(new_src, encoding="utf-8")
    return len(to_add)


# ── Dropdown helper ──────────────────────────────────────────────
def _try_select_timeframe(driver: "webdriver.Chrome", minutes: int) -> bool:
    """
    Attempt to select 'Change Last <minutes> Min (%)' from a page dropdown.
    Returns True if successful.
    """
    target_text = f"{minutes} Min"

    # Strategy 1: native <select>
    try:
        from selenium.webdriver.support.select import Select as SeleniumSelect
        for sel_el in driver.find_elements(By.TAG_NAME, "select"):
            for opt in sel_el.find_elements(By.TAG_NAME, "option"):
                if str(minutes) in opt.text and "min" in opt.text.lower():
                    SeleniumSelect(sel_el).select_by_visible_text(opt.text)
                    print(f"[OK   ] Dropdown selected (native <select>): {opt.text}")
                    return True
    except Exception:
        pass

    # Strategy 2: React custom dropdown — click trigger then option
    try:
        trigger = WebDriverWait(driver, 6).until(
            EC.element_to_be_clickable((By.XPATH,
                "//*[contains(@class,'select') or contains(@class,'Select')"
                " or contains(@class,'dropdown') or contains(@class,'Dropdown')]"
                "[contains(normalize-space(.),'Change') or contains(normalize-space(.),'%')]"
            ))
        )
        trigger.click()
        time.sleep(1.5)
        option = WebDriverWait(driver, 5).until(
            EC.element_to_be_clickable((By.XPATH,
                f"//*[contains(text(),'{target_text}') or contains(text(),'{target_text.lower()}')]"
            ))
        )
        print(f"[OK   ] Dropdown selected (React): {option.text}")
        option.click()
        return True
    except Exception:
        pass

    # Strategy 3: JS inject into any <select> with a matching option
    try:
        result = driver.execute_script(f"""
            var selects = document.querySelectorAll('select');
            for (var s of selects) {{
                for (var o of s.options) {{
                    if (o.text.includes('{minutes}') && o.text.toLowerCase().includes('min')) {{
                        s.value = o.value;
                        s.dispatchEvent(new Event('change', {{bubbles: true}}));
                        return o.text;
                    }}
                }}
            }}
            return null;
        """)
        if result:
            print(f"[OK   ] Dropdown selected (JS inject): {result}")
            return True
    except Exception:
        pass

    return False


def _try_select_30min(driver: "webdriver.Chrome") -> bool:
    return _try_select_timeframe(driver, 30)


def _try_select_15min(driver: "webdriver.Chrome") -> bool:
    return _try_select_timeframe(driver, 15)


def _scrape_toplists(
    driver: "webdriver.Chrome",
    select_minutes: Optional[int] = 15,
    update_config: bool = False,
) -> dict[str, list[str]]:
    """Scrape each toplist on the TIPro/toplists page and return their tickers."""
    results: dict[str, list[str]] = {}

    WebDriverWait(driver, TABLE_WAIT_SEC).until(
        EC.presence_of_element_located((By.CSS_SELECTOR, "#top-list-selector-card-div"))
    )

    if select_minutes is not None:
        found = _try_select_timeframe(driver, select_minutes)
        if not found:
            print(f"[WARN ] Could not find {select_minutes}-min dropdown — scraping current toplist view")
        else:
            time.sleep(DROPDOWN_REFRESH_SEC)

    items = driver.find_elements(By.CSS_SELECTOR, "div.top-list-setup-div")
    print(f"[INFO ] Found {len(items)} toplist items")

    for idx, item in enumerate(items, start=1):
        label = None
        try:
            label = item.find_element(By.TAG_NAME, "img").get_attribute("data-bs-original-title")
        except Exception:
            pass
        if not label:
            label = item.get_attribute("data") or f"toplist_{idx}"
        log_label = label.strip()[:60]
        print(f"[....] Selecting toplist {idx}/{len(items)}: {log_label}")

        try:
            target = driver.find_element(By.CSS_SELECTOR, "#toplist-card-div")
            driver.execute_script("arguments[0].scrollIntoView({block:'center', inline:'center'});", item)
            driver.execute_script("arguments[0].scrollIntoView({block:'center', inline:'center'});", target)
            time.sleep(0.5)
            ActionChains(driver).click_and_hold(item).pause(0.2).move_to_element(target).pause(0.4).release().perform()
        except Exception:
            try:
                img = item.find_element(By.TAG_NAME, "img")
                driver.execute_script("arguments[0].scrollIntoView({block:'center', inline:'center'});", img)
                driver.execute_script("arguments[0].scrollIntoView({block:'center', inline:'center'});", target)
                time.sleep(0.5)
                ActionChains(driver).click_and_hold(img).pause(0.2).move_to_element(target).pause(0.4).release().perform()
            except Exception:
                try:
                    driver.execute_script("arguments[0].scrollIntoView({block:'center', inline:'center'});", item)
                    time.sleep(0.5)
                    driver.execute_script("arguments[0].click();", item)
                except Exception:
                    try:
                        img = item.find_element(By.TAG_NAME, "img")
                        driver.execute_script("arguments[0].scrollIntoView({block:'center', inline:'center'});", img)
                        time.sleep(0.5)
                        driver.execute_script("arguments[0].click();", img)
                    except Exception as exc:
                        print(f"[WARN ] Unable to click toplist {log_label}: {exc}")
                        continue

        time.sleep(RENDER_GRACE_SEC + 1)

        rows = driver.execute_script("""
            var card = document.querySelector('#toplistGridCard');
            if (!card) return [];
            var rows = card.querySelectorAll('table.top-list-table tbody tr');
            return Array.from(rows).map(function(r){ return r.innerText || ''; });
        """) or []
        tickers: list[str] = []
        for row in rows:
            m = _TICKER_RE.match(row.strip())
            if m:
                tickers.append(m.group(1))

        tickers = [t for t in dict.fromkeys(tickers) if _is_valid_ti_ticker(t)]
        slug = re.sub(r'[^A-Za-z0-9]+', '_', label.strip().lower()).strip('_')
        if not slug:
            slug = f"toplist_{idx}"
        results[f"toplists_{slug}"] = tickers
        print(f"[OK   ] {log_label}: {len(tickers)} tickers — {tickers[:10]}{'…' if len(tickers)>10 else ''}")

        if update_config and tickers:
            added = _patch_config("PRIORITY_1_MOMENTUM", tickers)
            if added:
                print(f"[OK   ] universe.json: +{added} {log_label} tickers → tier 1")

    return results


# ── Stock Race Central: leaders vs laggards extraction ───────────
def _extract_race_sides(driver: "webdriver.Chrome") -> tuple[list[str], list[str]]:
    """
    Try to split stockracecentral tickers into:
      leaders  — top/green tiles (long candidates)  → tier 1
      laggards — bottom/red tiles (short candidates) → tier 2

    Strategy:
      1. Look for elements with positive vs negative change values
         (e.g. '+5.2%' vs '-3.1%') alongside ticker symbols.
      2. Fallback: read DOM order — first half = leaders, second half = laggards.
      3. If no split is possible, return (all, []) so everything goes to tier 1.
    """
    leaders:  list[str] = []
    laggards: list[str] = []

    try:
        result = driver.execute_script("""
            var leaders = [];
            var laggards = [];
            var seen = {};

            // Walk all elements looking for ticker+change pairs
            var allEls = document.querySelectorAll(
                '[class*="tile"],[class*="card"],[class*="row"],[class*="item"],[class*="stock"],[class*="race"]'
            );

            allEls.forEach(function(el) {
                var text = (el.innerText || el.textContent || '').trim();
                var tickerM = text.match(/\\b([A-Z]{2,5})\\b/);
                if (!tickerM) return;
                var ticker = tickerM[1];
                if (seen[ticker]) return;

                // Look for pct change in the element or its parent
                var combined = text + ' ' + (el.parentElement ? (el.parentElement.innerText || '') : '');
                var posM = combined.match(/\\+([0-9]+\\.?[0-9]*)%/);
                var negM = combined.match(/-([0-9]+\\.?[0-9]*)%/);

                // Also check for green/red background color hints
                var style = window.getComputedStyle(el);
                var bg = style.backgroundColor || '';
                // rgb(r,g,b) — green dominates if g>r and g>b, red if r>g
                var isGreen = bg.match(/rgb\\((\\d+),(\\d+),(\\d+)\\)/) &&
                              (function(m){ return parseInt(m[2])>parseInt(m[1]) && parseInt(m[2])>parseInt(m[3]); })
                              (bg.match(/rgb\\((\\d+),(\\d+),(\\d+)\\)/));
                var isRed   = bg.match(/rgb\\((\\d+),(\\d+),(\\d+)\\)/) &&
                              (function(m){ return parseInt(m[1])>parseInt(m[2]) && parseInt(m[1])>parseInt(m[3]); })
                              (bg.match(/rgb\\((\\d+),(\\d+),(\\d+)\\)/));

                seen[ticker] = true;
                if (negM && !posM || isRed)  { laggards.push(ticker); }
                else                          { leaders.push(ticker);  }
            });

            return {leaders: leaders, laggards: laggards};
        """) or {}

        leaders  = [t for t in (result.get("leaders", [])  or []) if t not in _IGNORE]
        laggards = [t for t in (result.get("laggards", []) or []) if t not in _IGNORE]
    except Exception:
        pass

    # Fallback: use standard extraction then split by DOM order
    if not leaders and not laggards:
        all_tickers = _extract_tickers(driver)
        mid = max(1, len(all_tickers) // 2)
        leaders  = all_tickers[:mid]
        laggards = all_tickers[mid:]
        print("[INFO ] Race side detection fell back to DOM-order split")

    # De-dup each list
    def _dedup(lst: list[str]) -> list[str]:
        seen: set[str] = set()
        return [t for t in lst if not (t in seen or seen.add(t))]  # type: ignore[func-returns-value]

    return _dedup(leaders[:25]), _dedup(laggards[:25])


def _ensure_logged_in(driver) -> bool:
    """If the page just loaded is TI's login form -- any scan page redirects
    here once the session's cookie has expired -- fill in TI_LOGIN /
    TI_PASSWORD from .env and submit. Returns True if we're past
    login (including "wasn't a login page at all"), False if a login form is
    still showing (no creds configured, or the submit didn't clear it) -- the
    caller skips scraping that page rather than let a login-page scrape
    pollute universe.json.

    2026-08-24, user request: this used to have no recovery at all -- a
    session lapsing silently produced a near-empty scrape (see
    MIN_VALID_TICKERS in _patch_config) until someone noticed and logged
    back in by hand.

    input[type='password'] is the detection signal: no real TI scan page has
    one, every login form does, so it needs no TI-specific markup to find.
    The email/username field is guessed from common attribute patterns since
    it's not as uniform -- falls back to the only text input on the page if
    nothing more specific matches.

    2026-08-24 (same day, caught by actually running it live): the first
    version took find_elements()[0] unconditionally and hit
    ElementNotInteractableException on every field -- a CSS match doesn't
    mean the element is the VISIBLE one; the page has other hidden/
    off-screen inputs matching the same broad selectors (a decoy field,
    a collapsed alternate form, etc.) that matched first. Now filters to
    is_displayed() and takes the first genuinely visible match.

    2026-08-27, user report (screenshot: "Your Free Use Has Expired" modal,
    "Continue With A Paid Account" / "View Plans & Subscribe" / "Log In To
    Your Account"): an unauthenticated/expired-trial visit doesn't drop
    straight into a login form -- it's gated behind this interstitial
    first, which has no password field of its own, so the old detection
    silently passed it through as "not a login page". Click the "Log In"
    entry point first (matched by visible text, not TI-specific markup,
    same principle as the password-field detection above) if present, THEN
    look for the password field it reveals.
    """
    try:
        login_links = [
            e for e in driver.find_elements(By.XPATH, "//*[self::button or self::a][contains(translate(., 'LOGIN', 'login'), 'log in')]")
            if e.is_displayed()
        ]
        if login_links:
            print("[....] 'Free Use Has Expired' interstitial detected -- clicking through to login form")
            login_links[0].click()
            WebDriverWait(driver, 5).until(
                lambda d: any(e.is_displayed() for e in d.find_elements(By.CSS_SELECTOR, "input[type='password']"))
            )
    except Exception:
        pass  # no interstitial, or it didn't reveal a password field -- fall through to the normal check

    try:
        pw_fields = [e for e in driver.find_elements(By.CSS_SELECTOR, "input[type='password']") if e.is_displayed()]
    except Exception:
        pw_fields = []
    if not pw_fields:
        return True  # not a login page

    if not TI_EMAIL or not TI_PASSWORD:
        print("[WARN ] TI login page detected but TI_LOGIN/TI_PASSWORD "
              "not set in .env -- can't auto-login")
        return False

    print("[....] TI session expired -- attempting auto-login")
    try:
        email_field = None
        for sel in ("input[type='email']", "input[name*='email' i]", "input[id*='email' i]",
                    "input[name*='user' i]", "input[id*='user' i]", "input[type='text']"):
            found = [e for e in driver.find_elements(By.CSS_SELECTOR, sel) if e.is_displayed()]
            if found:
                email_field = found[0]
                break
        if email_field is not None:
            email_field.click()
            email_field.clear()
            email_field.send_keys(TI_EMAIL)

        pw_fields[0].click()
        pw_fields[0].clear()
        pw_fields[0].send_keys(TI_PASSWORD)
        pw_fields[0].send_keys(Keys.RETURN)  # submit via Enter -- no site-specific button markup needed

        WebDriverWait(driver, LOGIN_WAIT_SEC).until(
            lambda d: not d.find_elements(By.CSS_SELECTOR, "input[type='password']")
        )
        print("[OK   ] TI auto-login succeeded")
        return True
    except Exception as e:
        print(f"[WARN ] TI auto-login failed: {e}")
        return False


# ── Main scrape function ──────────────────────────────────────────
def scrape_tradeideas(
    update_config: bool = False,
    headless: bool = False,
    chrome_profile: Optional[str] = None,
    select_minutes: Optional[int] = None,
    include_toplists: bool = False,
    scan_keys: Optional[list[str]] = None,
    select_30min: bool = False,
    browser: str = "edge",  # kept for signature compatibility; Edge is always used
    remote_debug_port: int = _REMOTE_DEBUG_PORT,
) -> dict[str, list[str]]:
    """
    Scrape Trade Ideas scan pages using a persistent Edge window.
    The browser stays open across calls so the TI login session is preserved.
    On the first run (or after a crash) the script tries to re-attach to an
    already-running Edge on *remote_debug_port* before opening a new window.
    If select_minutes is set, attempts to pick 'Change Last N Min (%)'
    from the heatmap dropdown before extracting tickers.
    Returns {scan_key: [tickers, …]}.
    """
    if not SELENIUM_OK:
        raise ImportError(
            "selenium / webdriver-manager not installed. "
            "Install packages: pip install selenium webdriver-manager pillow"
        )

    results: dict[str, list[str]] = {}
    # Reuse the persistent Edge window; re-attach if already running, else open new.
    driver = _get_driver(chrome_profile=chrome_profile, remote_debug_port=remote_debug_port)

    # Watchdog: if the scrape hangs for > 90 s, null out the driver singleton
    # and let the next cycle start fresh.  We don't kill Edge — the user may
    # have manually navigated away; just mark it dead so _get_driver re-opens.
    import threading as _ti_thread, subprocess as _ti_sp, sys as _ti_sys
    _scrape_done = _ti_thread.Event()

    def _hard_kill():
        global _edge_driver
        if _scrape_done.wait(90):
            return  # finished cleanly — nothing to do
        print("[WARN ] TI scrape hard-timeout (90 s) — marking Edge session dead")
        _edge_driver = None  # force re-open on next cycle

    _killer = _ti_thread.Thread(target=_hard_kill, daemon=True)
    _killer.start()

    if select_minutes is None:
        select_minutes = 15
    if select_30min:
        select_minutes = 30

    try:
        if scan_keys is not None:
            scan_keys = set(scan_keys)

        for scan_key, scan in SCANS.items():
            if scan_keys is not None and scan_key not in scan_keys:
                continue
            if scan_key == "toplists" and not include_toplists:
                continue

            url   = scan["url"]
            label = scan["label"]

            print(f"\n[....] Loading {url}")
            try:
                driver.get(url)
            except TimeoutException:
                print(f"[WARN ] Page load timeout for {scan_key}; continuing with partial DOM")

            # Wait for body/div to appear
            for sel in ["body", "div"]:
                try:
                    WebDriverWait(driver, TABLE_WAIT_SEC).until(
                        EC.presence_of_element_located((By.CSS_SELECTOR, sel))
                    )
                    break
                except Exception:
                    continue

            # Short grace period for React heatmap to render.
            time.sleep(RENDER_GRACE_SEC)

            if not _ensure_logged_in(driver):
                # 2026-08-28, user request: don't contribute zero tickers for
                # this cycle just because the login wall couldn't be cleared --
                # scrape whatever's visible on the page anonymously (TI's free
                # tier / teaser view) as a degraded fallback instead of nothing.
                # _extract_tickers naturally returns [] if there's truly no
                # ticker data on a bare login page, which flows through the
                # existing empty-list handling below same as any thin scrape.
                print(f"[WARN ] {scan_key}: still on TI login page — scraping anonymous/free view instead of skipping")

            if scan_key == "toplists":
                local_select_minutes = 15 if select_minutes is None else select_minutes
                toplist_results = _scrape_toplists(
                    driver,
                    select_minutes=local_select_minutes,
                    update_config=update_config,
                )
                results.update(toplist_results)
                tickers = [t for lst in toplist_results.values() for t in lst]
            else:
                # Optionally select a timeframe dropdown before scraping.
                if select_minutes is not None:
                    found = _try_select_timeframe(driver, select_minutes)
                    if not found:
                        print(f"[WARN ] Could not find {select_minutes}-min dropdown — scraping current view")
                    else:
                        time.sleep(DROPDOWN_REFRESH_SEC)

                tickers = _extract_tickers(driver)

                # 2026-08-28, user report: a thin/empty scrape can happen with
                # NO password field and NO interstitial visible -- _ensure_logged_in
                # only catches an actually-expired session, not a stale/cached page
                # that rendered fine but with no real data (soft-cache, half-loaded
                # heatmap, etc.). One hard reload + re-check catches that case too,
                # instead of silently accepting a near-empty scrape.
                if len(tickers) < _MIN_SCRAPE_TICKERS:
                    print(f"[WARN ] {scan_key}: only {len(tickers)} ticker(s) — reloading page and retrying once")
                    try:
                        driver.get(url)
                        for sel in ["body", "div"]:
                            try:
                                WebDriverWait(driver, TABLE_WAIT_SEC).until(
                                    EC.presence_of_element_located((By.CSS_SELECTOR, sel))
                                )
                                break
                            except Exception:
                                continue
                        time.sleep(RENDER_GRACE_SEC)
                        if _ensure_logged_in(driver):
                            if select_minutes is not None:
                                _try_select_timeframe(driver, select_minutes)
                                time.sleep(DROPDOWN_REFRESH_SEC)
                            retried = _extract_tickers(driver)
                            if len(retried) > len(tickers):
                                print(f"[OK   ] {scan_key}: retry recovered {len(retried)} ticker(s)")
                                tickers = retried
                        else:
                            print(f"[WARN ] {scan_key}: still on TI login page after reload — keeping anonymous/free view")
                    except Exception as e:
                        print(f"[WARN ] {scan_key}: reload retry failed ({e}) — keeping original {len(tickers)}-ticker scrape")

                results[scan_key] = tickers
            print(f"[OK   ] {scan_key}: {len(tickers)} tickers — {tickers[:10]}{'…' if len(tickers)>10 else ''}")

            if scan["target"] == "BOTH":
                # stockracecentral: split leaders (tier 1) vs laggards (tier 2)
                leaders, laggards = _extract_race_sides(driver)
                results[f"{scan_key}_leaders"]  = leaders
                results[f"{scan_key}_laggards"] = laggards
                print(f"[OK   ] {scan_key} leaders  ({len(leaders)}):  {leaders[:10]}{'…' if len(leaders)>10 else ''}")
                print(f"[OK   ] {scan_key} laggards ({len(laggards)}): {laggards[:10]}{'…' if len(laggards)>10 else ''}")
                if update_config:
                    if leaders:
                        added = _patch_config("PRIORITY_1_MOMENTUM", leaders)
                        print(f"[OK   ] universe.json: +{added} leader(s) → tier 1 (long candidates)")
                    if laggards:
                        added = _patch_config("PRIORITY_2_ESTABLISHED", laggards)
                        print(f"[OK   ] universe.json: +{added} laggard(s) → tier 2 (short candidates)")
            elif update_config and tickers:
                added = _patch_config(scan["target"], tickers)
                if added:
                    print(f"[OK   ] universe.json: +{added} new tickers added to tier {1 if 'PRIORITY_1' in scan['target'] else 2}")
                else:
                    print(f"[INFO ] universe.json: all tickers already present")
                # HSF tickers are persisted in universe.json tier-2, NOT config.py
                # (_patch_high_short_float rewrites config.py — disabled to prevent
                # continuous source-file modifications during live trading)

            # ── Persist unusual options volume tickers for the live options engine ──
            if scan_key == "unusualoptionsvolume" and tickers:
                clean_opts = [t for t in tickers if _is_valid_ti_ticker(t)]
                if len(clean_opts) >= _MIN_SCRAPE_TICKERS:
                    import json as _json, datetime as _dt
                    _data = {
                        "updated": _dt.datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
                        "tickers": clean_opts,
                    }
                    TI_UNUSUAL_OPTIONS_FILE.parent.mkdir(parents=True, exist_ok=True)
                    TI_UNUSUAL_OPTIONS_FILE.write_text(
                        _json.dumps(_data, indent=2), encoding="utf-8"
                    )
                    print(f"[OK   ] ti_unusual_options.json updated: {clean_opts[:10]}{'…' if len(clean_opts)>10 else ''}")
                else:
                    print(f"[WARN ] Unusual options scrape too sparse ({len(clean_opts)}) — ti_unusual_options.json not updated")

            # Navigate away so the tab goes blank
            try:
                driver.get("about:blank")
            except Exception:
                pass

    finally:
        _scrape_done.set()  # signal the watchdog: scrape finished normally

    # Persist the latest captured TI universe as the primary scan source.
    all_tickers: list[str] = []
    for scan_key, tickers in results.items():
        # Exclude unusual options tickers from the primary equity scan universe
        if scan_key == "unusualoptionsvolume":
            continue
        all_tickers.extend(tickers)
    clean_primary = [t for t in dict.fromkeys(all_tickers) if _is_valid_ti_ticker(t)]
    if len(clean_primary) >= _MIN_SCRAPE_TICKERS:
        try:
            import json as _json, datetime as _dt
            _data = {
                "updated": _dt.datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
                "tickers": clean_primary,
            }
            TI_PRIMARY_FILE.parent.mkdir(parents=True, exist_ok=True)
            TI_PRIMARY_FILE.write_text(_json.dumps(_data, indent=2), encoding="utf-8")
            print(f"[OK   ] ti_primary.json updated: {clean_primary[:10]}{'…' if len(clean_primary)>10 else ''}")

            if update_config:
                try:
                    import sys as _sys
                    _sys.path.insert(0, str(REPO_ROOT))
                    from engine.equity.universe import add_tickers  # noqa: E402
                    added = add_tickers(clean_primary, tier=1)
                    if added:
                        print(f"[OK   ] universe.json mirrored {added} latest TI primary tickers as tier 1")
                except Exception as exc:
                    print(f"[WARN ] universe.json mirror failed: {exc}")
        except Exception as exc:
            print(f"[WARN ] ti_primary.json update failed: {exc}")
    else:
        print(f"[WARN ] ti_primary.json not updated: only {len(clean_primary)} valid tickers")

    # Close the browser now that the scrape is done — no more staying open
    # between cycles. Each cycle logs into 'Default' fresh from the profile's
    # own saved cookies, so nothing about the TI login is lost by closing.
    #
    # driver.quit() alone is not enough: when this session came from
    # _try_attach_edge() (CDP reattach to an already-running window) rather
    # than _create_edge_driver(), Selenium never "owned" that browser's
    # process and quit() just drops the CDP connection — the window stays
    # open. Always follow up by killing our own marked process directly so
    # "closed" is actually true either way.
    global _edge_driver
    try:
        driver.quit()
    except Exception as exc:
        print(f"[WARN ] Edge quit() failed (may already be closed): {exc}")
    _edge_driver = None
    if _kill_orphaned_automation_edge(chrome_profile):
        print("[OK   ] Scrape done. Edge closed.")
    else:
        print("[OK   ] Scrape done. Edge closed (quit() already handled it).")

    return results


# ── CLI ───────────────────────────────────────────────────────────
def main() -> None:
    parser = argparse.ArgumentParser(
        description="Capture Trade Ideas scans and optionally update the stock universe"
    )
    parser.add_argument(
        "--update-config", action="store_true",
        help="Persist newly discovered tickers into data/universe.json (TTL-managed)",
    )
    parser.add_argument(
        "--loop", type=int, metavar="SECONDS", default=0,
        help="Repeat every N seconds (0 = single shot, default)",
    )
    parser.add_argument(
        "--chrome-profile", metavar="PROFILE", default=None,
        help='Use an existing Edge profile, e.g. "Default" (keeps TI login session)',
    )
    parser.add_argument(
        "--toplists", dest="include_toplists", action="store_true",
        help="Scrape the TIPro/toplists page in addition to the built-in scans",
    )
    parser.add_argument(
        "--30min", dest="select_30min", action="store_true",
        help="Select 'Change Last 30 Min (%%)' dropdown on each page before scraping",
    )
    parser.add_argument(
        "--15min", dest="select_15min", action="store_true",
        help="Select 'Change Last 15 Min (%%)' dropdown on each page before scraping",
    )
    parser.add_argument(
        "--remote-debug-port", dest="remote_debug_port",
        type=int, default=_REMOTE_DEBUG_PORT,
        metavar="PORT",
        help=(
            f"CDP remote-debugging port (default {_REMOTE_DEBUG_PORT}).  "
            "On the first run a new Edge window is opened on this port so that "
            "subsequent runs can re-attach to the same session (preserving TI login). "
            "Set to 0 to disable."
        ),
    )
    args = parser.parse_args()

    if args.select_15min and args.select_30min:
        print("[WARN ] Both --15min and --30min were passed; using --15min.")

    select_minutes = None
    if args.select_15min:
        select_minutes = 15
    elif args.select_30min:
        select_minutes = 30

    if args.loop > 0:
        print(f"[INFO ] Loop mode — capturing every {args.loop}s. Ctrl+C to stop.")
        while True:
            try:
                scrape_tradeideas(
                    update_config=args.update_config,
                    chrome_profile=args.chrome_profile,
                    select_minutes=select_minutes,
                    include_toplists=args.include_toplists,
                    remote_debug_port=args.remote_debug_port,
                )
            except Exception as exc:
                # An uncaught exception here used to kill the whole scheduled task —
                # e.g. a locked Edge profile crashed the loop for 30+ hours until the
                # next 08:00/logon trigger (2026-08-03). Log and retry next interval
                # instead; drop the driver singleton so the retry opens fresh.
                global _edge_driver
                print(f"[ERROR] Scrape cycle failed: {exc} — retrying in {args.loop}s")
                _edge_driver = None
            print(f"[INFO ] Sleeping {args.loop}s …")
            time.sleep(args.loop)
    else:
        try:
            scrape_tradeideas(
                update_config=args.update_config,
                chrome_profile=args.chrome_profile,
                select_minutes=select_minutes,
                include_toplists=args.include_toplists,
                remote_debug_port=args.remote_debug_port,
            )
        except Exception as exc:
            # Single-shot runs are owned by Task Scheduler's own 20-min
            # trigger (see run_ti_capture_task.ps1) — log cleanly and exit
            # non-zero instead of dumping a raw traceback; the next trigger
            # retries on its own.
            print(f"[ERROR] Scrape cycle failed: {exc}")
            raise SystemExit(1)


if __name__ == "__main__":
    main()
