"""(#735) Claiming a platform_admin invitation writes a privilege-grant audit row.

The grant itself is already implemented (invitations.py, #130): when a
``platform_admin`` invitation is claimed, a dedicated ``admin_invitation.claim``
audit event records the new ``platform_role`` — and that action IS in
AUDITED_ACTIONS (guarded by test_audit_coverage, #129). What was missing is the
BEHAVIOURAL test (#735 acceptance criterion 3): that the claim flow end-to-end
actually produces the audit row, attributed to the promoted user.

Integration tier: needs the real Invitation/User/AuditLog tables (migrated_db).
The Telegram initData HMAC is bypassed via monkeypatch so we exercise the claim
+ audit logic without forging a signed payload.
"""
from __future__ import annotations

from datetime import UTC, datetime, timedelta
from types import SimpleNamespace

from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker


async def test_platform_admin_claim_writes_grant_audit(migrated_db, monkeypatch) -> None:
    from app.api.invitations import claim_invitation
    from app.models import AuditLog, Invitation, User

    # Bypass the Telegram initData HMAC — return a fixed Telegram user. The handler
    # imports verify_init_data at call time from this module, so patching here wins.
    tg_user = SimpleNamespace(
        id=778899, username="newadmin", first_name="New", last_name="Admin", photo_url=None,
    )
    import app.core.telegram_auth as tgauth
    monkeypatch.setattr(
        tgauth, "verify_init_data", lambda *a, **k: SimpleNamespace(user=tg_user)
    )

    Session = async_sessionmaker(migrated_db, expire_on_commit=False)
    async with Session() as s:
        s.add(Invitation(
            token="tok-platform-admin", tenant_id=None, kind="platform_admin",
            role="support", expires_at=datetime.now(UTC) + timedelta(days=1),
        ))
        await s.commit()

    # The account receiving a platform role must have PROVED which number it
    # belongs to. This claim used to carry the number in its body — a client
    # assertion about identity, accepted in the same request that grants the
    # privilege — so the proof is now established first, exactly as the bot's
    # contact-share does it.
    async with Session() as s:
        from app.services.telegram_contact import record_verified_contact

        s.add(User(telegram_id=778899))
        await s.flush()
        res = await record_verified_contact(
            s, sender_telegram_id=778899, contact_user_id=778899,
            raw_phone="+998901234567",
        )
        assert res.ok, res.outcome
        await s.commit()

    async with Session() as s:
        await claim_invitation(
            token="tok-platform-admin",
            body={"init_data": "stub", "first_name": "New"},
            db=s,
        )

    async with Session() as s:
        grants = (await s.execute(
            select(AuditLog).where(AuditLog.action == "admin_invitation.claim")
        )).scalars().all()
        assert len(grants) == 1, "platform_admin claim must write one privilege-grant audit row"
        row = grants[0]
        # The granted role is captured (not buried among generic claims).
        assert row.after_json["platform_role"] == "support"
        assert row.entity_type == "user"
        # Attributed to the promoted user.
        user = (await s.execute(
            select(User).where(User.telegram_id == 778899)
        )).scalar_one()
        assert str(row.actor_user_id) == str(user.id)
        assert user.platform_role is not None  # the privilege was actually granted
