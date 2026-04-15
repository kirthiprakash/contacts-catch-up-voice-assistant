from app.models.contact import Contact
from app.services.social.base import SocialAdapterBase, SocialUpdate
from app.services.social.fixtures import get_fixture_updates


class InstagramAdapter(SocialAdapterBase):
    platform = "instagram"

    async def fetch_updates(self, contact: Contact) -> list[SocialUpdate]:
        return get_fixture_updates(contact, self.platform)
