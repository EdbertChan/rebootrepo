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


class PaymentIntentExecutionTest(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self) -> None:
        self.rbt = Reboot()
        await self.rbt.start()
        await self.rbt.up(
            self.application(),
            effect_validation=EffectValidation.DISABLED,
        )
        self.context = self.rbt.create_initialize_context(
            name="payment-test",
        )

    async def asyncTearDown(self) -> None:
        reset_gateway_for_tests()
        await self.rbt.stop()

    def application(self) -> Application:
        return Application(
            servicers=APPLICATION_SERVICERS,
            libraries=[ordered_map_library()],
        )

    async def create_payment_intent(
        self,
        payment_intent_id: str,
        *,
        amount: int = 250,
    ) -> PaymentIntent.WeakReference:
        payment, _ = await PaymentIntent.idempotently(
            f"create-{payment_intent_id}"
        ).create(
            self.context,
            payment_intent_id,
            user_id="winner",
            market_id="market-1",
            bet_id="bet-1",
            amount=amount,
        )
        return payment

    async def run_and_wait(
        self,
        payment_intent_id: str,
        statuses: set[str],
        *,
        timeout_seconds: float = 10,
    ):
        payment = PaymentIntent.ref(payment_intent_id)
        await payment.idempotently(f"run-{payment_intent_id}").run(self.context)
        deadline = asyncio.get_running_loop().time() + timeout_seconds
        while asyncio.get_running_loop().time() < deadline:
            current = await payment.get(self.context)
            if current.status in statuses:
                return current
            await asyncio.sleep(0.05)
        self.fail(
            f"Payment {payment_intent_id} did not reach one of {sorted(statuses)}."
        )

    async def test_gateway_success_records_attempt_and_completion(self) -> None:
        await self.create_payment_intent("payment-success")

        payment = await self.run_and_wait("payment-success", {"succeeded"})

        self.assertEqual(payment.status, "succeeded")
        self.assertEqual(payment.failure_class, "")
        self.assertEqual(payment.attempts[0].status, "success")
        self.assertEqual(
            payment.attempts[0].provider_transaction_id,
            "simulated:payment-success",
        )
        self.assertNotEqual(payment.idempotency_key, "")

    async def test_gateway_timeout_then_success_records_retry_history(self) -> None:
        seen_attempts: list[int] = []

        async def timeout_then_success(
            payment_intent_id: str,
            amount: int,
            idempotency_key: str,
            attempt_number: int,
        ) -> GatewayOutcome:
            del amount, idempotency_key
            seen_attempts.append(attempt_number)
            if attempt_number == 1:
                return GatewayOutcome(
                    status="retryable_failure",
                    failure_class="timeout",
                    message="gateway timed out",
                )
            return GatewayOutcome(
                status="success",
                provider_transaction_id=f"simulated:{payment_intent_id}",
            )

        configure_gateway_for_tests(timeout_then_success)
        await self.create_payment_intent("payment-timeout-success")

        payment = await self.run_and_wait(
            "payment-timeout-success",
            {"succeeded"},
        )

        self.assertEqual(seen_attempts, [1, 2])
        self.assertEqual(payment.status, "succeeded")
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
                failure_class="account_closed",
                message="HTTP 200 success=false",
            )

        configure_gateway_for_tests(business_failure)
        await self.create_payment_intent("payment-business-failure")

        payment = await self.run_and_wait("payment-business-failure", {"failed"})

        self.assertEqual(payment.status, "failed")
        self.assertEqual(payment.failure_class, "account_closed")
        self.assertEqual(len(payment.attempts), 1)
        self.assertEqual(payment.attempts[0].status, "business_failure")

    async def test_permanent_failure_records_terminal_failure(self) -> None:
        async def permanent_failure(
            payment_intent_id: str,
            amount: int,
            idempotency_key: str,
            attempt_number: int,
        ) -> GatewayOutcome:
            del payment_intent_id, amount, idempotency_key, attempt_number
            return GatewayOutcome(
                status="permanent_failure",
                failure_class="provider_declined",
                message="provider declined payout",
            )

        configure_gateway_for_tests(permanent_failure)
        await self.create_payment_intent("payment-permanent-failure")

        payment = await self.run_and_wait("payment-permanent-failure", {"failed"})

        self.assertEqual(payment.status, "failed")
        self.assertEqual(payment.failure_class, "provider_declined")
        self.assertEqual(len(payment.attempts), 1)
        self.assertEqual(payment.attempts[0].status, "permanent_failure")

    async def test_duplicate_idempotency_key_replay_returns_original_success(self) -> None:
        provider_by_key: dict[str, str] = {}
        keys: list[str] = []

        async def replaying_gateway(
            payment_intent_id: str,
            amount: int,
            idempotency_key: str,
            attempt_number: int,
        ) -> GatewayOutcome:
            del amount
            keys.append(idempotency_key)
            if idempotency_key in provider_by_key:
                return GatewayOutcome(
                    status="success",
                    provider_transaction_id=provider_by_key[idempotency_key],
                    message="idempotency replay",
                )
            provider_by_key[idempotency_key] = f"provider:{payment_intent_id}"
            return GatewayOutcome(
                status="retryable_failure",
                failure_class="timeout",
                message=f"attempt {attempt_number} response lost",
            )

        configure_gateway_for_tests(replaying_gateway)
        await self.create_payment_intent("payment-idempotency-replay")

        payment = await self.run_and_wait(
            "payment-idempotency-replay",
            {"succeeded"},
        )

        self.assertEqual(len(keys), 2)
        self.assertEqual(keys[0], keys[1])
        self.assertEqual(payment.status, "succeeded")
        self.assertEqual(
            [attempt.status for attempt in payment.attempts],
            ["retryable_failure", "success"],
        )
        self.assertEqual(
            payment.attempts[1].provider_transaction_id,
            "provider:payment-idempotency-replay",
        )
