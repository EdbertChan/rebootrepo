import unittest

from reboot.aio.applications import Application
from reboot.aio.auth.authorizers import allow
from reboot.aio.contexts import EffectValidation
from reboot.aio.tests import Reboot
from reboot.std.collections.ordered_map.v1.ordered_map import ordered_map_library

from predictions.v1.predictions_rbt import PaymentIntent
from servicers.predictions import (
    APPLICATION_SERVICERS,
    GatewayOutcome,
    PaymentIntentServicer,
    configure_gateway_for_tests,
    reset_gateway_for_tests,
)


class _PaymentIntentTestServicer(PaymentIntentServicer):
    def authorizer(self):
        return PaymentIntent.Authorizer(_default=allow())


class PaymentIntentExecutionTest(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self) -> None:
        self.rbt = Reboot()
        await self.rbt.start()
        await self.rbt.up(
            self.application(),
            effect_validation=EffectValidation.DISABLED,
        )
        self.context = await self.rbt.create_external_context_as(
            "payment-test",
            user_id="payment-test",
        )

    async def asyncTearDown(self) -> None:
        reset_gateway_for_tests()
        await self.rbt.stop()

    def application(self) -> Application:
        servicers = [
            _PaymentIntentTestServicer
            if servicer is PaymentIntentServicer
            else servicer
            for servicer in APPLICATION_SERVICERS
        ]
        return Application(
            servicers=servicers,
            libraries=[ordered_map_library()],
        )

    async def create_payment_intent(
        self,
        payment_intent_id: str,
        *,
        amount: int = 250,
    ):
        payment_ref, _ = await PaymentIntent.Create(
            self.context,
            payment_intent_id,
            user_id="user-1",
            market_id="market-1",
            bet_id="bet-1",
            amount=amount,
        )
        return payment_ref

    async def run_payment(self, payment_intent_id: str):
        payment_ref = await self.create_payment_intent(payment_intent_id)
        await payment_ref.run(self.context)
        return await payment_ref.get(self.context)

    async def test_success_records_single_attempt_and_idempotency_key(self) -> None:
        calls: list[tuple[str, int, str, int]] = []

        async def success(
            payment_intent_id: str,
            amount: int,
            idempotency_key: str,
            attempt_number: int,
        ) -> GatewayOutcome:
            calls.append((payment_intent_id, amount, idempotency_key, attempt_number))
            return GatewayOutcome(
                status="success",
                provider_transaction_id=f"provider:{payment_intent_id}",
            )

        configure_gateway_for_tests(success)

        payment = await self.run_payment("payment-success")

        self.assertEqual(payment.status, "succeeded")
        self.assertEqual(payment.failure_class, "")
        self.assertEqual(payment.idempotency_key, "payment-intent:payment-success")
        self.assertEqual(len(payment.attempts), 1)
        self.assertEqual(payment.attempts[0].status, "success")
        self.assertEqual(calls, [("payment-success", 250, payment.idempotency_key, 1)])

    async def test_timeout_then_success_records_retry_history(self) -> None:
        calls = 0

        async def timeout_then_success(
            payment_intent_id: str,
            amount: int,
            idempotency_key: str,
            attempt_number: int,
        ) -> GatewayOutcome:
            nonlocal calls
            del amount, idempotency_key
            calls += 1
            if attempt_number == 1:
                return GatewayOutcome(
                    status="retryable_failure",
                    failure_class="timeout",
                    message="gateway timed out",
                )
            return GatewayOutcome(
                status="success",
                provider_transaction_id=f"provider:{payment_intent_id}",
            )

        configure_gateway_for_tests(timeout_then_success)

        payment = await self.run_payment("payment-timeout-success")

        self.assertEqual(payment.status, "succeeded")
        self.assertEqual(calls, 2)
        self.assertEqual(
            [attempt.status for attempt in payment.attempts],
            ["retryable_failure", "success"],
        )
        self.assertEqual(payment.attempts[0].failure_class, "timeout")

    async def test_http_200_business_failure_body_is_terminal(self) -> None:
        async def business_failure(
            payment_intent_id: str,
            amount: int,
            idempotency_key: str,
            attempt_number: int,
        ) -> GatewayOutcome:
            del payment_intent_id, amount, idempotency_key, attempt_number
            return GatewayOutcome(
                status="business_failure",
                failure_class="recipient_rejected",
                message="HTTP 200: payout rejected by provider body",
            )

        configure_gateway_for_tests(business_failure)

        payment = await self.run_payment("payment-business-failure")

        self.assertEqual(payment.status, "failed")
        self.assertEqual(payment.failure_class, "recipient_rejected")
        self.assertEqual(len(payment.attempts), 1)
        self.assertEqual(payment.attempts[0].status, "business_failure")
        self.assertEqual(
            payment.attempts[0].message,
            "HTTP 200: payout rejected by provider body",
        )

    async def test_permanent_failure_is_terminal(self) -> None:
        async def permanent_failure(
            payment_intent_id: str,
            amount: int,
            idempotency_key: str,
            attempt_number: int,
        ) -> GatewayOutcome:
            del payment_intent_id, amount, idempotency_key, attempt_number
            return GatewayOutcome(
                status="permanent_failure",
                failure_class="declined",
                message="provider declined payout",
            )

        configure_gateway_for_tests(permanent_failure)

        payment = await self.run_payment("payment-permanent-failure")

        self.assertEqual(payment.status, "failed")
        self.assertEqual(payment.failure_class, "declined")
        self.assertEqual(len(payment.attempts), 1)
        self.assertEqual(payment.attempts[0].status, "permanent_failure")

    async def test_duplicate_idempotency_key_replays_gateway_result(self) -> None:
        seen: dict[str, GatewayOutcome] = {}
        keys: list[str] = []

        async def replay_by_idempotency_key(
            payment_intent_id: str,
            amount: int,
            idempotency_key: str,
            attempt_number: int,
        ) -> GatewayOutcome:
            del amount, attempt_number
            keys.append(idempotency_key)
            if idempotency_key not in seen:
                seen[idempotency_key] = GatewayOutcome(
                    status="success",
                    provider_transaction_id=f"provider:{payment_intent_id}:once",
                )
            return seen[idempotency_key]

        configure_gateway_for_tests(replay_by_idempotency_key)
        payment_ref = await self.create_payment_intent("payment-replay")

        await payment_ref.run(self.context)
        await payment_ref.record_status(
            self.context,
            status="pending",
            failure_class="",
        )
        await payment_ref.run(self.context)

        payment = await payment_ref.get(self.context)

        self.assertEqual(payment.status, "succeeded")
        self.assertEqual(keys, ["payment-intent:payment-replay"] * 2)
        self.assertEqual(
            [attempt.provider_transaction_id for attempt in payment.attempts],
            ["provider:payment-replay:once", "provider:payment-replay:once"],
        )
