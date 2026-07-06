"""Evaluation of conditional ``applicability`` against the fact store.

A checklist entry's ``applicability`` is either ``"always"`` or a named
condition of the form ``has_<fact_key_base>`` (e.g. ``has_group_companies``,
``has_convertibles``). The condition is true when the store holds at least one
confirmed, truthy fact for ``<fact_key_base>[]`` or ``<fact_key_base>``.

Unrecognised condition strings evaluate as APPLICABLE: for a disclosure
checklist, over-disclosing (asking for something possibly not required) is
safe, silently dropping a requirement is not.
"""

from __future__ import annotations

import logging

from app.facts import FactStore
from app.schema.models import ChecklistEntry

logger = logging.getLogger("drhp.schema.applicability")

_CONDITION_PREFIX = "has_"


def entry_applies(entry: ChecklistEntry, store: FactStore) -> bool:
    """True when the entry's disclosure requirement applies to this issuer."""
    condition = entry.applicability
    if condition == "always":
        return True
    if not condition.startswith(_CONDITION_PREFIX):
        logger.warning(
            "unknown applicability %r on %s — treating as applicable", condition, entry.id
        )
        return True
    base = condition[len(_CONDITION_PREFIX) :]
    for key in (f"{base}[]", base):
        for fact in store.confirmed_by_key(key):
            if fact.value:  # empty list / empty string / None → condition not met
                return True
    return False
