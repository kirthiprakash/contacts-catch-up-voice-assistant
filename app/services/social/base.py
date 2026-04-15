from abc import ABC, abstractmethod
from datetime import datetime, UTC

from pydantic import BaseModel, Field

from app.models.contact import Contact


class SocialUpdate(BaseModel):
    platform: str
    text: str
    timestamp: datetime = Field(default_factory=lambda: datetime.now(UTC))


class SocialAdapterBase(ABC):
    platform: str

    @abstractmethod
    async def fetch_updates(self, contact: Contact) -> list[SocialUpdate]:
        raise NotImplementedError
