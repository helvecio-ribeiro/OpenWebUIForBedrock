from __future__ import annotations

import time
import uuid
from typing import Optional

from open_webui.internal.db import Base, get_async_db_context
from pydantic import BaseModel, ConfigDict
from sqlalchemy import BigInteger, Column, Text, delete, select
from sqlalchemy.ext.asyncio import AsyncSession


class WebPanel(Base):
    __tablename__ = 'web_panel'

    id = Column(Text, primary_key=True, unique=True)
    user_id = Column(Text, nullable=False, index=True)
    title = Column(Text, nullable=False)
    url = Column(Text, nullable=False, default='')
    created_at = Column(BigInteger, nullable=False)
    updated_at = Column(BigInteger, nullable=False)


class WebPanelModel(BaseModel):
    id: str
    user_id: str
    title: str
    url: str
    created_at: int
    updated_at: int

    model_config = ConfigDict(from_attributes=True)


class WebPanelTable:
    async def create(self, user_id: str, title: str, url: str, db: Optional[AsyncSession] = None):
        async with get_async_db_context(db) as db:
            now = int(time.time())
            row = WebPanel(id=str(uuid.uuid4()), user_id=user_id, title=title, url=url, created_at=now, updated_at=now)
            db.add(row)
            await db.commit()
            await db.refresh(row)
            return WebPanelModel.model_validate(row)

    async def list(self, user_id: str, db: Optional[AsyncSession] = None):
        async with get_async_db_context(db) as db:
            result = await db.execute(
                select(WebPanel).where(WebPanel.user_id == user_id).order_by(WebPanel.updated_at.desc())
            )
            return [WebPanelModel.model_validate(row) for row in result.scalars().all()]

    async def get(self, panel_id: str, user_id: str, db: Optional[AsyncSession] = None):
        async with get_async_db_context(db) as db:
            result = await db.execute(select(WebPanel).where(WebPanel.id == panel_id, WebPanel.user_id == user_id))
            row = result.scalars().first()
            return WebPanelModel.model_validate(row) if row else None

    async def get_by_id(self, panel_id: str, db: Optional[AsyncSession] = None):
        async with get_async_db_context(db) as db:
            result = await db.execute(select(WebPanel).where(WebPanel.id == panel_id))
            row = result.scalars().first()
            return WebPanelModel.model_validate(row) if row else None

    async def update(self, panel_id: str, user_id: str, values: dict, db: Optional[AsyncSession] = None):
        async with get_async_db_context(db) as db:
            result = await db.execute(select(WebPanel).where(WebPanel.id == panel_id, WebPanel.user_id == user_id))
            row = result.scalars().first()
            if not row:
                return None
            for key, value in values.items():
                setattr(row, key, value)
            row.updated_at = int(time.time())
            await db.commit()
            await db.refresh(row)
            return WebPanelModel.model_validate(row)

    async def delete(self, panel_id: str, user_id: str, db: Optional[AsyncSession] = None) -> bool:
        async with get_async_db_context(db) as db:
            result = await db.execute(delete(WebPanel).where(WebPanel.id == panel_id, WebPanel.user_id == user_id))
            await db.commit()
            return bool(result.rowcount)

    async def delete_many(self, panel_ids: list[str], user_id: str, db: Optional[AsyncSession] = None) -> int:
        async with get_async_db_context(db) as db:
            result = await db.execute(delete(WebPanel).where(WebPanel.id.in_(panel_ids), WebPanel.user_id == user_id))
            await db.commit()
            return result.rowcount or 0


WebPanels = WebPanelTable()
