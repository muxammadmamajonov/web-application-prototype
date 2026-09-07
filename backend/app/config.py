"""Application settings — loaded from .env at the repo root or process env."""
from __future__ import annotations

import logging
from functools import lru_cache
from pathlib import Path

from pydantic import Field, field_validator, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

REPO_ROOT = Path(__file__).resolve().parents[2]
ENV_FILE = REPO_ROOT / ".env"


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=str(ENV_FILE),
        env_file_encoding="utf-8",
        case_sensitive=False,
        extra="ignore",
    )

    # runtime
    environment: str = Field(default="development")
    project_name: str = Field(default="Muddato")
    secret_key: str = Field(default="change-me")
    # (#133) Comma-separated PREVIOUS SECRET_KEYs (decrypt-only) for zero-downtime
    # key rotation: set the old key here + the new one in secret_key, deploy, then
    # re-encrypt with crypto.rotate_secret before retiring the old key.
    secret_key_previous: str | None = Field(default=None)
    # (#F-21) Key separation. Historically one SECRET_KEY both *signed* session JWTs
    # and *derived* the Fernet key for payment secrets, so a single leak would forge
    # tokens AND decrypt every tenant's provider keys. These two optional keys split
    # those duties. Each FALLS BACK to secret_key when unset, so leaving them unset is
    # byte-identical to the old single-key behaviour (existing JWTs still verify,
    # existing Fernet payloads still decrypt). In production they SHOULD be set and
    # distinct from each other and from secret_key — see _enforce_production_secrets.
    #   * jwt_signing_key  → signs/verifies the session JWT (core/security.py)
    #   * encryption_key   → derives the Fernet key for secrets at rest (core/crypto.py)
    # The existing MultiFernet rotation still applies: secret_key_previous holds the
    # prior ENCRYPTION key(s) for decrypt-only, whether that was secret_key (pre-split)
    # or an earlier encryption_key.
    jwt_signing_key: str | None = Field(default=None)
    encryption_key: str | None = Field(default=None)
    log_level: str = Field(default="info")
    # (#82) The reminder/billing scheduler runs in a dedicated worker process
    # (app/worker.py). Web machines set RUN_SCHEDULER=false so they don't each
    # boot APScheduler; default True keeps a single-process / dev deploy working.
    run_scheduler: bool = Field(default=True)
    # Late fees stay dormant behind an explicit opt-in until the legal and
    # collection semantics are approved.
    enable_late_fees: bool = Field(default=False)

    # urls
    frontend_url: str = Field(default="http://localhost:5173")
    backend_url: str = Field(default="http://localhost:8000")
    allowed_origins: str = Field(default="http://localhost:5173")

    # ── database credentials: two identities, never one ──────────────────────
    #
    # (Batch 5 correction) There are TWO database identities and they are not
    # interchangeable. Before this correction there was one — the schema owner —
    # and every ordinary request connected as it, downgrading itself with
    # ``SET LOCAL ROLE`` at BEGIN. That made the isolation guarantee depend on a
    # single SQLAlchemy event listener: anything that opened a connection
    # outside it (a health check, a direct ``engine.connect()``, a library, a
    # future CLI) ran as the owner and bypassed row security entirely.
    #
    #   DATABASE_URL / DATABASE_URL_SYNC
    #     The ORDINARY APPLICATION LOGIN (``muddato_api``). LOGIN, NOSUPERUSER,
    #     NOBYPASSRLS, NOINHERIT; owns no table; cannot CREATE in ``public``.
    #     NOINHERIT is load-bearing: it is a member of the five authority roles
    #     so it may ``SET ROLE`` to them, but it holds none of their privileges
    #     until it does — a raw connection therefore reads NOTHING, rather than
    #     silently inheriting ``muddato_worker``'s cross-tenant grants. API, bot,
    #     worker, scheduler, health checks and ordinary CLI jobs all use this.
    #
    #   MIGRATION_DATABASE_URL / MIGRATION_DATABASE_URL_SYNC
    #     The SCHEMA OWNER. Alembic, release migrations and tightly controlled
    #     operator tools only. Never the API, bot, worker or health check.
    #
    # Alembic needs only the *sync* form; the async one exists so an operator
    # tool can share the same credential without a second env var. See
    # ``migration_url_sync`` for the resolution order and the production guard.
    database_url: str = Field(...)        # asyncpg — restricted application login
    database_url_sync: str = Field(...)   # psycopg — restricted application login
    migration_database_url: str | None = Field(default=None)       # asyncpg — owner
    migration_database_url_sync: str | None = Field(default=None)  # psycopg — owner
    #: The login role DATABASE_URL is expected to authenticate as. The preflight
    #: asserts the live connection actually is this role, so a URL quietly
    #: repointed at the owner fails the deploy instead of serving traffic.
    db_runtime_role: str = Field(default="muddato_api")
    supabase_project_ref: str | None = Field(default=None)

    # (#96) Receipt image storage. "postgres" (default) keeps bytes in
    # receipt_blobs.data — fine at low volume, simple, no extra infra. "supabase"
    # moves the bytes to a *private* Supabase Storage bucket (only a reference +
    # the HMAC capability URL stay in Postgres) so the primary DB / WAL / backups
    # don't bloat at scale. Activation = set RECEIPT_STORAGE_BACKEND=supabase +
    # SUPABASE_SERVICE_KEY, then run scripts/backfill_receipts_to_object_store.py.
    receipt_storage_backend: str = Field(default="postgres")  # postgres | supabase
    supabase_url: str | None = Field(default=None)            # else derived from project_ref
    supabase_service_key: str | None = Field(default=None)    # service_role key (server-only)
    receipt_bucket: str = Field(default="receipts")

    # Connection-pool budget (env-driven so it can be tuned per deploy). The
    # global budget is roughly
    #   web_machines x (db_pool_size + db_max_overflow)  +  worker connections
    # and MUST stay under the Supabase pooler connection ceiling. See fly.toml
    # and docs/RUNBOOK.md for the budget math.
    db_pool_size: int = Field(default=5)
    db_max_overflow: int = Field(default=5)
    # Hard ceilings so one slow query / stuck lock can't pile up connections.
    # Enforced client-side via asyncpg command_timeout (pooler-safe) always, and
    # server-side (statement_timeout / lock_timeout) on direct connections.
    db_statement_timeout_ms: int = Field(default=30_000)
    db_lock_timeout_ms: int = Field(default=10_000)

    # redis
    redis_url: str = Field(...)

    # telegram
    telegram_bot_token: str = Field(...)
    telegram_bot_username: str = Field(default="MuddatoBot")
    # (MUDD-76) How long a Mini-App ``initData`` stays acceptable. Was an
    # implicit 24h, i.e. how long a captured initData could be replayed as its
    # owner. The login path now also spends each one exactly once
    # (app/core/initdata_replay.py), so this bounds only the residue: paths not
    # wired to the seen-store, and the gap if the store is emptied. A launch
    # presents its initData within seconds — an hour is already slack.
    # Keep in step with ``telegram_auth.INITDATA_MAX_AGE_SECONDS`` (that module
    # stays settings-free by design, so the number is stated in both places and
    # pinned equal by a test).
    telegram_initdata_max_age_seconds: int = Field(default=3_600)
    telegram_login_client_id: str | None = Field(default=None)
    telegram_login_client_secret: str | None = Field(default=None)

    # (MUDD-71 L1) The Telegram channel both public forms (contact + demo request)
    # post leads into. This was a bare module constant in
    # app/services/support_channel.py, so pointing a second environment — staging,
    # a fork, a different owner — at its own channel meant editing source. It is a
    # deployment fact, not a code fact. The default is the live Muddato channel, so
    # an unset SUPPORT_CHANNEL_ID is byte-identical to the previous behaviour.
    support_channel_id: int = Field(default=-1001234567890)

    # (#814) Telegram-group media storage. When enabled, tenant media (payment
    # proofs + generic files) is sent as documents into a single private FORUM
    # supergroup — one topic per tenant, platform-owner media in topic 1 — and only
    # references are kept in media_files. OFF by default, so the existing receipt
    # storage path is unchanged. See docs/TELEGRAM-MEDIA.md.
    telegram_media_enabled: bool = Field(default=False)
    # The forum supergroup chat id — MUST be the -100… supergroup form (e.g. raw
    # 4295764583 → -1001234567891). Unset → the media path errors (so the flag and
    # the id must be set together).
    telegram_media_chat_id: int | None = Field(default=None)
    # getFile download ceiling. A bot can SEND up to 50 MB but can only DOWNLOAD
    # ≤20 MB, so anything larger could never be proxied back — hard-cap at 20 MB.
    telegram_media_max_bytes: int = Field(default=20 * 1024 * 1024)
    # "dual_write" (default) keeps a durable copy in the existing private receipt
    # store (source of truth for financial proofs) alongside the Telegram mirror;
    # "telegram_only" keeps references only (cheapest). See docs/TELEGRAM-MEDIA.md.
    media_fallback_mode: str = Field(default="dual_write")  # dual_write | telegram_only

    # payments (per-tenant in DB; these are only platform-level fallbacks)
    click_merchant_id: str | None = Field(default=None)
    click_service_id: str | None = Field(default=None)
    click_secret_key: str | None = Field(default=None)
    payme_merchant_id: str | None = Field(default=None)
    payme_secret_key: str | None = Field(default=None)

    # SMS fallback (Round 25 — Eskiz.uz). Env-driven; sandbox creds added to
    # .env later. When email/password are unset the SMS path is a no-op and
    # the reminder cron silently falls back to Telegram-only.
    eskiz_email: str | None = Field(default=None)
    eskiz_password: str | None = Field(default=None)
    eskiz_base_url: str = Field(default="https://notify.eskiz.uz/api")
    # Default numeric sender (Eskiz test code is "4546"). Pro+ tenants can
    # override with a registered alpha-name via tenant_settings.sms_sender_alpha.
    eskiz_default_sender: str = Field(default="4546")
    # Estimated cost per SMS part in so'm — used for budget accounting (Eskiz
    # bills per 160-char GSM part / 70-char unicode part).
    sms_cost_per_part_uzs: int = Field(default=45)

    # archive.org IAS3 keys for hosting global catalog media (brand logos,
    # category/model images). Set as Fly secrets ARCHIVE_S3_ACCESS / _SECRET —
    # never committed. When unset, the in-app upload endpoint returns 503 and
    # admins can still paste an external URL.
    archive_s3_access: str | None = Field(default=None)
    archive_s3_secret: str | None = Field(default=None)

    sentry_dsn: str | None = Field(default=None)

    # (F-08 fix) Shared secret the Cloudflare Pages /api proxy presents (X-Proxy-Secret)
    # alongside the real end-user IP (X-Real-Client-IP, taken from the edge
    # CF-Connecting-IP). The rate limiter trusts that IP ONLY when this secret
    # matches, so a request hitting Fly directly can't spoof it. MUST be set to the
    # SAME value as the Pages project's PROXY_SHARED_SECRET, else every request keys
    # on Cloudflare's shared egress IP and login globally rate-limits. See
    # app/core/ratelimit.py:_client_ip and frontend/functions/api/[[path]].ts.
    proxy_shared_secret: str | None = Field(default=None)

    # cross-tenant risk network (Round 23) — salt for hashing JSHSHIR before
    # it crosses the tenant boundary. MUST be set & kept stable in prod;
    # rotating it orphans every existing signal hash.
    risk_hash_salt: str = Field(default="muddato-risk-dev-salt")

    # (#105) Optional shared secret for triggering cron jobs via the X-Cron-Secret
    # header (e.g. an external scheduler). When set, POST /admin/cron/run requires
    # it in addition to a super-admin caller.
    cron_secret: str | None = Field(default=None)

    # (#95, rewritten for Batch 5) Transaction-level authority + scope.
    #
    # NOT the isolation boundary. Since the Batch 5 correction the boundary is the
    # CREDENTIAL: DATABASE_URL is a restricted, non-owning, NOBYPASSRLS login that
    # inherits nothing, so it reads no protected table at all until a transaction
    # assumes an authority role. This flag controls the layer ABOVE that — whether
    # each transaction issues `SET LOCAL ROLE <authority>` plus the app.tenant_id /
    # app.user_id / app.invitation_id GUCs (app/db.py, migrations rf0/rg0).
    #
    # With it OFF the restricted login still cannot read anything, so the failure
    # mode is "denied", not "leaked". It is OFF by default so the unit tier and a
    # single-role developer database keep working; production sets it true.
    rls_enabled: bool = Field(default=False)

    # (#F-01) RLS strict mode. Escalates "RLS_ENABLED is false in production" from a
    # CRITICAL log into a boot-blocking ValueError, so config drift (a dropped env
    # var) cannot silently downgrade a production machine to credential-only
    # isolation. Production sets it; dev/test are unaffected. The credential split
    # itself is enforced unconditionally by _enforce_database_identity_split above
    # and by `python -m app.cli.rls_preflight` at release time.
    rls_strict: bool = Field(default=False)

    # (#70 follow-up) Port the dedicated worker process exposes its Prometheus
    # metrics on. Cron-health metrics live in the worker (the scheduler runs
    # there, not in the web app), so Fly's [metrics] scrape needs them served from
    # that machine too — the web app already serves /metrics via uvicorn on the
    # same port. Set to 0 to disable the worker's metrics server.
    metrics_port: int = Field(default=8000)

    # Dead-man's switch. The URL of an external heartbeat monitor
    # (Healthchecks.io / BetterStack Heartbeats / UptimeRobot cron monitor —
    # any of them, free tier). The scheduler GETs it on an interval, but ONLY
    # while `readiness()` says this process can serve; see
    # app/services/heartbeat.py for why silence is the alarm.
    #
    # Unset disables the job entirely — correct for local dev, CI and review
    # apps, which must not hold a production switch open.
    heartbeat_url: str | None = Field(default=None)
    # Ping cadence. Set the EXTERNAL service's grace period to several times
    # this (5 min ping -> ~15-20 min grace) so one dropped ping — a blip on the
    # way out, a redeploy — does not page anyone, while a real outage still
    # surfaces inside half an hour.
    heartbeat_interval_seconds: int = Field(default=300)

    @field_validator("allowed_origins")
    @classmethod
    def _normalise_origins(cls, v: str) -> str:
        return v.strip()

    @property
    def allowed_origins_list(self) -> list[str]:
        return [o.strip() for o in self.allowed_origins.split(",") if o.strip()]

    @property
    def is_production(self) -> bool:
        return self.environment.lower() == "production"

    @property
    def supabase_base_url(self) -> str | None:
        """Base URL of the Supabase project — explicit ``supabase_url`` wins,
        else derived from the project ref (``https://<ref>.supabase.co``)."""
        if self.supabase_url:
            return self.supabase_url.rstrip("/")
        if self.supabase_project_ref:
            return f"https://{self.supabase_project_ref}.supabase.co"
        return None

    @staticmethod
    def _url_user(url: str | None) -> str | None:
        """The login role in a SQLAlchemy URL, or None. Never returns a password."""
        if not url:
            return None
        try:
            from sqlalchemy.engine import make_url

            return make_url(url).username
        except Exception:
            return None

    @staticmethod
    def _to_sync(url: str) -> str:
        """Rewrite an asyncpg URL onto the psycopg driver Alembic speaks."""
        return url.replace("+asyncpg", "+psycopg")

    @property
    def migration_url_sync(self) -> str:
        """The credential Alembic must use, or a hard error.

        Resolution order, deliberately explicit:

        1. ``MIGRATION_DATABASE_URL_SYNC``
        2. ``MIGRATION_DATABASE_URL`` with the driver rewritten to psycopg
        3. **production: raise.** A missing migration credential must not
           silently fall back to the ordinary runtime login — that would either
           fail mid-DDL with a confusing privilege error or, on a cluster where
           the two were the same role, quietly restore the very posture this
           correction removes. Outside production it falls back to
           ``DATABASE_URL_SYNC`` with a CRITICAL log, so a developer's laptop
           and the unit tier keep working with one URL.
        """
        if self.migration_database_url_sync:
            return self.migration_database_url_sync
        if self.migration_database_url:
            return self._to_sync(self.migration_database_url)
        if self.is_production:
            raise RuntimeError(
                "MIGRATION_DATABASE_URL_SYNC (or MIGRATION_DATABASE_URL) is not "
                "set. Migrations must run as the schema owner, and the ordinary "
                "DATABASE_URL is a restricted login that cannot perform DDL — "
                "there is deliberately no fallback. Set the migration credential "
                "as a deploy secret; see docs/RLS.md."
            )
        logging.getLogger("muddato.config").critical(
            "MIGRATION_DATABASE_URL is unset — falling back to DATABASE_URL_SYNC "
            "for migrations. This is allowed OUTSIDE production only; production "
            "refuses to start Alembic without an explicit migration credential.",
        )
        return self.database_url_sync

    @property
    def migration_credential_is_separate(self) -> bool:
        """True when the migration URL names a different login than the runtime one."""
        mig = self.migration_database_url_sync or self.migration_database_url
        if not mig:
            return False
        return self._url_user(mig) != self._url_user(self.database_url)

    @model_validator(mode="after")
    def _enforce_database_identity_split(self) -> Settings:
        """Production must not run ordinary traffic as the schema owner.

        Two boot blockers, both cheap and both about the same mistake:

        * the migration credential is missing entirely — the deploy would run
          Alembic as the restricted login and fail mid-DDL, or (worse) succeed
          because someone pointed DATABASE_URL back at the owner;
        * the two URLs name the SAME login — the split exists only on paper.

        Neither check opens a connection; both read the URL's username only, and
        nothing here is ever logged with credentials attached.
        """
        if not self.is_production:
            return self
        mig = self.migration_database_url_sync or self.migration_database_url
        if not mig:
            raise ValueError(
                "Refusing to start in production: MIGRATION_DATABASE_URL_SYNC "
                "(or MIGRATION_DATABASE_URL) is unset. The ordinary DATABASE_URL "
                "must be a restricted, non-owning login, so migrations need their "
                "own schema-owner credential — see docs/RLS.md."
            )
        if not self.migration_credential_is_separate:
            raise ValueError(
                "Refusing to start in production: DATABASE_URL and the migration "
                "URL authenticate as the SAME role. Ordinary runtime must not "
                "connect as the schema/migration owner — provision the restricted "
                f"login {self.db_runtime_role!r} and point DATABASE_URL at it."
            )
        return self

    @model_validator(mode="after")
    def _enforce_production_secrets(self) -> Settings:
        """Guard against insecure production config.

        Two values block boot in production. ``secret_key`` signs every session
        JWT (and derives the bot webhook secret), so a leftover ``change-me``
        lets anyone forge a token for any user. ``risk_hash_salt`` keys the
        cross-tenant risk HMAC (#72); its dev default is public, so leaving it
        would make the 14-digit JSHSHIR space trivially reversible. A merely
        *short* secret_key is logged at CRITICAL but doesn't block, so an
        already-running deploy isn't bricked by config that is sub-optimal
        rather than forgeable.

        Two later checks are advisory by default so the *current* live deploy keeps
        booting: the RLS backstop being off logs CRITICAL (and becomes a hard
        ValueError only under ``rls_strict``, #F-01); and unset/duplicate
        ``jwt_signing_key`` / ``encryption_key`` warn but never block (#F-21).
        """
        if not self.is_production:
            return self

        if self.secret_key in {"", "change-me"}:
            raise ValueError(
                "Refusing to start in production: SECRET_KEY is unset or the "
                "default 'change-me' — set a 48-byte urlsafe random value."
            )

        log = logging.getLogger("muddato.config")
        if len(self.secret_key) < 32:
            log.critical(
                "SECRET_KEY is shorter than 32 chars in production — rotate to "
                "a 48-byte urlsafe random value (current length=%d).",
                len(self.secret_key),
            )
        if self.risk_hash_salt in {"", "muddato-risk-dev-salt"}:
            raise ValueError(
                "Refusing to start in production: RISK_HASH_SALT is unset or the "
                "public dev default — it keys the cross-tenant risk HMAC; set a "
                "stable random secret value."
            )
        # (#97) CORS sends credentials, so a wildcard / localhost / empty origin
        # list in production would let any (or a dev) origin make credentialed
        # requests. Refuse to boot on that.
        origins = self.allowed_origins_list
        bad = [o for o in origins if o == "*" or "localhost" in o or "127.0.0.1" in o]
        if not origins or bad:
            raise ValueError(
                "Refusing to start in production: ALLOWED_ORIGINS must be a "
                "non-empty list of explicit origins with no '*' and no localhost "
                f"(got: {origins or '[]'})."
            )
        # (#124) RLS is the DB-level tenant-isolation backstop (#95); it is INERT
        # unless RLS_ENABLED=true (the app then runs tenant work under the
        # restricted muddato_app role). Warn loudly — but don't block — so a
        # missing env var can't silently drop the second isolation layer, while a
        # deploy on a DB where the role isn't provisioned yet still boots. See the
        # RLS cutover in docs/RUNBOOK.md.
        if not self.rls_enabled:
            # (#F-01) With RLS_STRICT on, the missing backstop is a hard boot failure
            # (config drift can't silently drop the second isolation layer); otherwise
            # it stays a loud-but-non-blocking CRITICAL so the current live deploy —
            # whose DB may not have the muddato_app role provisioned yet — still boots.
            msg = (
                "RLS_ENABLED is false in production — the database-level tenant "
                "isolation backstop (#95) is INERT. Set RLS_ENABLED=true once the "
                "muddato_app role + policies are verified live (docs/RUNBOOK.md + "
                "scripts/bootstrap_runtime_login.py)."
            )
            if self.rls_strict:
                raise ValueError(
                    "Refusing to start in production: " + msg + " RLS_STRICT=true "
                    "requires the backstop to be enabled."
                )
            log.critical(msg)
        # (#F-21) Key separation is advisory, not boot-blocking: unset keys fall back
        # to secret_key (identical to the historical single-key behaviour), so an
        # already-running deploy is never bricked. But in production these SHOULD be
        # set and distinct so a leak of one key can't both forge JWTs and decrypt
        # payment secrets. Warn (don't block) when they're absent or not distinct.
        if not self.jwt_signing_key or not self.encryption_key:
            log.warning(
                "JWT_SIGNING_KEY / ENCRYPTION_KEY are not both set in production — "
                "falling back to SECRET_KEY for the unset one(s). Set distinct values "
                "so a single key leak can't both forge sessions and decrypt payment "
                "secrets (#F-21)."
            )
        elif len({self.jwt_signing_key, self.encryption_key, self.secret_key}) < 3:
            log.warning(
                "JWT_SIGNING_KEY, ENCRYPTION_KEY and SECRET_KEY should be three "
                "distinct values in production (#F-21) — at least two currently match."
            )
        return self


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    return Settings()  # type: ignore[call-arg]


settings = get_settings()
