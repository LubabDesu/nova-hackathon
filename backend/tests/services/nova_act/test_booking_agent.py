"""Unit tests for JapaneseRestaurantBookingAgent._try_screenshot().

The implementation uses asyncio.run_coroutine_threadsafe() to schedule
Playwright's async screenshot coroutine from a background thread, bypassing
the greenlet thread-affinity constraint of Playwright's sync API.

Tests patch asyncio.run_coroutine_threadsafe so no real event loop is needed.
"""

import base64
import os
import queue
import threading
from unittest.mock import MagicMock, patch

import pytest

from services.nova_act.booking_agent import (
    JapaneseRestaurantBookingAgent,
    _choose_starting_url,
)

_PATCH = "services.nova_act.booking_agent.asyncio.run_coroutine_threadsafe"


# ---------------------------------------------------------------------------
# Shared fixture
# ---------------------------------------------------------------------------

@pytest.fixture
def agent() -> JapaneseRestaurantBookingAgent:
    """A minimal agent with a real queue for event inspection."""
    return JapaneseRestaurantBookingAgent(
        restaurant_name="Test Restaurant",
        restaurant_description=None,
        trip_location=None,
        date="2026-03-14",
        time="19:00",
        party_size=2,
        phone_number="+1-555-0100",
        event_queue=queue.Queue(),
    )


def _make_nova(screenshot_bytes: bytes = b"\xff\xd8\xff\xe0") -> tuple[MagicMock, MagicMock]:
    """Return (nova_mock, future_mock).

    nova.page._impl_obj  — the async Playwright Page (mock)
    nova.page._loop      — a mock event loop reporting is_running() = True
    future_mock          — the Future returned by run_coroutine_threadsafe;
                           its .result() returns screenshot_bytes
    """
    future = MagicMock()
    future.result.return_value = screenshot_bytes

    nova = MagicMock()
    nova.page._impl_obj = MagicMock()      # async page
    nova.page._loop = MagicMock()
    nova.page._loop.is_running.return_value = True

    return nova, future


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------

class TestTryScreenshot:

    @patch(_PATCH)
    def test_valid_bytes_emits_base64_jpeg_screenshot(self, mock_rct, agent):
        """Valid bytes from the async screenshot → screenshot event queued as base64 string."""
        raw = b"\xff\xd8\xff\xe0"
        nova, future = _make_nova(screenshot_bytes=raw)
        mock_rct.return_value = future

        agent._try_screenshot(nova)

        assert not agent.event_queue.empty()
        event = agent.event_queue.get_nowait()
        assert event["type"] == "screenshot"
        assert event["image_data"] == base64.b64encode(raw).decode()
        assert isinstance(event["image_data"], str)

    @patch(_PATCH)
    def test_screenshot_raises_exception_is_swallowed(self, mock_rct, agent):
        """Any exception from the future is silently suppressed — no crash, queue stays empty."""
        nova, future = _make_nova()
        future.result.side_effect = RuntimeError("Playwright page closed")
        mock_rct.return_value = future

        agent._try_screenshot(nova)  # must not raise

        assert agent.event_queue.empty()

    @patch(_PATCH)
    def test_empty_bytes_does_not_emit_screenshot(self, mock_rct, agent):
        """Empty bytes are filtered out — no screenshot event is queued."""
        nova, future = _make_nova(screenshot_bytes=b"")
        mock_rct.return_value = future

        agent._try_screenshot(nova)

        assert agent.event_queue.empty()

    def test_none_page_attribute_does_not_raise(self, agent):
        """nova.page = None returns early without raising or touching the queue."""
        nova = MagicMock()
        nova.page = None

        agent._try_screenshot(nova)

        assert agent.event_queue.empty()

    def test_missing_page_attribute_does_not_raise(self, agent):
        """A nova object with no .page at all returns early without raising."""
        nova = object()

        agent._try_screenshot(nova)

        assert agent.event_queue.empty()

    @patch(_PATCH)
    def test_screenshot_called_with_jpeg_quality(self, mock_rct, agent):
        """The async page's screenshot() is scheduled with type='jpeg' and quality=75."""
        raw = b"\xff\xd8\xff"
        nova, future = _make_nova(screenshot_bytes=raw)
        mock_rct.return_value = future

        agent._try_screenshot(nova)

        # Verify the coroutine was created with the right arguments.
        nova.page._impl_obj.screenshot.assert_called_once_with(type="jpeg", quality=75)

    @patch(_PATCH)
    def test_stopped_loop_skips_screenshot(self, mock_rct, agent):
        """If the Playwright event loop is not running, skip without crashing."""
        nova, _ = _make_nova()
        nova.page._loop.is_running.return_value = False

        agent._try_screenshot(nova)

        mock_rct.assert_not_called()
        assert agent.event_queue.empty()


# ---------------------------------------------------------------------------
# TestChooseStartingUrl — Tabelog URL selection for Japanese destinations
# ---------------------------------------------------------------------------

class TestChooseStartingUrl:

    def test_tokyo_uses_tabelog_url(self):
        url = _choose_starting_url(None, "sushi restaurant", "Tokyo")
        assert url == "https://tabelog.com/"

    def test_kyoto_uses_tabelog_url(self):
        url = _choose_starting_url("kaiseki restaurant", "dining", "Kyoto")
        assert url == "https://tabelog.com/"

    def test_sapporo_uses_tabelog_homepage(self):
        url = _choose_starting_url(None, "ramen shop", "Sapporo")
        assert url == "https://tabelog.com/"

    def test_non_japan_uses_google_maps(self):
        url = _choose_starting_url("bistro", "Le Petit", "Paris")
        assert "google.com/maps" in url
        assert "tabelog" not in url

    def test_japan_keyword_in_description_triggers_tabelog(self):
        # "ramen" is in _JAPAN_KEYWORDS — should route to Tabelog.
        url = _choose_starting_url("ramen restaurant", "Local Noodle Bar", None)
        assert url == "https://tabelog.com/"

    def test_japan_url_has_no_query_params(self):
        # Nova Act navigates the search UI itself — no pre-constructed query params.
        url = _choose_starting_url("famous sushi", "Sukiyabashi Jiro", "Tokyo")
        assert "?" not in url
        assert url == "https://tabelog.com/"

    def test_non_japan_url_contains_search_term(self):
        # Google Maps URL should embed the search term.
        url = _choose_starting_url("bistro", "Le Petit Bistro", "Paris")
        assert "google.com/maps" in url
        assert "Paris" in url or "bistro" in url.lower()

    def test_non_japan_generic_name_uses_description(self):
        # Non-Japan path: description is included in the Maps search query.
        url = _choose_starting_url("French cuisine fine dining", "Restaurant", "Lyon")
        assert "google.com/maps" in url
        assert "French" in url or "Lyon" in url


# ---------------------------------------------------------------------------
# TestHITLAuthPause — sign-in wall detection, resume, and timeout
# ---------------------------------------------------------------------------

_NOVA_CLS_PATCH = "services.nova_act.booking_agent._NovaAct"
_NOVA_AVAIL_PATCH = "services.nova_act.booking_agent._NOVA_ACT_AVAILABLE"


def _make_hitl_agent(resume_event=None) -> JapaneseRestaurantBookingAgent:
    return JapaneseRestaurantBookingAgent(
        restaurant_name="Sukiyabashi Jiro",
        restaurant_description="famous sushi",
        trip_location="Tokyo",
        date="2026-03-14",
        time="19:00",
        party_size=2,
        phone_number="+81-90-1234-5678",
        event_queue=queue.Queue(),
        booking_id="test-booking-id",
        resume_event=resume_event,
    )


def _drain(q: queue.Queue) -> list:
    items = []
    while not q.empty():
        items.append(q.get_nowait())
    return items


# ---------------------------------------------------------------------------
# TestNovaActProfileDir — user_data_dir is only passed when non-empty
# ---------------------------------------------------------------------------

@pytest.fixture
def japan_agent(tmp_path) -> JapaneseRestaurantBookingAgent:
    """A Japan-market agent backed by a real temp queue."""
    return JapaneseRestaurantBookingAgent(
        restaurant_name="Teppanyaki Restaurant",
        restaurant_description="teppanyaki",
        trip_location="Kyoto",
        date="2026-03-28",
        time="12:00",
        party_size=2,
        phone_number="+81-90-0000-0000",
        event_queue=queue.Queue(),
    )


class TestNovaActProfileDir:
    """user_data_dir must only be forwarded to _NovaAct when the profile dir has content."""

    @patch.dict(os.environ, {"NOVA_ACT_API_KEY": "test-key",
                             "NOVA_ACT_PROFILE_DIR": ""})
    @patch(_NOVA_AVAIL_PATCH, True)
    @patch(_NOVA_CLS_PATCH)
    def test_empty_profile_dir_omits_user_data_dir(self, mock_nova_cls, tmp_path, japan_agent):
        """An empty profile dir must NOT be forwarded — Nova Act rejects empty dirs."""
        os.environ["NOVA_ACT_PROFILE_DIR"] = str(tmp_path)  # tmp_path is empty

        mock_nova = MagicMock()
        mock_nova_cls.return_value = mock_nova
        mock_nova.start.return_value = None
        mock_nova.stop.return_value = None
        mock_nova.page = None  # skip screenshots / demo phases

        japan_agent.run()

        assert mock_nova_cls.called
        _, kwargs = mock_nova_cls.call_args
        assert "user_data_dir" not in kwargs, (
            "user_data_dir must not be passed when the profile directory is empty"
        )

    @patch.dict(os.environ, {"NOVA_ACT_API_KEY": "test-key",
                             "NOVA_ACT_PROFILE_DIR": ""})
    @patch(_NOVA_AVAIL_PATCH, True)
    @patch(_NOVA_CLS_PATCH)
    def test_populated_profile_dir_passes_user_data_dir(self, mock_nova_cls, tmp_path, japan_agent):
        """A non-empty profile dir IS forwarded so the saved login session is reused."""
        (tmp_path / "Default").mkdir()  # simulate a Chromium profile
        os.environ["NOVA_ACT_PROFILE_DIR"] = str(tmp_path)

        mock_nova = MagicMock()
        mock_nova_cls.return_value = mock_nova
        mock_nova.start.return_value = None
        mock_nova.stop.return_value = None
        mock_nova.page = None

        japan_agent.run()

        assert mock_nova_cls.called
        _, kwargs = mock_nova_cls.call_args
        assert "user_data_dir" in kwargs
        assert kwargs["user_data_dir"] == str(tmp_path)


class TestHITLAuthPause:

    @patch.dict(os.environ, {"NOVA_ACT_API_KEY": "test-key"})
    @patch(_NOVA_AVAIL_PATCH, True)
    @patch(_NOVA_CLS_PATCH)
    def test_sign_in_required_emits_needs_auth_event(self, mock_nova_cls):
        """When Nova Act returns SIGN_IN_REQUIRED, a needs_auth event is queued."""
        resume = threading.Event()
        resume.set()  # pre-set so the agent doesn't block waiting for user
        agent = _make_hitl_agent(resume_event=resume)

        mock_nova = MagicMock()
        mock_nova_cls.return_value = mock_nova
        mock_nova.act.side_effect = [
            "SIGN_IN_REQUIRED: blocked at http://example.com/login",
            "Booking confirmed! Confirmation: ABC123",
        ]
        mock_nova.page = None  # skip screenshots

        agent.run()

        events = _drain(agent.event_queue)
        needs_auth_events = [e for e in events if e.get("type") == "needs_auth"]
        assert len(needs_auth_events) == 1
        assert needs_auth_events[0]["message"] != ""

    @patch.dict(os.environ, {"NOVA_ACT_API_KEY": "test-key"})
    @patch(_NOVA_AVAIL_PATCH, True)
    @patch(_NOVA_CLS_PATCH)
    def test_resume_unblocks_thread(self, mock_nova_cls):
        """Pre-set resume event → agent completes Phase 2 without hanging."""
        resume = threading.Event()
        resume.set()
        agent = _make_hitl_agent(resume_event=resume)

        mock_nova = MagicMock()
        mock_nova_cls.return_value = mock_nova
        mock_nova.act.side_effect = [
            "SIGN_IN_REQUIRED: http://example.com/login",
            "Booking confirmed! Confirmation: XYZ999",
        ]
        mock_nova.page = None

        agent.run()  # must not hang

        events = _drain(agent.event_queue)
        done_events = [e for e in events if e.get("type") == "done"]
        assert len(done_events) == 1

    @patch.dict(os.environ, {"NOVA_ACT_API_KEY": "test-key"})
    @patch(_NOVA_AVAIL_PATCH, True)
    @patch(_NOVA_CLS_PATCH)
    def test_auth_timeout_emits_failure_result(self, mock_nova_cls):
        """Auth wait() returning False (timeout) emits a failure BookingResult."""
        agent = _make_hitl_agent()
        # Patch wait() to return False immediately, simulating a timeout.
        agent._resume_event.wait = MagicMock(return_value=False)

        mock_nova = MagicMock()
        mock_nova_cls.return_value = mock_nova
        mock_nova.act.return_value = "SIGN_IN_REQUIRED: http://example.com/login"
        mock_nova.page = None

        agent.run()

        events = _drain(agent.event_queue)
        result_events = [e for e in events if e.get("type") == "result"]
        assert len(result_events) == 1
        booking_result = result_events[0]["result"]
        assert booking_result.success is False
        assert "timeout" in (booking_result.error or "").lower()
