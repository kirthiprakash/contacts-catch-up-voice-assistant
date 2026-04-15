from app.models.contact import Contact
from app.models.memory import MemoryEntry
from app.services.qdrant import store_memory
from app.services.social.instagram import InstagramAdapter
from app.services.social.linkedin import LinkedInAdapter
from app.services.social.twitter import TwitterAdapter


async def ingest_social_updates(contact: Contact) -> list[str]:
    adapters = [TwitterAdapter(), InstagramAdapter(), LinkedInAdapter()]
    stored_ids: list[str] = []

    for adapter in adapters:
        updates = await adapter.fetch_updates(contact)
        for update in updates:
            entry = MemoryEntry(
                contact_id=contact.contact_id,
                type="social",
                text=f"[{update.platform}] {update.text}",
                timestamp=update.timestamp,
            )
            stored_ids.append(await store_memory(entry))

    return stored_ids
