"""Fact persistence — Postgres-backed.

Async repository functions over :class:`app.db_models.FactRow`, translating
to/from the pure, DB-free :class:`app.facts.Fact`/:class:`app.facts.Provenance`
models. ``app.generate``/``app.validate``/``app.coverage``/``app.assemble``
never talk to Postgres directly — a request handler calls
:func:`load_fact_store` once and hands the resulting in-memory
:class:`app.facts.FactStore` to those modules exactly as before.
"""

from __future__ import annotations

from sqlalchemy.ext.asyncio import AsyncSession
from sqlmodel import select

from app.db_models import FactRow
from app.facts import Fact, FactStore, Provenance
from app.schema.models import Role


class FactNotFound(Exception):
    """Raised in place of the in-memory store's ``KeyError``."""


def _to_fact(row: FactRow) -> Fact:
    return Fact(
        fact_id=row.fact_id,
        key=row.key,
        value=row.value,
        provenance=Provenance(
            kind=row.provenance_kind,
            detail=row.provenance_detail,
            snippet=row.provenance_snippet,
            supersedes=row.provenance_supersedes,
            document_id=row.provenance_document_id,
            page=row.provenance_page,
            source_file=row.provenance_source_file,
        ),
        confidence=row.confidence,
        confirmed=row.confirmed,
        supplied_by=row.supplied_by,
        corrected_by_role=Role(row.corrected_by_role) if row.corrected_by_role else None,
        created_at=row.created_at,
    )


def _to_row(fact: Fact) -> FactRow:
    return FactRow(
        fact_id=fact.fact_id,
        key=fact.key,
        value=fact.value,
        provenance_kind=fact.provenance.kind.value,
        provenance_detail=fact.provenance.detail,
        provenance_snippet=fact.provenance.snippet,
        provenance_supersedes=fact.provenance.supersedes,
        provenance_document_id=fact.provenance.document_id,
        provenance_page=fact.provenance.page,
        provenance_source_file=fact.provenance.source_file,
        confidence=fact.confidence,
        confirmed=fact.confirmed,
        supplied_by=fact.supplied_by.value,
        corrected_by_role=fact.corrected_by_role.value if fact.corrected_by_role else None,
        created_at=fact.created_at,
    )


async def add(session: AsyncSession, fact: Fact) -> Fact:
    session.add(_to_row(fact))
    await session.commit()
    return fact


async def confirm(session: AsyncSession, fact_id: str) -> Fact:
    row = await session.get(FactRow, fact_id)
    if row is None:
        raise FactNotFound(fact_id)
    row.confirmed = True
    session.add(row)
    await session.commit()
    return _to_fact(row)


async def correct(
    session: AsyncSession,
    fact_id: str,
    new_value: object,
    provenance: Provenance,
    *,
    corrected_by_role: Role | None = None,
) -> Fact:
    """Corrections never mutate: a new version supersedes the old one."""
    old = await session.get(FactRow, fact_id)
    if old is None:
        raise FactNotFound(fact_id)
    replacement = Fact(
        key=old.key,
        value=new_value,
        provenance=provenance.model_copy(update={"supersedes": fact_id}),
        supplied_by=old.supplied_by,
        corrected_by_role=corrected_by_role,
    )
    return await add(session, replacement)


async def get(session: AsyncSession, fact_id: str) -> Fact:
    row = await session.get(FactRow, fact_id)
    if row is None:
        raise FactNotFound(fact_id)
    return _to_fact(row)


async def all_facts(session: AsyncSession) -> list[Fact]:
    """Every fact ever stored — confirmed, unconfirmed, and superseded alike."""
    rows = (
        (await session.execute(select(FactRow).order_by(FactRow.created_at, FactRow.fact_id)))
        .scalars()
        .all()
    )
    return [_to_fact(row) for row in rows]


async def load_fact_store(session: AsyncSession) -> FactStore:
    """Rebuild an in-memory :class:`FactStore` from every row in the table.

    Hands ``generate``/``validate``/``coverage``/``assemble`` an object with
    the exact interface they already use (``confirmed_by_key``,
    ``all_confirmed``, ...) — the "confirmed and not superseded" filtering
    logic stays in :class:`FactStore` itself, computed once per request.
    """
    store = FactStore()
    for fact in await all_facts(session):
        store.add(fact)
    return store
