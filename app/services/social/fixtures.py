from datetime import datetime, UTC, timedelta

from app.models.contact import Contact
from app.services.social.base import SocialUpdate


def _ts(days_ago: int) -> datetime:
    return datetime.now(UTC) - timedelta(days=days_ago)


FIXTURES: dict[str, dict[str, list[SocialUpdate]]] = {
    "twitter": {
        "__default__": [
            SocialUpdate(platform="twitter", text="Shared a quick life update.", timestamp=_ts(2)),
        ],
        "alice": [
            SocialUpdate(platform="twitter", text="Posted about starting a new role.", timestamp=_ts(1)),
        ],
    },
    "instagram": {
        "__default__": [
            SocialUpdate(platform="instagram", text="Shared a weekend photo dump.", timestamp=_ts(3)),
        ],
        "alice": [
            SocialUpdate(platform="instagram", text="Posted hiking photos from a recent trip.", timestamp=_ts(2)),
        ],
    },
    "linkedin": {
        "__default__": [
            SocialUpdate(platform="linkedin", text="Reacted to an industry post.", timestamp=_ts(4)),
        ],
        "alice": [
            SocialUpdate(platform="linkedin", text="Announced a promotion and thanked the team.", timestamp=_ts(1)),
        ],
    },
}


def get_fixture_updates(contact: Contact, platform: str) -> list[SocialUpdate]:
    platform_fixtures = FIXTURES.get(platform, {})
    return platform_fixtures.get(contact.name.lower(), platform_fixtures.get("__default__", []))
