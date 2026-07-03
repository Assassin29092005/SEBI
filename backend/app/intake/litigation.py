"""Litigation lookup behind a connector protocol.

There is no clean free API over Indian court records, so the demo ships a
mock. A real integration is an adapter implementing ``LitigationConnector`` —
the seam is real even though the data is not.

The mock reads canned records from
``settings.data_dir / "demo_company" / "litigation_records.json"`` — the
same file the promoter journey demo cites. Missing file or unknown entity
returns an empty list; the seam is unchanged.
"""

from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Protocol

from pydantic import BaseModel

from app.config import settings

logger = logging.getLogger("drhp.intake.litigation")

_DEMO_ENTITY_SUBSTRING = "sunrise agrotech"


class LitigationRecord(BaseModel):
    case_number: str
    forum: str                 # court/tribunal name
    parties: str
    nature: str                # civil / criminal / tax / regulatory
    amount_involved_paise: int | None
    status: str


class LitigationConnector(Protocol):
    async def search(
        self, entity_name: str, identifiers: dict[str, str]
    ) -> list[LitigationRecord]:
        """Search proceedings by entity name and identifiers (CIN/PAN-format)."""
        ...


def _demo_records_path() -> Path:
    return settings.data_dir / "demo_company" / "litigation_records.json"


def _load_demo_records() -> list[LitigationRecord]:
    """Load and validate canned records from the demo fixtures directory."""
    path = _demo_records_path()
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        logger.warning("no demo litigation fixtures at %s", path)
        return []
    except json.JSONDecodeError as exc:
        logger.warning("invalid JSON in %s: %s", path, exc)
        return []
    if not isinstance(raw, list):
        logger.warning("expected a JSON array in %s, got %s", path, type(raw).__name__)
        return []
    return [LitigationRecord.model_validate(item) for item in raw]


class MockLitigationConnector:
    """Returns canned records for the synthetic demo company only.

    The seam matches ``LitigationConnector``: a real adapter would replace
    this class without touching any caller. The mock never touches the
    network and is safe to run in the offline demo.
    """

    async def search(
        self, entity_name: str, identifiers: dict[str, str]
    ) -> list[LitigationRecord]:
        if _DEMO_ENTITY_SUBSTRING not in entity_name.lower():
            return []
        return _load_demo_records()
