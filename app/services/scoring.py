from datetime import datetime
from zoneinfo import ZoneInfo

from app.models.contact import Contact

DEFAULT_WINDOW_START = "09:00"
DEFAULT_WINDOW_END = "20:00"
DEFAULT_RECENCY_DAYS = 7


def compute_category_gap_scores(contacts: list[Contact]) -> dict[str, float]:
    """
    For each category tag, find the least-recently-contacted member (by last_called).
    The score for a category = days since that member was last called.
    Returns dict[str, float] mapping category -> gap score.
    """
    # Collect all tags across all contacts
    all_tags: set[str] = set()
    for contact in contacts:
        for tag in contact.tags:
            all_tags.add(tag)

    now = datetime.now(tz=ZoneInfo("UTC"))
    gap_scores: dict[str, float] = {}

    for tag in all_tags:
        members = [c for c in contacts if tag in c.tags]
        if not members:
            gap_scores[tag] = 0.0
            continue

        # Find the member called longest ago (or never called)
        max_gap = 0.0
        for member in members:
            if member.last_called is None:
                # Never called — treat as very large gap
                gap = float("inf")
            else:
                lc = member.last_called
                if lc.tzinfo is None:
                    lc = lc.replace(tzinfo=ZoneInfo("UTC"))
                gap = (now - lc).total_seconds() / 86400.0
            if gap > max_gap:
                max_gap = gap

        # Cap inf at a large but finite value for scoring purposes
        if max_gap == float("inf"):
            max_gap = 365.0

        gap_scores[tag] = max_gap

    return gap_scores


def compute_score(
    contact: Contact,
    now: datetime,
    category_gap_scores: dict[str, float],
) -> float:
    """
    score = days_since_last_spoken * 0.6 + category_gap_score * 0.3 + priority_boost * 0.1
    days_since_last_spoken is derived from contact.last_spoken (not last_called).
    """
    if contact.last_spoken is None:
        days_since_last_spoken = 365.0
    else:
        ls = contact.last_spoken
        if ls.tzinfo is None:
            ls = ls.replace(tzinfo=ZoneInfo("UTC"))
        n = now
        if n.tzinfo is None:
            n = n.replace(tzinfo=ZoneInfo("UTC"))
        days_since_last_spoken = (n - ls).total_seconds() / 86400.0

    # Use the highest category gap score among the contact's tags
    if contact.tags and category_gap_scores:
        category_gap_score = max(
            category_gap_scores.get(tag, 0.0) for tag in contact.tags
        )
    else:
        category_gap_score = 0.0

    return (
        days_since_last_spoken * 0.6
        + category_gap_score * 0.3
        + contact.priority_boost * 0.1
    )


def is_in_call_window(contact: Contact, now: datetime) -> bool:
    """
    Returns True if now (in contact's timezone) is within preferred_time_window,
    or within 09:00–20:00 if no preference is set.
    """
    try:
        tz = ZoneInfo(contact.timezone)
    except Exception:
        tz = ZoneInfo("UTC")

    local_now = now.astimezone(tz)
    local_time_str = local_now.strftime("%H:%M")

    if contact.preferred_time_window is not None:
        window_start = contact.preferred_time_window.start
        window_end = contact.preferred_time_window.end
    else:
        window_start = DEFAULT_WINDOW_START
        window_end = DEFAULT_WINDOW_END

    return window_start <= local_time_str < window_end


def get_top_contacts(
    contacts: list[Contact],
    now: datetime,
    max_results: int = 2,
    recency_days: int = DEFAULT_RECENCY_DAYS,
) -> list[Contact]:
    """
    Returns up to max_results contacts eligible for a call right now.

    Order of operations:
    1. Immediate callback override: contacts with next_call_at <= now are ALWAYS
       selected, bypassing the recency filter.
    2. Exclude contacts where call_started_at is not None (currently on a call).
    3. Recency filter: exclude contacts called within recency_days (based on last_called).
    4. Time-window filter: only include contacts whose local time is within their window.
    5. Category balancing: compute category_gap_scores.
    6. Score sort: sort by compute_score descending.
    7. Return top max_results.
    """
    n = now
    if n.tzinfo is None:
        n = n.replace(tzinfo=ZoneInfo("UTC"))

    # Step 1: Immediate callback overrides
    callback_contacts = [
        c for c in contacts
        if c.next_call_at is not None and _to_utc(c.next_call_at) <= n
    ]

    # Step 2: Exclude contacts currently on a call (from callback pool too)
    callback_contacts = [c for c in callback_contacts if c.call_started_at is None]

    # Remaining contacts (not in callback override)
    callback_ids = {c.contact_id for c in callback_contacts}
    remaining = [c for c in contacts if c.contact_id not in callback_ids]

    # Step 2 (remaining): Exclude contacts currently on a call
    remaining = [c for c in remaining if c.call_started_at is None]

    # Step 3: Recency filter
    def recently_called(c: Contact) -> bool:
        if c.last_called is None:
            return False
        lc = _to_utc(c.last_called)
        return (n - lc).total_seconds() / 86400.0 < recency_days

    remaining = [c for c in remaining if not recently_called(c)]

    # Step 4: Time-window filter
    remaining = [c for c in remaining if is_in_call_window(c, n)]

    # Step 5 & 6: Category balancing + score sort
    all_candidates = remaining  # callback contacts skip recency/time-window
    category_gap_scores = compute_category_gap_scores(all_candidates)
    all_candidates.sort(
        key=lambda c: compute_score(c, n, category_gap_scores),
        reverse=True,
    )

    # Callback contacts also need scoring for ordering among themselves
    cb_gap_scores = compute_category_gap_scores(callback_contacts)
    callback_contacts.sort(
        key=lambda c: compute_score(c, n, cb_gap_scores),
        reverse=True,
    )

    # Combine: callback overrides first, then scored remaining
    result = callback_contacts + all_candidates
    return result[:max_results]


def _to_utc(dt: datetime) -> datetime:
    if dt.tzinfo is None:
        return dt.replace(tzinfo=ZoneInfo("UTC"))
    return dt
