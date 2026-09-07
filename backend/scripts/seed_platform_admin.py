"""Idempotently grant platform super-admin to a Telegram user.

Usage:
    uv run python -m scripts.seed_platform_admin <telegram_id> [first_name]
"""
from __future__ import annotations

import asyncio
import sys

from sqlalchemy import select

from app.db import SessionLocal, engine
from app.models import User
from app.models.enums import PlatformRole


async def main(telegram_id: int, first_name: str | None = None) -> None:
    async with SessionLocal() as db:
        user = (await db.execute(select(User).where(User.telegram_id == telegram_id))).scalar_one_or_none()
        if user is None:
            user = User(
                telegram_id=telegram_id,
                first_name=first_name or "Muddato",
                platform_role=PlatformRole.super_admin,
                is_active=True,
                locale="uz",
            )
            db.add(user)
            action = "created"
        else:
            user.platform_role = PlatformRole.super_admin
            user.is_active = True
            action = "promoted"
        await db.commit()
        await db.refresh(user)
        print(f"{action}: user_id={user.id} telegram_id={user.telegram_id} role={user.platform_role}")
    await engine.dispose()


if __name__ == "__main__":
    if len(sys.argv) < 2:
        print("usage: seed_platform_admin <telegram_id> [first_name]", file=sys.stderr)
        sys.exit(2)
    asyncio.run(main(int(sys.argv[1]), sys.argv[2] if len(sys.argv) > 2 else None))
