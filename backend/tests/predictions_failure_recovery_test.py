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
    configure_payout_barrier_for_tests,
    reset_gateway_for_tests,
    reset_payout_barrier_for_tests,
)


class FailureRecoveryTest(unittest.IsolatedAsyncioTestCase):
    """Kills the app at precise points inside the payout workflow and proves
    Reboot's durable replay resumes exactly where it left off: no duplicate
    gateway calls for already-confirmed attempts, and credit applied
    exactly once, even when the crash lands after the gateway confirms but
    before the internal side effects are applied.
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
        reset_payout_barrier_for_tests()
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

    async def test_crash_after_gateway_confirmation_credits_exactly_once_on_replay(
        self,
    ) -> None:
        # The gateway call itself completes and is durably recorded via
        # `at_least_once` *before* the crash; only the pure in-workflow
        # steps after it (credit application, bet/market bookkeeping) are
        # interrupted. Replay must resume from there without re-invoking
        # the gateway.
        started = asyncio.Event()
        release = asyncio.Event()
        gateway_calls: list[int] = []

        async def succeed_immediately(
            payment_intent_id: str,
            amount: int,
            idempotency_key: str,
            attempt_number: int,
        ) -> GatewayOutcome:
            gateway_calls.append(attempt_number)
            return GatewayOutcome(
                status="success",
                provider_transaction_id=f"simulated:{payment_intent_id}",
            )

        async def stall_before_credit(checkpoint: str) -> None:
            started.set()
            await release.wait()

        configure_gateway_for_tests(succeed_immediately)
        configure_payout_barrier_for_tests(stall_before_credit)
        payment_id = await self._place_and_resolve_winning_bet(
            "Will a crash between gateway confirmation and credit still pay exactly once?",
        )

        await asyncio.wait_for(started.wait(), timeout=10)
        await self.rbt.down()
        release.set()

        replay_gateway_calls: list[int] = []

        async def fail_if_called_again(
            payment_intent_id: str,
            amount: int,
            idempotency_key: str,
            attempt_number: int,
        ) -> GatewayOutcome:
            replay_gateway_calls.append(attempt_number)
            return GatewayOutcome(status="success")

        configure_gateway_for_tests(fail_if_called_again)
        reset_payout_barrier_for_tests()
        await self.rbt.up(revision=self.revision)
        await self.refresh_contexts()

        dashboard, payment = await self.wait_for_payment_status(payment_id, {"succeeded"})
        # Give a would-be duplicate replay a window to (incorrectly) fire.
        await asyncio.sleep(0.2)
        after_replay_window = await self.bob.dashboard(self.bob_context)

        self.assertEqual(dashboard.balance, 1100)
        self.assertEqual(after_replay_window.balance, 1100)
        self.assertEqual(dashboard.bets[0].status, "won_paid")
        self.assertEqual(payment.status, "succeeded")
        self.assertEqual(len(payment.attempts), 1)
        self.assertEqual(gateway_calls, [1])
        self.assertEqual(replay_gateway_calls, [])

    async def test_crash_mid_retry_resumes_at_next_attempt_without_repeating_prior_ones(
        self,
    ) -> None:
        started = asyncio.Event()
        release = asyncio.Event()

        async def fail_once_then_stall(
            payment_intent_id: str,
            amount: int,
            idempotency_key: str,
            attempt_number: int,
        ) -> GatewayOutcome:
            if attempt_number == 1:
                return GatewayOutcome(
                    status="retryable_failure",
                    failure_class="timeout",
                    message="gateway did not respond before the deadline",
                )
            started.set()
            await release.wait()
            return GatewayOutcome(status="success")

        configure_gateway_for_tests(fail_once_then_stall)
        payment_id = await self._place_and_resolve_winning_bet(
            "Will a crash mid-retry resume at attempt two instead of repeating attempt one?",
        )

        await asyncio.wait_for(started.wait(), timeout=10)
        await self.rbt.down()
        release.set()

        replay_calls: list[int] = []

        async def succeed_on_replay(
            payment_intent_id: str,
            amount: int,
            idempotency_key: str,
            attempt_number: int,
        ) -> GatewayOutcome:
            replay_calls.append(attempt_number)
            return GatewayOutcome(status="success")

        configure_gateway_for_tests(succeed_on_replay)
        await self.rbt.up(revision=self.revision)
        await self.refresh_contexts()

        dashboard, payment = await self.wait_for_payment_status(payment_id, {"succeeded"})

        self.assertEqual(dashboard.balance, 1100)
        self.assertEqual(
            [attempt.status for attempt in payment.attempts],
            ["retryable_failure", "success"],
        )
        self.assertEqual(
            [attempt.attempt_number for attempt in payment.attempts],
            [1, 2],
        )
        # Attempt 1's result is already durably recorded; only attempt 2,
        # which never finished before the crash, is replayed.
        self.assertEqual(replay_calls, [2])

    async def test_crash_during_final_retry_marks_exhausted_exactly_once_after_restart(
        self,
    ) -> None:
        started = asyncio.Event()
        release = asyncio.Event()

        def timeout_outcome() -> GatewayOutcome:
            return GatewayOutcome(
                status="retryable_failure",
                failure_class="timeout",
                message="gateway did not respond before the deadline",
            )

        async def fail_first_two_then_stall(
            payment_intent_id: str,
            amount: int,
            idempotency_key: str,
            attempt_number: int,
        ) -> GatewayOutcome:
            if attempt_number < 3:
                return timeout_outcome()
            started.set()
            await release.wait()
            return timeout_outcome()

        configure_gateway_for_tests(fail_first_two_then_stall)
        payment_id = await self._place_and_resolve_winning_bet(
            "Will a crash during the final retry still exhaust exactly once?",
        )

        await asyncio.wait_for(started.wait(), timeout=10)
        await self.rbt.down()
        release.set()

        replay_calls: list[int] = []

        async def fail_permanently_on_replay(
            payment_intent_id: str,
            amount: int,
            idempotency_key: str,
            attempt_number: int,
        ) -> GatewayOutcome:
            replay_calls.append(attempt_number)
            return timeout_outcome()

        configure_gateway_for_tests(fail_permanently_on_replay)
        await self.rbt.up(revision=self.revision)
        await self.refresh_contexts()

        dashboard, payment = await self.wait_for_payment_status(
            payment_id, {"retry_exhausted"}
        )

        # Stake stays debited; no payout was ever applied.
        self.assertEqual(dashboard.balance, 900)
        self.assertEqual(dashboard.markets[0].status, "review_required")
        self.assertEqual(dashboard.bets[0].status, "payment_failed")
        self.assertEqual(dashboard.markets[0].payout_failed_count, 1)
        self.assertEqual(len(payment.attempts), 3)
        self.assertEqual(
            [attempt.attempt_number for attempt in payment.attempts], [1, 2, 3]
        )
        # Attempt 3 never finished before the crash, so only it replays.
        self.assertEqual(replay_calls, [3])


if __name__ == "__main__":
    unittest.main()
