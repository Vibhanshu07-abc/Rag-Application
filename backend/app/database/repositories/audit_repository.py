import json
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from backend.app.database.models import AuditEventRecord


class AuditRepository:
    """Append-only audit persistence; callers own the transaction."""

    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def record(
        self, *, organization_id: str, actor_user_id: str | None, event_type: str,
        resource_type: str, resource_id: str | None, details: dict[str, Any] | None = None,
    ) -> None:
        self.session.add(AuditEventRecord(
            organization_id=organization_id, actor_user_id=actor_user_id,
            event_type=event_type, resource_type=resource_type, resource_id=resource_id,
            details_json=json.dumps(details, sort_keys=True) if details else None,
        ))
        await self.session.flush()
