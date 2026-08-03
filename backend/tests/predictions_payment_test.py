import asyncio
import unittest

from reboot.aio.applications import Application
from reboot.aio.contexts import EffectValidation
from reboot.aio.tests import Reboot
from reboot.std.collections.ordered_map.v1.ordered_map import ordered_map_library

from predictions.v1.predictions_rbt import PaymentIntent
from servicers.predictions import (
    APPLICATION_SERVICERS,
    GatewayOutcome,
    configure_gateway_for_tests,
    reset_gateway_for_tests,
)


class PredictionPaymentIntentTest(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self) -> None:
        self.rbt = Reboot()
        await self.rbt.start()
        await self.rbt.up(
            self.application(),
            effect_validation=EffectValidation.DISABLED,
        )
        self.context = self.rbt.create_external_context(
            name="payment-runner",
            app_internal=True,
        )

    async def asyncTearDown(self) -> None:
        reset_gateway_for_tests()
        await self.rbt.stop()

    def application(self) -> Application:
        return Application(
            servicers=APPLICATION_SERVICERS,
            libraries=[ordered_map_library()],
        )

    async def create_payment(self, payment_intent_id: str = "payment-1"):
        payment, _ = await PaymentIntent.create(
            self.context,
            payment_intent_id,
            user_id="winner-1",
            market_id="market-1",
            bet_id="bet-1",
            amount=250,
        )
        return payment

    async def wait_for_status(
        self,
        payment,
        statuses: set[str],
        *,
        timeout_seconds: float = 10,
    ):
        deadline = asyncio.get_running_loop().time() + timeout_seconds
        while asyncio.get_running_loop().time() < deadline:
            record = await payment.get(self.context)
            if record.status in statuses:
                return record
            await asyncio.sleep(0.05)
        self.fail(f"Payment did not reach one of {sorted(statuses)}.")

    async def test_success_records_single_gateway_attempt(self) -> None:
        calls: list[tuple[str, int, str, int]] = []

        async def succeeds(
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

        configure_gateway_for_tests(succeeds)
        payment = await self.create_payment("payment-success")

        await payment.run(self.context)
        record = await self.wait_for_status(payment, {"succeeded"})

        self.assertEqual(record.status, "succeeded")
        self.assertEqual(record.failure_class, "")
        self.assertEqual(record.idempotency_key, "payment-intent:payment-success")
        self.assertEqual([attempt.status for attempt in record.attempts], ["success"])
        self.assertEqual(calls, [("payment-success", 250, record.idempotency_key, 1)])

    async def test_timeout_then_success_records_retry_history(self) -> None:
        async def timeout_then_success(
            payment_intent_id: str,
            amount: int,
            idempotency_key: str,
            attempt_number: int,
        ) -> GatewayOutcome:
            del payment_intent_id, amount, idempotency_key
            if attempt_number == 1:
                return GatewayOutcome(
                    status="retryable_failure",
                    failure_class="timeout",
                    message="gateway timed out",
                )
            return GatewayOutcome(
                status="success",
                provider_transaction_id="provider:retry-success",
            )

        configure_gateway_for_tests(timeout_then_success)
        payment = await self.create_payment("payment-retry-success")

        await payment.run(self.context)
        record = await self.wait_for_status(payment, {"succeeded"})

        self.assertEqual(record.status, "succeeded")
        self.assertEqual(
            [attempt.status for attempt in record.attempts],
            ["retryable_failure", "success"],
        )
        self.assertEqual(record.attempts[0].failure_class, "timeout")
        self.assertEqual(record.attempts[1].provider_transaction_id, "provider:retry-success")

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
                failure_class="business_rule_rejected",
                message="HTTP 200 body reported payout rejected",
            )

        configure_gateway_for_tests(business_failure)
        payment = await self.create_payment("payment-business-failure")

        await payment.run(self.context)
        record = await self.wait_for_status(payment, {"failed"})

        self.assertEqual(record.status, "failed")
        self.assertEqual(record.failure_class, "business_rule_rejected")
        self.assertEqual([attempt.status for attempt in record.attempts], ["business_failure"])

    async def test_permanent_failure_stops_without_retrying(self) -> None:
        calls = 0

        async def permanent_failure(
            payment_intent_id: str,
            amount: int,
            idempotency_key: str,
            attempt_number: int,
        ) -> GatewayOutcome:
            del payment_intent_id, amount, idempotency_key, attempt_number
            nonlocal calls
            calls += 1
            return GatewayOutcome(
                status="permanent_failure",
                failure_class="provider_declined",
                message="provider declined payout",
            )

        configure_gateway_for_tests(permanent_failure)
        payment = await self.create_payment("payment-permanent-failure")

        await payment.run(self.context)
        record = await self.wait_for_status(payment, {"failed"})

        self.assertEqual(calls, 1)
        self.assertEqual(record.status, "failed")
        self.assertEqual(record.failure_class, "provider_declined")
        self.assertEqual([attempt.status for attempt in record.attempts], ["permanent_failure"])

    async def test_duplicate_idempotency_key_replay_does_not_charge_again(self) -> None:
        calls: list[str] = []

        async def succeeds_once(
            payment_intent_id: str,
            amount: int,
            idempotency_key: str,
            attempt_number: int,
        ) -> GatewayOutcome:
            del amount, attempt_number
            calls.append(idempotency_key)
            return GatewayOutcome(
                status="success",
                provider_transaction_id=f"provider:{payment_intent_id}",
            )

        configure_gateway_for_tests(succeeds_once)
        payment = await self.create_payment("payment-duplicate-replay")

        await payment.run(self.context)
        first = await self.wait_for_status(payment, {"succeeded"})
        await payment.run(self.context)
        second = await self.wait_for_status(payment, {"succeeded"})

        self.assertEqual(calls, ["payment-intent:payment-duplicate-replay"])
        self.assertEqual(first.idempotency_key, second.idempotency_key)
        self.assertEqual(len(second.attempts), 1)
