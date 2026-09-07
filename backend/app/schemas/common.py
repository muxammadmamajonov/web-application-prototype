from __future__ import annotations

from typing import Annotated

from fastapi import Query
from pydantic import BaseModel


class OkResponse(BaseModel):
    ok: bool = True
    message: str | None = None


# (#271) Shared pagination bounds. The *defaults* stay per-route (a literal on
# the param, e.g. `limit: LimitQ = 50`) since they legitimately differ; only the
# repeated min/max bounds are centralised here so a list endpoint can't drift
# into an unbounded (OOM/DoS) page size. See tests/test_pagination.py (#78).
LimitQ = Annotated[int, Query(ge=1, le=200)]      # standard list page size
LimitMaxQ = Annotated[int, Query(ge=1, le=100)]   # tighter cap (heavy per-row payloads)
OffsetQ = Annotated[int, Query(ge=0, le=100_000)]  # bounded offset

# Free-text list search. Every use is a leading-wildcard ``ILIKE`` over several
# columns, which no index can serve, so the *input* is where the work is capped.
# 120 characters is far beyond any real customer name, phone or contract number
# while keeping a single request's scan bounded by construction.
SearchQ = Annotated[str | None, Query(max_length=120)]
