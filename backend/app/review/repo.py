"""Review-state persistence — Postgres-backed.

Async repository functions over :class:`app.db_models.SectionStateRow` /
:class:`app.db_models.BankerEditRow`, translating to/from the pure, DB-free
:class:`app.review.workflow.ReviewState`/:class:`BankerEdit` models. A
request handler calls :func:`load_review_state` to get the same object shape
as before; mutations go through :func:`advance`/:func:`record_edit`.
"""

from __future__ import annotations

from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession
from sqlmodel import select

from app.db_models import BankerEditRow, SectionStateRow
from app.review.workflow import BankerEdit, ReviewState, SectionState

_ORDER = [SectionState.DRAFT, SectionState.REVIEWED, SectionState.CERTIFIED]


async def load_review_state(session: AsyncSession) -> ReviewState:
    section_rows = (await session.execute(select(SectionStateRow))).scalars().all()
    edit_rows = (
        (await session.execute(select(BankerEditRow).order_by(BankerEditRow.at)))
        .scalars()
        .all()
    )
    return ReviewState(
        states={row.entry_id: SectionState(row.state) for row in section_rows},
        audit_trail=[
            BankerEdit(
                entry_id=row.entry_id,
                editor=row.editor,
                before=row.before,
                after=row.after,
                at=row.at,
            )
            for row in edit_rows
        ],
    )


async def advance(session: AsyncSession, entry_id: str, to: SectionState) -> None:
    """Row-locked read-validate-write — the transition that must not lose an update.

    ``SELECT ... FOR UPDATE`` blocks a second concurrent ``advance()`` on the
    same ``entry_id`` until the first transaction commits (or the row lock is
    released on the ``ValueError`` rollback path), so the second request always
    validates against the post-commit state rather than a stale read.
    """
    await session.execute(
        insert(SectionStateRow)
        .values(entry_id=entry_id, state=SectionState.DRAFT.value)
        .on_conflict_do_nothing(index_elements=["entry_id"])
    )
    row = (
        await session.execute(
            select(SectionStateRow).where(SectionStateRow.entry_id == entry_id).with_for_update()
        )
    ).scalar_one()
    current = SectionState(row.state)
    if _ORDER.index(to) != _ORDER.index(current) + 1:
        await session.rollback()
        raise ValueError(f"{entry_id}: cannot move {current} → {to}; states advance one step")
    row.state = to.value
    session.add(row)
    await session.commit()


async def record_edit(session: AsyncSession, edit: BankerEdit) -> None:
    """Append to the audit trail; any banker edit demotes the section back to draft."""
    session.add(
        BankerEditRow(
            entry_id=edit.entry_id,
            editor=edit.editor,
            before=edit.before,
            after=edit.after,
            at=edit.at,
        )
    )
    await session.execute(
        insert(SectionStateRow)
        .values(entry_id=edit.entry_id, state=SectionState.DRAFT.value)
        .on_conflict_do_update(
            index_elements=["entry_id"], set_={"state": SectionState.DRAFT.value}
        )
    )
    await session.commit()
