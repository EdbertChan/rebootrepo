import asyncio
import unittest

from reboot.aio.applications import Application
from reboot.aio.contexts import EffectValidation
from reboot.aio.tests import Reboot
from reboot.std.collections.ordered_map.v1.ordered_map import ordered_map_library

from predictions.v1.predictions_rbt import User
from servicers.predictions import (
    APPLICATION_SERVICERS,
    GatewayOutcome,
    configure_gateway_for_tests,
    reset_gateway_for_tests,
)


class PaymentExecutionTest(unittest.IsolatedAsyncioTestCase):
    """Exercises PaymentIntent.run against a fake payment gateway.

    Market resolution is only used as the trigger that schedules a
    PaymentIntent workflow; these tests assert on PaymentIntent-local
    fields (status, failure_class, attempts, idempotency_key) plus the
    balance/market side effects the existing contract already wires up.
    """

    async def asyncSetUp(self) -> None:
        self.rbt = Reboot()
        await self.rbt.start()
        self.revision = await self.rbt.up(
            self.application(),
            effect_validation=EffectValidation.DISABLED,
        )
        self.alice_context = await self.rbt.create_external_context_as(
            "alice",
            user_id="alice",
        )
        self.bob_context = await self.rbt.create_external_context_as(
            "bob",
            user_id="bob",
        )
        self.alice = User.ref("alice")
        self.bob = User.ref("bob")

    async def asyncTearDown(self) -> None:
        reset_gateway_for_tests()
        await self.rbt.stop()

    def application(self) -> Application:
        return Application(
            servicers=APPLICATION_SERVICERS,
            libraries=[ordered_map_library()],
        )

    async def refresh_contexts(self) -> None:
        self.alice_context = await self.rbt.create_external_context_as(
            "alice",
            user_id="alice",
        )
        self.bob_context = await self.rbt.create_external_context_as(
            "bob",
            user_id="bob",
        )
        self.alice = User.ref("alice")
        self.bob = User.ref("bob")

    async def wait_for_payment_status(
        self,
        payment_intent_id: str,
        statuses: set[str],
        *,
        timeout_seconds: float = 20,
    ):
        deadline = asyncio.get_running_loop().time() + timeout_seconds
        while asyncio.get_running_loop().time() < deadline:
            dashboard = await self.bob.dashboard(self.bob_context)
            for payment in dashboard.payments:
                if (
                    payment.payment_intent_id == payment_intent_id
                    and payment.status in statuses
                ):
                    return dashboard, payment
            await asyncio.sleep(0.1)
        self.fail(
            f"Payment {payment_intent_id} did not reach one of {sorted(statuses)}."
        )

    async def _place_and_resolve_winning_bet(
        self,
        question: str,
        *,
        stake: int = 100,
    ) -> str:
        market = await self.alice.create_market(
            self.alice_context,
            question=question,
            close_after_seconds=0,
        )
        await self.bob.place_bet(
            self.bob_context,
            market_id=market.market_id,
            outcome="YES",
            stake=stake,
        )
        await self.alice.close_market(self.alice_context, market_id=market.market_id)
        await self.alice.resolve_market(
            self.alice_context,
            market_id=market.market_id,
            winning_outcome="YES",
        )
        dashboard = await self.bob.dashboard(self.bob_context)
        return dashboard.payments[0].payment_intent_id

    async def test_gateway_success_uses_deterministic_idempotency_key(self) -> None:
        seen_keys: list[str] = []

        async def succeed(
            payment_intent_id: str,
            amount: int,
            idempotency_key: str,
            attempt_number: int,
        ) -> GatewayOutcome:
            seen_keys.append(idempotency_key)
            return GatewayOutcome(
                status="success",
                provider_transaction_id=f"simulated:{payment_intent_id}",
                message="HTTP 200 charge accepted.",
            )

        configure_gateway_for_tests(succeed)
        payment_id = await self._place_and_resolve_winning_bet(
            "Will a clean charge succeed on the first attempt?",
        )

        dashboard, payment = await self.wait_for_payment_status(payment_id, {"succeeded"})

        self.assertEqual(dashboard.balance, 1100)
        self.assertEqual(payment.status, "succeeded")
        self.assertEqual(len(payment.attempts), 1)
        self.assertEqual(payment.attempts[0].status, "success")
        self.assertEqual(seen_keys, [f"payout:{payment_id}"])

    async def test_gateway_timeout_then_success(self) -> None:
        calls: list[int] = []

        async def timeout_then_success(
            payment_intent_id: str,
            amount: int,
            idempotency_key: str,
            attempt_number: int,
        ) -> GatewayOutcome:
            calls.append(attempt_number)
            if attempt_number == 1:
                return GatewayOutcome(
                    status="retryable_failure",
                    failure_class="timeout",
                    message="gateway did not respond before the deadline",
                )
            return GatewayOutcome(
                status="success",
                provider_transaction_id=f"simulated:{payment_intent_id}",
            )

        configure_gateway_for_tests(timeout_then_success)
        payment_id = await self._place_and_resolve_winning_bet(
            "Will a timeout be retried until it succeeds?",
        )

        dashboard, payment = await self.wait_for_payment_status(payment_id, {"succeeded"})

        self.assertEqual(dashboard.balance, 1100)
        self.assertEqual(payment.status, "succeeded")
        self.assertEqual(
            [attempt.status for attempt in payment.attempts],
            ["retryable_failure", "success"],
        )
        self.assertEqual(payment.attempts[0].failure_class, "timeout")
        self.assertGreaterEqual(len(calls), 2)

    async def test_gateway_http_200_business_failure_body_is_not_retried(self) -> None:
        # The gateway's HTTP call itself succeeds (transport-level 200), but
        # the response body encodes a business-level decline. That is not a
        # transient error, so it must behave like a permanent failure rather
        # than a timeout that gets retried.
        async def declined_at_business_layer(
            payment_intent_id: str,
            amount: int,
            idempotency_key: str,
            attempt_number: int,
        ) -> GatewayOutcome:
            return GatewayOutcome(
                status="permanent_failure",
                failure_class="business_decline",
                provider_transaction_id=f"simulated:{payment_intent_id}",
                message='HTTP 200 {"result": "declined", "reason": "risk_hold"}',
            )

        configure_gateway_for_tests(declined_at_business_layer)
        payment_id = await self._place_and_resolve_winning_bet(
            "Will an HTTP 200 business decline stop retries?",
        )

        dashboard, payment = await self.wait_for_payment_status(payment_id, {"failed"})

        self.assertEqual(dashboard.balance, 900)
        self.assertEqual(dashboard.markets[0].status, "review_required")
        self.assertEqual(payment.status, "failed")
        self.assertEqual(payment.failure_class, "business_decline")
        self.assertEqual(len(payment.attempts), 1)
        self.assertEqual(
            payment.attempts[0].provider_transaction_id,
            f"simulated:{payment_id}",
        )

    async def test_gateway_permanent_failure_stops_without_retrying(self) -> None:
        async def invalid_account(
            payment_intent_id: str,
            amount: int,
            idempotency_key: str,
            attempt_number: int,
        ) -> GatewayOutcome:
            return GatewayOutcome(
                status="permanent_failure",
                failure_class="invalid_account",
                message="destination account no longer exists",
            )

        configure_gateway_for_tests(invalid_account)
        payment_id = await self._place_and_resolve_winning_bet(
            "Will an invalid account stop the payout without retries?",
        )

        dashboard, payment = await self.wait_for_payment_status(payment_id, {"failed"})

        self.assertEqual(dashboard.balance, 900)
        self.assertEqual(dashboard.markets[0].status, "review_required")
        self.assertEqual(payment.status, "failed")
        self.assertEqual(payment.failure_class, "invalid_account")
        self.assertEqual(len(payment.attempts), 1)

    async def test_duplicate_idempotency_key_replay_does_not_double_charge(self) -> None:
        started = asyncio.Event()
        release = asyncio.Event()
        pre_crash_keys: list[str] = []

        async def stalls_before_confirming(
            payment_intent_id: str,
            amount: int,
            idempotency_key: str,
            attempt_number: int,
        ) -> GatewayOutcome:
            pre_crash_keys.append(idempotency_key)
            started.set()
            await release.wait()
            return GatewayOutcome(
                status="success",
                provider_transaction_id=f"simulated:{payment_intent_id}",
            )

        configure_gateway_for_tests(stalls_before_confirming)
        payment_id = await self._place_and_resolve_winning_bet(
            "Will a mid-attempt crash replay with the same idempotency key?",
        )

        await asyncio.wait_for(started.wait(), timeout=10)
        # Crash before the at-least-once gateway call's result is durably
        # recorded, then restart so the workflow replays that same attempt
        # instead of minting a new one.
        await self.rbt.down()
        release.set()

        reset_gateway_for_tests()
        replay_keys: list[str] = []

        async def replay_records_key(
            payment_intent_id: str,
            amount: int,
            idempotency_key: str,
            attempt_number: int,
        ) -> GatewayOutcome:
            replay_keys.append(idempotency_key)
            return GatewayOutcome(
                status="success",
                provider_transaction_id=f"simulated:{payment_intent_id}",
            )

        configure_gateway_for_tests(replay_records_key)
        await self.rbt.up(revision=self.revision)
        await self.refresh_contexts()

        dashboard, payment = await self.wait_for_payment_status(payment_id, {"succeeded"})
        await asyncio.sleep(0.2)
        after_replay_window = await self.bob.dashboard(self.bob_context)

        expected_key = f"payout:{payment_id}"
        self.assertEqual(dashboard.balance, 1100)
        self.assertEqual(after_replay_window.balance, 1100)
        self.assertEqual(payment.status, "succeeded")
        self.assertEqual(payment.idempotency_key, expected_key)
        # Exactly one attempt is ever recorded in durable state, proving the
        # replay reused the original attempt rather than appending a new one.
        self.assertEqual(len(payment.attempts), 1)
        self.assertEqual(pre_crash_keys, [expected_key])
        for key in replay_keys:
            self.assertEqual(key, expected_key)


if __name__ == "__main__":
    unittest.main()
