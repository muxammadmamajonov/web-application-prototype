# --- builder ---
# Pin the base image to a specific patch + distro (3.12.13 on Debian bookworm)
# instead of the floating `python:3.12-slim` tag, so rebuilds are reproducible
# and a silently-republished `3.12-slim` can't shift our toolchain underneath us.
# (#153) Digest-pinned: the tag stays for readability, the @sha256 freezes the EXACT
# image bytes so a silently-republished tag can't shift our toolchain underneath us.
# Refresh: docker buildx imagetools inspect python:3.12.13-slim-bookworm --format '{{.Manifest.Digest}}'
FROM python:3.12.13-slim-bookworm@sha256:d50fb7611f86d04a3b0471b46d7557818d88983fc3136726336b2a4c657aa30b AS builder

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    UV_LINK_MODE=copy \
    UV_PYTHON_DOWNLOADS=never

RUN apt-get update \
    # MUDD-118: CVE-2026-56408 — libexpat1 in the pinned bookworm base lags a
    # security patch (2.5.0-1+deb12u2 -> ...u3). apt-get install alone won't
    # touch a package already satisfied by the base image, so upgrade it
    # explicitly rather than waiting on the next base-image digest refresh.
    && apt-get upgrade -y libexpat1 \
    && apt-get install -y --no-install-recommends \
        build-essential curl ca-certificates \
        # WeasyPrint (PDF generation, Round 19) build deps:
        # pango + cairo render glyphs; libffi + shared objects for cffi.
        libpango-1.0-0 libpangoft2-1.0-0 libcairo2 libffi-dev \
        # Fonts — DejaVu has comprehensive Cyrillic + Latin (Uzbek lotin/kirill),
        # Noto for fallback glyphs. Keeps the PDF render deterministic across
        # builds (no "font not found" defaults).
        fonts-dejavu fonts-noto-core fonts-noto-cjk-extra \
    && rm -rf /var/lib/apt/lists/*

# Install uv (fast Python package manager). Pinned to a concrete patch (0.11.18)
# rather than a floating minor tag so the resolver/build is deterministic.
# (#153) Digest-pinned (refresh: docker buildx imagetools inspect ghcr.io/astral-sh/uv:0.11.18 --format '{{.Manifest.Digest}}').
# 0.11+ is required because pyproject.toml uses uv's relative `exclude-newer`
# supply-chain cooldown; older releases ignored that setting after a parse warning.
COPY --from=ghcr.io/astral-sh/uv:0.11.18@sha256:78bc42400d77b0678ba95765305c826652ed5431f399257271dda681d0318f03 /uv /uvx /usr/local/bin/

WORKDIR /app
COPY pyproject.toml uv.lock ./
# Fail closed on lock drift. The previous `|| uv sync …` fallback meant a
# pyproject/uv.lock mismatch silently re-resolved unpinned versions inside the
# image — defeating the frozen install it was paired with. A drifted lock must
# break the build, not ship an unaudited dependency set.
RUN uv sync --frozen --no-dev --no-install-project

COPY . .
RUN uv sync --frozen --no-dev

# --- runtime ---
# Same digest-pin (#153) as the builder stage above.
FROM python:3.12.13-slim-bookworm@sha256:d50fb7611f86d04a3b0471b46d7557818d88983fc3136726336b2a4c657aa30b AS runtime
ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PATH="/app/.venv/bin:${PATH}"

RUN apt-get update \
    # MUDD-118: CVE-2026-56408, see builder stage above — this is the stage
    # Trivy actually scans (the shipped runtime image), so the fix has to
    # land here regardless of whether the builder stage also carries it.
    && apt-get upgrade -y libexpat1 \
    && apt-get install -y --no-install-recommends \
        libpq5 ca-certificates curl \
        # WeasyPrint runtime libs — mirrored from builder so the compiled
        # cffi extensions in the venv can dlopen pango/cairo.
        libpango-1.0-0 libpangoft2-1.0-0 libcairo2 \
        fonts-dejavu fonts-noto-core \
    && rm -rf /var/lib/apt/lists/*

# Run as a dedicated unprivileged user (defense-in-depth: a compromised process
# can't write outside /app or escalate as root). Create it *before* the COPY so
# the --chown below resolves the uid/gid. uvicorn binds 8000 (> 1024), so no
# privileged-port capability is needed.
RUN groupadd --system appuser && \
    useradd --system --gid appuser --no-create-home --home-dir /app appuser

WORKDIR /app
# --chown hands the app code + the .venv (which is on PATH) to appuser with the
# build artifact's existing read/execute bits, so the non-root user can import
# the venv and run the entrypoint.
COPY --from=builder --chown=appuser:appuser /app /app

USER appuser

EXPOSE 8000
HEALTHCHECK --interval=30s --timeout=5s --start-period=15s --retries=3 \
    CMD curl -fsS http://localhost:8000/health || exit 1

CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000", "--proxy-headers"]
