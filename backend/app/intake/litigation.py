"""Litigation lookup behind a connector protocol.

There is no clean free API over Indian court records, so the demo ships a
mock. A real integration is an adapter implementing ``LitigationConnector`` —
the seam is real even though the data is not.
"""

from __future__ import annotations

from typing import Protocol

from pydantic import BaseModel


class LitigationRecord(BaseModel):
    case_number: str
    forum: str                 # court/tribunal name
    parties: str
    nature: str                # civil / criminal / tax / regulatory
    amount_involved_paise: int | None
    status: str


class LitigationConnector(Protocol):
    async def search(self, entity_name: str, identifiers: dict[str, str]) -> list[LitigationRecord]:
        """Search proceedings by entity name and identifiers (CIN/PAN-format)."""
        ...


class MockLitigationConnector:
    """Returns canned records for the synthetic demo company only."""

    async def search(
        self, entity_name: str, identifiers: dict[str, str]
    ) -> list[LitigationRecord]:
        if "sunrise agrotech" not in entity_name.lower():
            return []
        # TODO: populate from data/demo_company/ fixtures
        return []
