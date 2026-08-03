import asyncio
import unittest
from typing import Any

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


class PaymentIntentExecutionTest(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self) -> None:
        self.rbt = Reboot()
        await self.rbt.start()
        await self.rbt.up(
            Application(
                servicers=APPLICATION_SERVICERS,
                libraries=[ordered_map_library()],
            ),
            effect_validation=EffectValidation.DISABLED,
        )
        self.context = await self.rbt.create_external_context(
            name="payment-tester",
            app_internal=True,
        )

    async def asyncTearDown(self) -> None:
        reset_gateway_for_tests()
        await self.rbt.stop()

    async def create_payment(self, payment_intent_id: str = "payment-1") -> Any:
        await PaymentIntent.create(
            self.context,
            payment_intent_id,
            user_id="winner",
            market_id="market-1",
            bet_id="bet-1",
            amount=250,
        )
        return PaymentIntent.ref(payment_intent_id)

    async def run_and_wait(
        self,
        payment: Any,
        statuses: set[str],
        *,
        timeout_seconds: float = 10,
    ) -> PaymentIntent.GetResponse:
        await payment.schedule().run(self.context)
        deadline = asyncio.get_running_loop().time() + timeout_seconds
        while asyncio.get_running_loop().time() < deadline:
            record = await payment.get(self.context)
            if record.status in statuses:
                return record
            await asyncio.sleep(0.05)
        self.fail(f"Payment did not reach one of {sorted(statuses)}.")

    async def test_success_records_single_attempt(self) -> None:
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
                provider_transaction_id=f"gateway:{idempotency_key}",
            )

        configure_gateway_for_tests(succeeds)
        payment = await self.create_payment("payment-success")

        record = await self.run_and_wait(payment, {"succeeded"})

        self.assertEqual(record.status, "succeeded")
        self.assertEqual(record.failure_class, "")
        self.assertEqual(record.idempotency_key, "payment-intent:payment-success")
        self.assertEqual([attempt.status for attempt in record.attempts], ["success"])
        self.assertEqual(calls, [("payment-success", 250, record.idempotency_key, 1)])

    async def test_timeout_then_success_records_retryable_history(self) -> None:
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
                provider_transaction_id=f"gateway:attempt-{attempt_number}",
            )

        configure_gateway_for_tests(timeout_then_success)
        payment = await self.create_payment("payment-retry")

        record = await self.run_and_wait(payment, {"succeeded"})

        self.assertEqual(record.status, "succeeded")
        self.assertEqual(
            [attempt.status for attempt in record.attempts],
            ["retryable_failure", "success"],
        )
        self.assertEqual(record.attempts[0].failure_class, "timeout")

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
                failure_class="invalid_recipient",
                message="HTTP 200 body rejected the payment",
            )

        configure_gateway_for_tests(business_failure)
        payment = await self.create_payment("payment-business-failure")

        record = await self.run_and_wait(payment, {"failed"})

        self.assertEqual(record.status, "failed")
        self.assertEqual(record.failure_class, "invalid_recipient")
        self.assertEqual([attempt.status for attempt in record.attempts], ["business_failure"])

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
                message="provider declined payment",
            )

        configure_gateway_for_tests(permanent_failure)
        payment = await self.create_payment("payment-permanent-failure")

        record = await self.run_and_wait(payment, {"failed"})

        self.assertEqual(record.status, "failed")
        self.assertEqual(record.failure_class, "declined")
        self.assertEqual([attempt.status for attempt in record.attempts], ["permanent_failure"])

    async def test_duplicate_idempotency_key_replay_is_not_charged_twice(self) -> None:
        gateway_results: dict[str, GatewayOutcome] = {}
        gateway_calls: list[str] = []

        async def idempotent_gateway(
            payment_intent_id: str,
            amount: int,
            idempotency_key: str,
            attempt_number: int,
        ) -> GatewayOutcome:
            del payment_intent_id, amount, attempt_number
            gateway_calls.append(idempotency_key)
            if idempotency_key not in gateway_results:
                gateway_results[idempotency_key] = GatewayOutcome(
                    status="success",
                    provider_transaction_id=f"gateway:{idempotency_key}",
                    message="created",
                )
            return gateway_results[idempotency_key]

        configure_gateway_for_tests(idempotent_gateway)
        payment = await self.create_payment("payment-replay")

        first = await self.run_and_wait(payment, {"succeeded"})
        await payment.schedule().run(self.context)
        await asyncio.sleep(0.2)
        second = await payment.get(self.context)

        self.assertEqual(first.status, "succeeded")
        self.assertEqual(second.status, "succeeded")
        self.assertEqual(len(second.attempts), 1)
        self.assertEqual(gateway_calls, ["payment-intent:payment-replay"])
