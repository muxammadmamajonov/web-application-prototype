"""(#175) Load / stress harness for the highest-risk PUBLIC endpoints: the
unauthenticated payment-provider + bot webhooks, and the customer payment-initiate.

Run against STAGING — never production — once a staging env exists (#177):

    uv run --with locust locust -f tests/load/locustfile.py --host https://staging.muddato.uz
    # then open http://localhost:8089 and set users / spawn-rate
    # headless example:
    # uv run --with locust locust -f tests/load/locustfile.py --host https://staging.muddato.uz \
    #     --users 200 --spawn-rate 20 --run-time 2m --headless

The webhook tasks send UNSIGNED / wrong-secret payloads on purpose: they exercise the
signature-verify + early-reject path (the real attack/DoS surface) at volume WITHOUT
settling any money. A 400/401/200-ack response is success here — we're measuring the
endpoint's throughput + latency under load, not business outcomes. Watch the Fly
metrics (connection pool, p95) + that /ready stays green (it now gates routing — #146).

payment-initiate is authenticated; set MUDDATO_LOAD_TOKEN to a STAGING customer JWT to
include it, otherwise that task is skipped.
"""
from __future__ import annotations

import os

from locust import HttpUser, between, task

_TOKEN = os.environ.get("MUDDATO_LOAD_TOKEN", "")

# A minimal Telegram update + a deliberately-wrong webhook secret → the handler does
# its constant-time secret check (#132) and rejects. Exercises the public surface.
_TG_UPDATE = {"update_id": 1, "message": {"message_id": 1, "text": "/start",
              "chat": {"id": 1, "type": "private"}, "from": {"id": 1, "is_bot": False, "first_name": "L"}}}


class PublicEndpoints(HttpUser):
    wait_time = between(0.1, 1.0)

    @task(4)
    def click_prepare(self) -> None:
        # Click sends form-encoded params; a missing/wrong sign_string is rejected
        # after the signature check runs (the load we want to measure).
        self.client.post(
            "/api/webhooks/click/prepare",
            data={"click_trans_id": "1", "service_id": "1", "merchant_trans_id": "load",
                  "amount": "1000.00", "action": "0", "sign_time": "2026-01-01 00:00:00",
                  "sign_string": "deadbeef"},
            name="POST /api/webhooks/click/prepare",
        )

    @task(2)
    def payme_rpc(self) -> None:
        # Payme JSON-RPC with a dummy Basic header → 401 after auth check.
        self.client.post(
            "/api/webhooks/payme",
            json={"method": "CheckPerformTransaction",
                  "params": {"amount": 100000, "account": {"installment_id": "load"}}, "id": 1},
            headers={"Authorization": "Basic UGF5Y29tOmxvYWQtdGVzdA=="},
            name="POST /api/webhooks/payme",
        )

    @task(2)
    def bot_webhook(self) -> None:
        self.client.post(
            "/api/bot/webhook",
            json=_TG_UPDATE,
            headers={"X-Telegram-Bot-Api-Secret-Token": "wrong-secret-load-test"},
            name="POST /api/bot/webhook",
        )

    @task(1)
    def payment_initiate(self) -> None:
        if not _TOKEN:
            return  # set MUDDATO_LOAD_TOKEN (staging customer JWT) to include this
        self.client.post(
            "/api/me/payments/initiate",
            json={"installment_id": "00000000-0000-0000-0000-000000000000", "provider": "click"},
            headers={"Authorization": f"Bearer {_TOKEN}"},
            name="POST /api/me/payments/initiate",
        )
