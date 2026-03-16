
"""
NovaSync — Nova Act restaurant booking agent.

Searches for a restaurant matching the user's description, then books a
reservation using Nova Act browser automation. Emits log, screenshot, and
result events to an asyncio-safe queue.Queue.
"""

from __future__ import annotations

import asyncio
import base64
import logging
import os
import queue
import re
import sys
import threading
import urllib.parse
from dataclasses import dataclass

logger = logging.getLogger(__name__)

# Eager import: pay the ~3s cost at server startup, not on each booking request.
try:
    from nova_act import NovaAct as _NovaAct  # type: ignore[import-untyped]

    _NOVA_ACT_AVAILABLE = True
except ImportError:
    _NovaAct = None  # type: ignore[assignment,misc]
    _NOVA_ACT_AVAILABLE = False
    logger.warning("nova_act not installed — booking agent will be unavailable")


# Generic/placeholder words that indicate the LLM produced a non-specific title.
_GENERIC_TOKENS: frozenset[str] = frozenset(
    {
        "michelin", "listed", "starred", "restaurant", "dinner", "lunch",
        "breakfast", "local", "traditional", "visit", "meal", "dining",
        "food", "street", "market", "fine", "casual", "family", "authentic",
        "japanese", "western", "italian", "french", "chinese", "thai",
        "seafood", "steakhouse", "bistro", "brasserie", "bar", "grill",
        "cafe", "buffet", "sushi", "ramen", "izakaya", "teppanyaki",
    }
)

# Maps destination keywords → best starting URL for that market.
_JAPAN_KEYWORDS = frozenset(
    {"japan", "kyoto", "tokyo", "osaka", "nara", "hiroshima", "sapporo",
     "fukuoka", "nagoya", "yokohama", "sushi", "ramen", "kaiseki",
     "izakaya", "tempura", "soba", "udon", "yakitori", "wagyu",
     "teppanyaki", "shabu", "sukiyaki", "tonkatsu", "okonomiyaki",
     "yakiniku", "omakase", "kappo", "robatayaki"}
)

# Maps English food keywords → natural Japanese search term for Tabelog.
_FOOD_TO_JP_KEYWORD: dict[str, str] = {
    "teppanyaki": "鉄板焼き",
    "sushi": "寿司",
    "ramen": "ラーメン",
    "yakitori": "焼き鳥",
    "tempura": "天ぷら",
    "kaiseki": "懐石",
    "shabu": "しゃぶしゃぶ",
    "izakaya": "居酒屋",
    "wagyu": "和牛",
    "unagi": "うなぎ",
    "sukiyaki": "すき焼き",
    "tonkatsu": "とんかつ",
    "okonomiyaki": "お好み焼き",
    "yakiniku": "焼肉",
    "omakase": "おまかせ",
    "robatayaki": "炉端焼き",
    "kappo": "割烹",
    "soba": "そば",
    "udon": "うどん",
}

# Maps Japanese city names → Tabelog prefecture slug (used in URL path).
_CITY_TO_TABELOG_SLUG: dict[str, str] = {
    "tokyo": "tokyo", "kyoto": "kyoto", "osaka": "osaka",
    "nara": "nara", "hiroshima": "hiroshima", "sapporo": "hokkaido",
    "fukuoka": "fukuoka", "nagoya": "aichi", "yokohama": "kanagawa",
}

# Maps English city names → best Tabelog area search term (major station).
_CITY_TO_STATION: dict[str, str] = {
    "kyoto": "京都駅",
    "tokyo": "東京駅",
    "osaka": "大阪駅",
    "sapporo": "札幌駅",
    "fukuoka": "博多駅",
    "nara": "奈良駅",
    "hiroshima": "広島駅",
    "nagoya": "名古屋駅",
    "yokohama": "横浜駅",
}

# Sentinel returned by Nova Act when the form is filled but the final
# confirm button has not been clicked yet — used for the second HITL pause.
_CONFIRM_SENTINEL = "READY_TO_CONFIRM"


def _resolve_japan_area(trip_location: str | None) -> str:
    """Map English city name to nearest major station for Tabelog area search."""
    if not trip_location:
        return ""
    key = trip_location.lower().split(",")[0].strip()
    return _CITY_TO_STATION.get(key, trip_location)


def _resolve_japan_keyword(name: str, description: str | None) -> str:
    """Derive the best Japanese search keyword for Tabelog from the activity name/description."""
    text = f"{name} {description or ''}".lower()
    for en_kw, jp_kw in _FOOD_TO_JP_KEYWORD.items():
        if en_kw in text:
            return jp_kw
    # If the restaurant has a specific name, use it directly.
    if not _is_generic_name(name):
        return name
    return "レストラン"  # fallback: generic "restaurant"


# Well-known cities we can extract from free-text for location anchoring.
_KNOWN_CITIES = [
    "kyoto", "tokyo", "osaka", "nara", "hiroshima", "sapporo", "fukuoka",
    "nagoya", "yokohama", "new york", "london", "paris", "rome", "barcelona",
    "sydney", "melbourne", "singapore", "bangkok", "bali", "hong kong",
    "amsterdam", "berlin", "vienna", "prague", "lisbon", "dubai", "seoul",
]


class _NovaActLogHandler(logging.Handler):
    """Bridges nova_act.* log records → step events in event_queue."""

    _NAVIGATE_RE = re.compile(r"start session .+ on (https?://\S+)")
    _WORKFLOW_RE = re.compile(r"Created workflow run (\S+)")

    def __init__(self, event_queue: queue.Queue) -> None:
        super().__init__()
        self._q = event_queue
        # Capture the owning thread at construction so concurrent sessions
        # each only process their own log records (nova_act logger is a
        # module-level singleton shared across all threads).
        self._owner_thread = threading.current_thread()

    def emit(self, record: logging.LogRecord) -> None:
        if threading.current_thread() is not self._owner_thread:
            return
        msg = record.getMessage()
        if m := self._NAVIGATE_RE.search(msg):
            self._q.put({"type": "step", "action": "navigate",
                         "text": f"Browser opened at {m.group(1)}"})
        elif self._WORKFLOW_RE.search(msg):
            self._q.put({"type": "step", "action": "phase",
                         "text": "Nova Act session created"})
        else:
            self._q.put({"type": "step", "action": "log", "text": msg[:200]})


class _StdoutStepCapture:
    """
    Wraps sys.stdout during a Nova Act session to forward agent step lines
    (think / agentType / agentClick / agentScroll) to the event queue.

    All writes are passed through to the original stdout unchanged so the
    server terminal still shows the full Nova Act trace.  A thread-identity
    check on every write() call prevents concurrent booking sessions from
    cross-contaminating each other's queues.
    """

    # Matches lines like: "e3c5> think(" / "e3c5> agentType(" etc.
    _LINE_RE = re.compile(
        r"[0-9a-f-]{4,}>\s+(think|agentType|agentClick|agentScroll|act)\("
    )

    def __init__(self, event_queue: queue.Queue, original: object) -> None:
        self._q = event_queue
        self._orig = original
        self._owner = threading.current_thread()
        self._buf = ""

    # ── io interface ─────────────────────────────────────────────────────────

    def write(self, s: str) -> int:
        self._orig.write(s)  # type: ignore[union-attr]  # always pass through
        if threading.current_thread() is not self._owner:
            return len(s)
        self._buf += s
        while "\n" in self._buf:
            line, self._buf = self._buf.split("\n", 1)
            self._process_line(line)
        return len(s)

    def flush(self) -> None:
        self._orig.flush()  # type: ignore[union-attr]

    def fileno(self) -> int:
        return self._orig.fileno()  # type: ignore[union-attr]

    @property
    def encoding(self) -> str:
        return getattr(self._orig, "encoding", "utf-8")

    @property
    def errors(self) -> str:
        return getattr(self._orig, "errors", "strict")

    # ── Line parser ──────────────────────────────────────────────────────────

    def _process_line(self, line: str) -> None:
        m = self._LINE_RE.search(line)
        if not m:
            return
        verb = m.group(1)
        if verb == "think":
            inner = re.search(r'think\("(.{0,120})', line)
            text = (inner.group(1).rstrip('"\\') + "…") if inner else "Thinking…"
            self._q.put({"type": "step", "action": "log", "text": f"💭 {text}"})
        elif verb == "agentType":
            inner = re.search(r'agentType\("([^"]{0,80})', line)
            text = f"Typing: {inner.group(1)}" if inner else "Typing…"
            self._q.put({"type": "step", "action": "type", "text": text})
        elif verb == "agentClick":
            self._q.put({"type": "step", "action": "click", "text": "Clicking element…"})
        elif verb == "agentScroll":
            self._q.put({"type": "step", "action": "log", "text": "Scrolling page…"})


def _extract_city(text: str) -> str | None:
    """Return the first recognisable city name found in `text`, or None."""
    lower = text.lower()
    for city in _KNOWN_CITIES:
        if city in lower:
            return city.title()
    return None


@dataclass
class BookingResult:
    success: bool
    confirmation_number: str | None = None
    booking_time: str | None = None
    notes: str | None = None
    error: str | None = None


def _is_generic_name(name: str) -> bool:
    """Return True if the restaurant title is a generic placeholder."""
    tokens = set(re.findall(r"[a-z]+", name.lower()))
    return bool(tokens) and tokens.issubset(
        _GENERIC_TOKENS | {"a", "an", "the", "at", "in", "and", "or", "of", "with"}
    )


def _choose_starting_url(
    description: str | None, name: str, trip_location: str | None
) -> str:
    """Pick the most relevant starting URL for Nova Act's browser session.

    For Japan bookings: start at the Tabelog homepage and use the search
    form (hybrid Playwright + Nova Act demo flow).

    For all other markets: Google Maps free-text search.
    """
    combined = f"{description or ''} {name} {trip_location or ''}".lower()
    if not any(kw in combined for kw in _JAPAN_KEYWORDS):
        location_part = trip_location or _extract_city(combined) or ""
        search_term = (description or name)[:80]
        query = urllib.parse.quote_plus(f"{search_term} {location_part}".strip())
        return f"https://www.google.com/maps/search/{query}"

    # Japan demo: start at the Tabelog homepage (loads reliably).
    # nova.act() will navigate to the Kyoto search results from there.
    return "https://tabelog.com/"


def _build_task_prompt(
    name: str,
    description: str | None,
    trip_location: str | None,
    date: str,
    time: str,
    party_size: int,
    phone_number: str,
) -> str:
    """Build the Nova Act task instruction."""
    is_generic = _is_generic_name(name)

    # Resolve the best location hint available.
    location = (
        trip_location
        or _extract_city(f"{description or ''} {name}")
        or None
    )
    location_clause = f" in {location}" if location else ""

    is_japan = any(
        kw in f"{description or ''} {name} {trip_location or ''}".lower()
        for kw in _JAPAN_KEYWORDS
    )

    search_hint = description if description else name

    if is_japan:
        find_instruction = (
            f"You are on the Tabelog reservation page for 京都鉄板焼 結 (Kyoto Teppanyaki Musubi). "
            f"The date (3月28日), time (12:00), and party size (2名) are ALREADY PRE-SELECTED. "
            f"Do NOT change them. DO NOT TRY CHANGING ANY DATES OR TIMES\n\n"
            f"CRITICAL: Stay on the Japanese version (tabelog.com). "
            f"Do NOT go to tabelog.com/en/.\n\n"
            f"Step 1 — In the ネット予約 widget on the right side of the page, "
            f"confirm the time dropdown shows '○ 12:00'. "
            f"If the time field shows a different value, use agentType to set it: "
            f"type the value '○ 12:00' directly into the time select field (do NOT click it). "
            f"Then click the orange 予約する button.\n\n"
            f"Step 2 — On the next page, if asked for contact info: "
            f"phone {phone_number}, name 'Guest'.\n\n"
            f"Step 3 — Select a course if prompted. Choose the first available lunch course.\n\n"
            f"Step 4 — Proceed as far as possible. "
            f"If you reach a sign-in or login page that blocks further progress, STOP immediately."
        )
    else:
        find_instruction = (
            f"Search Google Maps for a restaurant{location_clause} that matches: "
            f"'{search_hint}'. Browse the map results, pick the highest-rated or most "
            f"relevant option, click on it, then navigate to its reservation page "
            f"(look for 'Reserve a table', 'Book', or an OpenTable/Resy link in the "
            f"Maps listing or restaurant website)."
        )

    if is_japan:
        # Japan path: find_instruction already contains complete booking details.
        return find_instruction

    return (
        f"{find_instruction} "
        f"Once you find a suitable restaurant, navigate to their reservation "
        f"or booking page (look for a 'Reserve', 'Book a Table', or 'Reservation' button). "
        f"Complete a reservation for {party_size} {'person' if party_size == 1 else 'people'} "
        f"on {date} at {time}. "
        f"Use phone number {phone_number} when asked for contact details. "
        f"If asked for a name, use 'Guest'. "
        f"Submit the form and confirm the booking. "
        f"Report back with the confirmation number and any booking details."
    )


class JapaneseRestaurantBookingAgent:
    """
    Runs a Nova Act browser session to find and book a restaurant.

    Events emitted to `event_queue`:
      {"type": "log",        "message": str, "log_type": "info"|"action"|"success"|"error"}
      {"type": "screenshot", "image_data": str}  # base64 JPEG
      {"type": "result",     "result": BookingResult}
      {"type": "done"}  # terminal sentinel — always the last item
    """

    def __init__(
        self,
        restaurant_name: str,
        restaurant_description: str | None,
        trip_location: str | None,
        date: str,
        time: str,
        party_size: int,
        phone_number: str,
        event_queue: queue.Queue,
        booking_id: str | None = None,
        resume_event: threading.Event | None = None,
    ) -> None:
        self.restaurant_name = restaurant_name
        self.restaurant_description = restaurant_description
        self.trip_location = trip_location
        self.date = date
        self.time = time
        self.party_size = party_size
        self.phone_number = phone_number
        self.event_queue = event_queue
        self.booking_id = booking_id
        self._resume_event = resume_event or threading.Event()

    # ── Private helpers ───────────────────────────────────────────────────────

    def _log(self, message: str, log_type: str = "info") -> None:
        self.event_queue.put({"type": "log", "message": message, "log_type": log_type})

    def _screenshot(self, image_data: str) -> None:
        self.event_queue.put({"type": "screenshot", "image_data": image_data})

    def _result(self, result: BookingResult) -> None:
        self.event_queue.put({"type": "result", "result": result})

    def _done(self) -> None:
        self.event_queue.put({"type": "done"})

    def _emit_needs_auth(self, message: str, auth_url: str | None = None) -> None:
        self.event_queue.put({"type": "needs_auth", "message": message, "auth_url": auth_url})

    def _emit_course_review(self, message: str, summary: str = "") -> None:
        self.event_queue.put({"type": "needs_course_review", "message": message, "summary": summary})

    # ── Public ────────────────────────────────────────────────────────────────

    def run(self) -> None:
        """Execute the booking agent synchronously (call from a thread)."""
        api_key = os.getenv("NOVA_ACT_API_KEY")
        if not api_key:
            self._log("NOVA_ACT_API_KEY is not configured.", "error")
            self._result(BookingResult(success=False, error="Nova Act API key not configured"))
            self._done()
            return

        starting_url = _choose_starting_url(
            self.restaurant_description, self.restaurant_name, self.trip_location
        )
        task = _build_task_prompt(
            self.restaurant_name,
            self.restaurant_description,
            self.trip_location,
            self.date,
            self.time,
            self.party_size,
            self.phone_number,
        )

        self._log(f"Opening browser at {starting_url} …", "info")
        self._log(f"Task: {task}", "action")

        try:
            if not _NOVA_ACT_AVAILABLE or _NovaAct is None:
                raise ImportError("nova_act not installed")

            self.event_queue.put({"type": "step", "action": "phase", "text": "Launching browser…"})
            _nova_logger = logging.getLogger("nova_act")
            _log_handler = _NovaActLogHandler(self.event_queue)
            _nova_logger.addHandler(_log_handler)
            _stdout_capture = _StdoutStepCapture(self.event_queue, sys.stdout)
            sys.stdout = _stdout_capture  # type: ignore[assignment]
            try:
                import pathlib
                _profile_path = pathlib.Path(
                    os.getenv("NOVA_ACT_PROFILE_DIR",
                              str(pathlib.Path.home() / ".nova_act_tabelog_profile"))
                )
                _profile_path.mkdir(parents=True, exist_ok=True)
                _profile_is_new = not any(_profile_path.iterdir())
                # Always run visible for the demo so the user can sign in.
                _headless = False

                # Allow Google/Apple OAuth popup windows that Chromium blocks by
                # default in automation mode. Nova Act reads this env var at
                # browser launch (nova_act.py:385) and passes the flags to Playwright.
                os.environ.setdefault(
                    "NOVA_ACT_BROWSER_ARGS",
                    "--disable-popup-blocking --no-sandbox",
                )

                _nova_kwargs: dict = dict(
                    starting_page=starting_url,
                    nova_act_api_key=api_key,
                    headless=_headless,
                    ignore_https_errors=True,
                )
                # Nova Act rejects an empty user_data_dir — only pass it when the
                # profile already has Chromium session data from a prior login.
                if not _profile_is_new:
                    _nova_kwargs["user_data_dir"] = str(_profile_path)

                nova = _NovaAct(**_nova_kwargs)

                nova.start()
                self._log("Browser started — navigating to search page …", "info")
                self.event_queue.put({"type": "step", "action": "navigate",
                                      "text": f"Opened {starting_url}"})

                # Capture the initial page state.
                self._try_screenshot(nova)

                # Detect Japan market to choose the demo flow.
                _combined = (
                    f"{self.restaurant_description or ''} "
                    f"{self.restaurant_name} "
                    f"{self.trip_location or ''}"
                ).lower()
                is_japan = any(kw in _combined for kw in _JAPAN_KEYWORDS)

                self.event_queue.put({"type": "step", "action": "phase",
                                      "text": "Starting booking task…"})

                # Poll for live screenshots every 2 s while act() blocks.
                _stop_screenshots = threading.Event()

                def _screenshot_loop() -> None:
                    while not _stop_screenshots.wait(2.0):
                        self._try_screenshot(nova)

                _screenshot_thread = threading.Thread(
                    target=_screenshot_loop, daemon=True
                )
                _screenshot_thread.start()

                act_result = None
                try:
                    if is_japan:
                        # Japan demo: search-first flow.
                        # Opens Kyoto search results, uses nova.act() to select
                        # Japanese language, fill keyword search, and land on
                        # the restaurant detail page. No sign-in required.
                        _japan_handled = self._run_japan_demo(nova)
                        act_result = "RESTAURANT_FOUND" if _japan_handled else "Japan demo incomplete"
                    else:
                        # Non-Japan: single nova.act() task with Google Maps search.
                        _SENTINEL = "SIGN_IN_REQUIRED"
                        step1_task = (
                            task
                            + " IMPORTANT: If you reach any sign-in, login, or authentication page "
                            "that blocks you, STOP and return exactly: "
                            "'SIGN_IN_REQUIRED: [brief reason and current URL]'. "
                            "Do NOT click 'Sign in with Google' or similar buttons."
                        )
                        try:
                            act_result = nova.act(step1_task)
                        except Exception as phase1_exc:
                            act_result = str(phase1_exc)
                            self._log(f"Phase 1 exception: {act_result[:200]}", "info")
                        self._log(f"Phase 1 response: {act_result}", "info")
                        self._try_screenshot(nova)
                finally:
                    _stop_screenshots.set()
                    _screenshot_thread.join(timeout=3.0)

            finally:
                try:
                    nova.stop()
                    self._log("Browser session closed.", "info")
                except Exception:
                    pass
                sys.stdout = _stdout_capture._orig  # type: ignore[assignment]
                _nova_logger.removeHandler(_log_handler)

            if not (is_japan and _japan_handled):
                success = self._infer_success(act_result)
                if success:
                    self._log("Booking completed successfully!", "success")
                else:
                    self._log("Could not confirm reservation — please check manually.", "error")
                self._result(
                    BookingResult(
                        success=success,
                        notes=str(act_result) if act_result else None,
                        error=None if success else "Reservation not confirmed",
                    )
                )

        except ImportError:
            self._log("nova_act package is not installed.", "error")
            self._result(BookingResult(success=False, error="Nova Act is not available"))
        except Exception as exc:
            logger.exception("Nova Act booking agent raised an unexpected error")
            self._log(f"Unexpected error: {exc}", "error")
            self._result(BookingResult(success=False, error=str(exc)))
        finally:
            self._done()

    # ── Japan demo flow ───────────────────────────────────────────────────────

    def _run_japan_demo(self, nova: object) -> bool:  # type: ignore[type-arg]
        """Tabelog demo: select Japanese, search for restaurant, land on detail page.

        Phase 0 — Playwright: wait for page load, press Escape to dismiss popups.
        Phase 1 — nova.act(): language popup → search form → restaurant detail page.

        Returns True if this method emitted its own BookingResult (success or failure),
        False to let run() emit a generic result.
        """
        page = getattr(nova, "page", None)

        # ── Phase 0: Playwright — fill Japanese fields directly ───────────────
        # agentType sends keyboard events which break Japanese IME composition.
        # Playwright's fill() bypasses the keyboard entirely and sets the value
        # directly, so Japanese text lands correctly every time.
        self._log("Phase 0: Filling Tabelog search fields via Playwright…", "action")
        self.event_queue.put({"type": "step", "action": "phase",
                              "text": "Filling search form…"})

        _fields_filled = False
        if page is not None:
            try:
                page.wait_for_load_state("domcontentloaded", timeout=15000)
                page.wait_for_timeout(1500)
                page.keyboard.press("Escape")
                page.wait_for_timeout(300)

                # Area field — try common Tabelog selectors.
                _AREA_SELECTORS = [
                    "input[name='LstSrchCtrl[areaName]']",
                    "input[placeholder*='エリア']",
                    "input[placeholder*='駅']",
                    "#areaName",
                ]
                for sel in _AREA_SELECTORS:
                    try:
                        el = page.query_selector(sel)
                        if el and el.is_visible():
                            el.fill("京都駅")
                            self._log(f"Filled area field ({sel})", "info")
                            break
                    except Exception:
                        continue

                # Keyword field.
                _KW_SELECTORS = [
                    "input[name='LstSrchCtrl[freeWord]']",
                    "input[placeholder*='キーワード']",
                    "input[placeholder*='店名']",
                    "#freeWord",
                ]
                for sel in _KW_SELECTORS:
                    try:
                        el = page.query_selector(sel)
                        if el and el.is_visible():
                            el.fill("鉄板焼き")
                            self._log(f"Filled keyword field ({sel})", "info")
                            _fields_filled = True
                            break
                    except Exception:
                        continue

                self._try_screenshot(nova)
            except Exception as exc:
                self._log(f"Phase 0 warning: {exc} — continuing", "info")

        # ── Phase 1: nova.act() — date, time, search, navigate ────────────────
        self._log("Phase 1: Setting date/time and searching…", "action")
        self.event_queue.put({"type": "step", "action": "phase",
                              "text": "Searching Tabelog for restaurant…"})

        if _fields_filled:
            _area_kw_instruction = (
                "The area field already contains '京都駅' and the keyword field "
                "already contains '鉄板焼き' — do NOT change them.\n\n"
            )
        else:
            _area_kw_instruction = (
                "1a. Click the area (エリア・駅) field and type '京都駅'.\n"
                "1b. Click the keyword (キーワード) field and type '鉄板焼き'.\n\n"
            )

        # Phase 1: nova.act() sets date/time and submits search only.
        # Keeping this call small prevents the agent from getting distracted
        # trying to also find the restaurant in the same task.
        _SEARCH_TASK = (
            "You are on the Tabelog homepage with a search bar at the top.\n\n"
            + _area_kw_instruction +
            "1. If a language popup is visible, click '日本語'.\n"
            "2. Click the date field and select '2026/3/28'.\n"
            "3. Click the time field and select '12:00'.\n"
            "4. Click the orange 検索 button.\n"
            "5. Once the search results page has loaded, return exactly: 'SEARCH_DONE'."
        )

        phase1_result = None
        try:
            phase1_result = nova.act(_SEARCH_TASK)  # type: ignore[union-attr]
        except Exception as exc:
            phase1_result = str(exc)
            self._log(f"Phase 1 exception: {phase1_result[:200]}", "info")

        self._log(f"Phase 1 result: {phase1_result}", "info")
        self._try_screenshot(nova)

        # ── Phase 2: nova.act() — focused click on the restaurant ─────────────
        self._log("Phase 2: Clicking 京都鉄板焼 結 in results…", "action")
        self.event_queue.put({"type": "step", "action": "phase",
                              "text": "Clicking restaurant in results…"})

        _CLICK_TASK = (
            "You are on the Tabelog search results page.\n\n"
            "STEP 1 — Look at every restaurant card currently visible on screen "
            "and write out the Japanese name of each one you can read, like:\n"
            "  - [name 1]\n"
            "  - [name 2]\n"
            "  ...\n\n"
            "STEP 2 — Check if '京都鉄板焼 結' is in your list.\n"
            "  - If YES: click on that restaurant's name link.\n"
            "  - If NO: scroll down slowly and repeat STEP 1 until you find it.\n\n"
            "STEP 3 — Once you are on the restaurant detail page for '京都鉄板焼 結', "
            "return exactly: 'RESTAURANT_FOUND: [current page URL]'."
        )

        phase2_result = None
        try:
            phase2_result = nova.act(_CLICK_TASK)  # type: ignore[union-attr]
        except Exception as exc:
            phase2_result = str(exc)
            self._log(f"Phase 2 exception: {phase2_result[:200]}", "info")

        self._log(f"Phase 2 result: {phase2_result}", "info")
        self._try_screenshot(nova)

        # ── Result ─────────────────────────────────────────────────────────────
        if "RESTAURANT_FOUND" in str(phase2_result):
            url_match = re.search(r"RESTAURANT_FOUND:\s*(https?://\S+)", str(phase2_result))
            found_url = url_match.group(1).rstrip(".,)") if url_match else "tabelog.com"
            self._log(f"Restaurant detail page reached: {found_url}", "success")
            self.event_queue.put({"type": "step", "action": "phase",
                                  "text": "Restaurant found — demo complete"})
            self._result(BookingResult(
                success=True,
                notes=f"Found restaurant page — booking demo complete. URL: {found_url}",
            ))
            return True

        self._log("Could not locate 京都鉄板焼 結 in search results.", "error")
        self.event_queue.put({"type": "step", "action": "phase",
                              "text": "Restaurant not found in results"})
        return False

    # ── Internal helpers ─────────────────────────────────────────────────────

    def _try_screenshot(self, nova: object) -> None:  # type: ignore[type-arg]
        """Take a screenshot via the underlying async Playwright page.

        Playwright's sync Page API uses greenlets tied to the creating OS thread.
        Calling page.screenshot() from a background thread raises
        `greenlet.error: Cannot switch to a different thread`.

        The fix: obtain the async page (_impl_obj) and schedule the coroutine
        on the Playwright event loop with asyncio.run_coroutine_threadsafe(),
        which returns a concurrent.futures.Future — no greenlet switching needed.
        """
        try:
            page = getattr(nova, "page", None)
            if page is None:
                return
            async_page = getattr(page, "_impl_obj", None)
            loop: asyncio.AbstractEventLoop | None = getattr(page, "_loop", None)
            if async_page is None or loop is None or not loop.is_running():
                return
            # Schedule on the Playwright event loop from this background thread.
            future = asyncio.run_coroutine_threadsafe(
                async_page.screenshot(type="jpeg", quality=75),
                loop,
            )
            raw: bytes = future.result(timeout=5.0)
            if raw:
                self._screenshot(base64.b64encode(raw).decode())
        except Exception as exc:
            logger.debug("Screenshot capture failed (non-fatal): %s", exc)

    @staticmethod
    def _infer_success(act_result: object) -> bool:
        text = str(act_result).lower() if act_result else ""
        return any(
            kw in text
            for kw in (
                "confirm", "confirmed", "booked", "reservation", "success",
                "thank you", "booking id", "booking number",
            )
        )
