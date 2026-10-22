"""DB-backed per-host circuit breaker."""

import asyncio
from collections.abc import Callable
from datetime import UTC, datetime

from sqlalchemy import delete
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.core.http import HostState, apply_status
from app.models import HostBlock


def _utcnow() -> datetime:
    return datetime.now(UTC)


class DbHostGuard:
    """Persists strikes/pauses in ``host_blocks``.

    Uses its own short sessions (never a transaction across network I/O) and keeps a small
    per-instance cache so a run reads each host once.
    """

    def __init__(
        self,
        session_factory: async_sessionmaker[AsyncSession],
        now: Callable[[], datetime] = _utcnow,
    ) -> None:
        self._sf = session_factory
        self._now = now
        self._cache: dict[str, HostState] = {}
        self._lock = asyncio.Lock()

    async def _state(self, host: str) -> HostState:
        state = self._cache.get(host)
        if state is None:
            async with self._sf() as session:
                row = await session.get(HostBlock, host)
            state = (
                HostState(row.strikes, row.blocked_until, row.last_status) if row else HostState()
            )
            self._cache[host] = state
        return state

    async def paused_until(self, host: str) -> datetime | None:
        state = await self._state(host)
        if state.blocked_until is not None and state.blocked_until > self._now():
            return state.blocked_until
        return None

    async def last_status(self, host: str) -> int | None:
        return (await self._state(host)).last_status

    async def record(self, host: str, status: int) -> None:
        """Persist only real changes: strikes / pause start, or clearing a struck host.

        Healthy hosts never get a row, and 200 <-> 304 flips write nothing.
        """
        async with self._lock:
            old = await self._state(host)
            new = apply_status(host, old, status, self._now())
            self._cache[host] = new  # last_status is tracked in memory even when not written
            if (new.strikes, new.blocked_until) == (old.strikes, old.blocked_until):
                return
            async with self._sf() as session:
                if new.strikes == 0:
                    await session.execute(delete(HostBlock).where(HostBlock.host == host))
                else:
                    values = {
                        "host": host,
                        "strikes": new.strikes,
                        "blocked_until": new.blocked_until,
                        "last_status": new.last_status,
                        "updated_at": self._now(),
                    }
                    stmt = pg_insert(HostBlock).values(**values)
                    stmt = stmt.on_conflict_do_update(
                        index_elements=["host"],
                        set_={k: v for k, v in values.items() if k != "host"},
                    )
                    await session.execute(stmt)
                await session.commit()
