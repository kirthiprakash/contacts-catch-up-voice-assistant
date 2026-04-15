"""
Unit and property-based tests for the scoring engine.
Feature: contacts-catch-up-voice-assistant
"""
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

import pytest
from hypothesis import given, settings, assume
from hypothesis import strategies as st

from app.models.contact import Contact, TimeWindow
from app.services.scoring import (
    compute_category_gap_scores,
    compute_score,
    get_top_contacts,
    is_in_call_window,
)

UTC = ZoneInfo("UTC")

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def make_contact(
    *,
    name: str = "Alice",
    tags: list[str] | None = None,
    timezone: str = "UTC",
    last_called: datetime | None = None,
    last_spoken: datetime | None = None,
    next_call_at: datetime | None = None,
    priority_boost: float = 0.0,
    preferred_time_window: TimeWindow | None = None,
    call_started_at: datetime | None = None,
) -> Contact:
    return Contact(
        name=name,
        phone="+12125550001",
        tags=tags or [],
        timezone=timezone,
        last_called=last_called,
        last_spoken=last_spoken,
        next_call_at=next_call_at,
        priority_boost=priority_boost,
        preferred_time_window=preferred_time_window,
        call_started_at=call_started_at,
    )


NOW = datetime(2024, 6, 15, 14, 0, 0, tzinfo=UTC)  # 14:00 UTC on a Saturday


# ---------------------------------------------------------------------------
# Unit tests: compute_score
# ---------------------------------------------------------------------------

class TestComputeScore:
    def test_known_inputs_produce_known_output(self):
        """score = days_since_last_spoken * 0.6 + category_gap * 0.3 + boost * 0.1"""
        last_spoken = NOW - timedelta(days=10)
        contact = make_contact(last_spoken=last_spoken, priority_boost=5.0)
        gap_scores = {"friends": 3.0}
        score = compute_score(contact, NOW, gap_scores)
        # days_since_last_spoken = 10, category_gap = 0 (no tags), boost = 5
        expected = 10 * 0.6 + 0.0 * 0.3 + 5.0 * 0.1
        assert abs(score - expected) < 1e-9

    def test_uses_last_spoken_not_last_called(self):
        """Verify days_since_last_spoken is derived from last_spoken, not last_called."""
        last_spoken = NOW - timedelta(days=5)
        last_called = NOW - timedelta(days=1)  # more recent — should be ignored
        contact = make_contact(last_spoken=last_spoken, last_called=last_called)
        score = compute_score(contact, NOW, {})
        expected = 5 * 0.6 + 0.0 * 0.3 + 0.0 * 0.1
        assert abs(score - expected) < 1e-9

    def test_no_last_spoken_defaults_to_365_days(self):
        contact = make_contact(last_spoken=None)
        score = compute_score(contact, NOW, {})
        expected = 365 * 0.6
        assert abs(score - expected) < 1e-9

    def test_category_gap_score_uses_highest_tag(self):
        contact = make_contact(tags=["work", "friends"], last_spoken=NOW - timedelta(days=2))
        gap_scores = {"work": 10.0, "friends": 20.0}
        score = compute_score(contact, NOW, gap_scores)
        expected = 2 * 0.6 + 20.0 * 0.3 + 0.0 * 0.1
        assert abs(score - expected) < 1e-9

    def test_priority_boost_contributes(self):
        contact = make_contact(last_spoken=NOW - timedelta(days=0), priority_boost=10.0)
        score = compute_score(contact, NOW, {})
        expected = 0.0 * 0.6 + 0.0 * 0.3 + 10.0 * 0.1
        assert abs(score - expected) < 1e-9


# ---------------------------------------------------------------------------
# Unit tests: is_in_call_window
# ---------------------------------------------------------------------------

class TestIsInCallWindow:
    def test_within_default_window(self):
        # 14:00 UTC, contact in UTC → within 09:00–20:00
        contact = make_contact(timezone="UTC")
        assert is_in_call_window(contact, NOW) is True

    def test_outside_default_window_early(self):
        # 07:00 UTC
        early = datetime(2024, 6, 15, 7, 0, 0, tzinfo=UTC)
        contact = make_contact(timezone="UTC")
        assert is_in_call_window(contact, early) is False

    def test_outside_default_window_late(self):
        # 21:00 UTC
        late = datetime(2024, 6, 15, 21, 0, 0, tzinfo=UTC)
        contact = make_contact(timezone="UTC")
        assert is_in_call_window(contact, late) is False

    def test_at_window_start_is_included(self):
        # Exactly 09:00 UTC
        at_start = datetime(2024, 6, 15, 9, 0, 0, tzinfo=UTC)
        contact = make_contact(timezone="UTC")
        assert is_in_call_window(contact, at_start) is True

    def test_at_window_end_is_excluded(self):
        # Exactly 20:00 UTC — end is exclusive
        at_end = datetime(2024, 6, 15, 20, 0, 0, tzinfo=UTC)
        contact = make_contact(timezone="UTC")
        assert is_in_call_window(contact, at_end) is False

    def test_preferred_time_window_respected(self):
        window = TimeWindow(start="18:00", end="22:00")
        contact = make_contact(timezone="UTC", preferred_time_window=window)
        # 19:00 UTC → inside window
        inside = datetime(2024, 6, 15, 19, 0, 0, tzinfo=UTC)
        assert is_in_call_window(contact, inside) is True
        # 14:00 UTC → outside window
        assert is_in_call_window(contact, NOW) is False

    def test_timezone_conversion(self):
        # Contact is in America/New_York (UTC-4 in summer)
        # NOW = 14:00 UTC = 10:00 New York → within 09:00–20:00
        contact = make_contact(timezone="America/New_York")
        assert is_in_call_window(contact, NOW) is True

    def test_timezone_conversion_outside(self):
        # 02:00 UTC = 22:00 New York (previous day) → outside 09:00–20:00
        night_utc = datetime(2024, 6, 15, 2, 0, 0, tzinfo=UTC)
        contact = make_contact(timezone="America/New_York")
        assert is_in_call_window(contact, night_utc) is False


# ---------------------------------------------------------------------------
# Unit tests: get_top_contacts
# ---------------------------------------------------------------------------

class TestGetTopContacts:
    def test_immediate_callback_override_bypasses_recency(self):
        """A contact with next_call_at <= now is selected even if recently called."""
        recent_call = NOW - timedelta(hours=1)
        contact = make_contact(
            name="Bob",
            last_called=recent_call,
            last_spoken=recent_call,
            next_call_at=NOW - timedelta(minutes=5),
        )
        result = get_top_contacts([contact], NOW)
        assert contact in result

    def test_recency_filter_excludes_recently_called(self):
        """Contact called 2 days ago (within 7-day window) is excluded."""
        recent = NOW - timedelta(days=2)
        contact = make_contact(last_called=recent, last_spoken=recent)
        result = get_top_contacts([contact], NOW)
        assert contact not in result

    def test_recency_filter_allows_old_calls(self):
        """Contact called 10 days ago (outside 7-day window) is included."""
        old = NOW - timedelta(days=10)
        contact = make_contact(last_called=old, last_spoken=old)
        result = get_top_contacts([contact], NOW)
        assert contact in result

    def test_time_window_filter_excludes_out_of_window(self):
        """Contact whose local time is outside their window is excluded."""
        # NOW = 14:00 UTC; contact window is 20:00–22:00 UTC
        window = TimeWindow(start="20:00", end="22:00")
        contact = make_contact(preferred_time_window=window)
        result = get_top_contacts([contact], NOW)
        assert contact not in result

    def test_time_window_filter_includes_in_window(self):
        """Contact whose local time is inside their window is included."""
        # NOW = 14:00 UTC; window 09:00–20:00 (default)
        old = NOW - timedelta(days=10)
        contact = make_contact(last_called=old, last_spoken=old)
        result = get_top_contacts([contact], NOW)
        assert contact in result

    def test_result_bounded_by_max_results(self):
        """Never returns more than max_results contacts."""
        old = NOW - timedelta(days=10)
        contacts = [
            make_contact(name=f"Person{i}", last_called=old, last_spoken=old)
            for i in range(5)
        ]
        result = get_top_contacts(contacts, NOW, max_results=2)
        assert len(result) <= 2

    def test_empty_input_returns_empty(self):
        assert get_top_contacts([], NOW) == []

    def test_excludes_contacts_currently_on_call(self):
        """Contacts with call_started_at set are excluded."""
        old = NOW - timedelta(days=10)
        on_call = make_contact(
            name="OnCall",
            last_called=old,
            last_spoken=old,
            call_started_at=NOW - timedelta(minutes=5),
        )
        result = get_top_contacts([on_call], NOW)
        assert on_call not in result

    def test_higher_score_contact_ranked_first(self):
        """Contact with higher score appears before lower-score contact."""
        old = NOW - timedelta(days=10)
        low_score = make_contact(name="Low", last_called=old, last_spoken=NOW - timedelta(days=1))
        high_score = make_contact(name="High", last_called=old, last_spoken=NOW - timedelta(days=30))
        result = get_top_contacts([low_score, high_score], NOW)
        assert result[0] == high_score

    def test_callback_override_excluded_if_on_call(self):
        """Callback contact that is currently on a call is still excluded."""
        contact = make_contact(
            name="OnCallCallback",
            next_call_at=NOW - timedelta(minutes=5),
            call_started_at=NOW - timedelta(minutes=3),
        )
        result = get_top_contacts([contact], NOW)
        assert contact not in result


# ---------------------------------------------------------------------------
# Property-based tests
# ---------------------------------------------------------------------------

# Hypothesis strategies
phone_strategy = st.just("+12125550001")
timezone_strategy = st.sampled_from(["UTC", "America/New_York", "Europe/London", "Asia/Tokyo"])
tag_strategy = st.lists(st.sampled_from(["work", "friends", "family", "mentor"]), max_size=3)

past_dt_strategy = st.datetimes(
    min_value=datetime(2020, 1, 1),
    max_value=datetime(2024, 6, 14, 23, 59, 59),
    timezones=st.just(UTC),
)

contact_strategy = st.builds(
    Contact,
    name=st.text(min_size=1, max_size=20, alphabet=st.characters(whitelist_categories=("Lu", "Ll"))),
    phone=phone_strategy,
    tags=tag_strategy,
    timezone=timezone_strategy,
    last_called=st.one_of(st.none(), past_dt_strategy),
    last_spoken=st.one_of(st.none(), past_dt_strategy),
    next_call_at=st.one_of(st.none(), past_dt_strategy),
    priority_boost=st.floats(min_value=0.0, max_value=10.0, allow_nan=False, allow_infinity=False),
    call_started_at=st.none(),
)


# Property 1: Scoring formula is exact
# Feature: contacts-catch-up-voice-assistant, Property 1: Scoring formula is exact
@given(
    days=st.floats(min_value=0.0, max_value=365.0, allow_nan=False, allow_infinity=False),
    gap=st.floats(min_value=0.0, max_value=365.0, allow_nan=False, allow_infinity=False),
    boost=st.floats(min_value=0.0, max_value=10.0, allow_nan=False, allow_infinity=False),
)
@settings(max_examples=100)
def test_property_1_scoring_formula_exact(days, gap, boost):
    """
    **Validates: Requirements 2.1**
    For any contact with known days_since_last_spoken, category_gap_score, and priority_boost,
    compute_score returns exactly days*0.6 + gap*0.3 + boost*0.1.
    """
    last_spoken = NOW - timedelta(days=days)
    contact = make_contact(
        tags=["work"],
        last_spoken=last_spoken,
        priority_boost=boost,
    )
    gap_scores = {"work": gap}
    score = compute_score(contact, NOW, gap_scores)
    expected = days * 0.6 + gap * 0.3 + boost * 0.1
    assert abs(score - expected) < 1e-6


# Property 2: Immediate callback always wins (overrides recency filter)
# Feature: contacts-catch-up-voice-assistant, Property 2: Immediate callback always wins
@given(
    recent_hours=st.floats(min_value=0.1, max_value=6.9, allow_nan=False),  # within 7-day recency
)
@settings(max_examples=100)
def test_property_2_immediate_callback_override(recent_hours):
    """
    **Validates: Requirements 2.2**
    A contact with next_call_at <= now is always selected, even if recently called.
    """
    recent_call = NOW - timedelta(hours=recent_hours)
    callback_contact = make_contact(
        name="Callback",
        last_called=recent_call,
        last_spoken=recent_call,
        next_call_at=NOW - timedelta(minutes=1),
    )
    # Add a non-callback contact that would normally score higher
    old = NOW - timedelta(days=30)
    other = make_contact(name="Other", last_called=old, last_spoken=old)
    result = get_top_contacts([callback_contact, other], NOW)
    assert callback_contact in result


# Property 3: Recency filter excludes recently-called contacts
# Feature: contacts-catch-up-voice-assistant, Property 3: Recency filter
@given(
    days_ago=st.floats(min_value=0.0, max_value=6.99, allow_nan=False),
)
@settings(max_examples=100)
def test_property_3_recency_filter(days_ago):
    """
    **Validates: Requirements 2.3**
    A contact called within the recency window (and no callback override) is excluded.
    """
    recent_call = NOW - timedelta(days=days_ago)
    contact = make_contact(
        last_called=recent_call,
        last_spoken=recent_call,
        next_call_at=None,
    )
    result = get_top_contacts([contact], NOW)
    assert contact not in result


# Property 4: Category gap score ordering
# Feature: contacts-catch-up-voice-assistant, Property 4: Category gap score ordering
@given(
    days_a=st.floats(min_value=1.0, max_value=100.0, allow_nan=False),
    days_b=st.floats(min_value=1.0, max_value=100.0, allow_nan=False),
)
@settings(max_examples=100)
def test_property_4_category_gap_ordering(days_a, days_b):
    """
    **Validates: Requirements 2.4**
    The category whose least-recently-contacted member was called longest ago
    receives the highest category_gap_score.
    """
    assume(abs(days_a - days_b) > 0.01)

    contact_a = make_contact(name="A", tags=["alpha"], last_called=NOW - timedelta(days=days_a))
    contact_b = make_contact(name="B", tags=["beta"], last_called=NOW - timedelta(days=days_b))

    gap_scores = compute_category_gap_scores([contact_a, contact_b])

    if days_a > days_b:
        assert gap_scores["alpha"] > gap_scores["beta"]
    else:
        assert gap_scores["beta"] > gap_scores["alpha"]


# Property 5: Time window filter
# Feature: contacts-catch-up-voice-assistant, Property 5: Time window filter
@given(
    hour=st.integers(min_value=0, max_value=23),
    minute=st.integers(min_value=0, max_value=59),
)
@settings(max_examples=100)
def test_property_5_time_window_filter(hour, minute):
    """
    **Validates: Requirements 2.5, 2.6**
    is_in_call_window returns False when local time is outside the window.
    """
    test_time = datetime(2024, 6, 15, hour, minute, 0, tzinfo=UTC)
    contact = make_contact(timezone="UTC")  # no preferred window → default 09:00–20:00

    result = is_in_call_window(contact, test_time)
    time_str = f"{hour:02d}:{minute:02d}"

    if "09:00" <= time_str < "20:00":
        assert result is True
    else:
        assert result is False


# Property 6: Result set size bounded
# Feature: contacts-catch-up-voice-assistant, Property 6: Result set size bounded
@given(contacts=st.lists(contact_strategy, min_size=0, max_size=10))
@settings(max_examples=100)
def test_property_6_result_size_bounded(contacts):
    """
    **Validates: Requirements 2.7**
    get_top_contacts always returns between 0 and 2 contacts (inclusive).
    """
    result = get_top_contacts(contacts, NOW)
    assert 0 <= len(result) <= 2
