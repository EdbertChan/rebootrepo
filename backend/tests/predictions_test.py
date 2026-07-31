import asyncio
import unittest

from reboot.aio.aborted import Aborted
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


class PredictionMarketTest(unittest.IsolatedAsyncioTestCase):
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

    async def test_user_starts_with_credits_and_sees_catalog(self) -> None:
        alice_dashboard = await self.alice.dashboard(self.alice_context)
        self.assertEqual(alice_dashboard.balance, 1000)
        self.assertEqual(alice_dashboard.markets, [])

        market = await self.alice.create_market(
            self.alice_context,
            question="Will prediction markets ship?",
            close_after_seconds=0,
        )
        bob_dashboard = await self.bob.dashboard(self.bob_context)

        self.assertEqual(bob_dashboard.balance, 1000)
        self.assertEqual(len(bob_dashboard.markets), 1)
        self.assertEqual(bob_dashboard.markets[0].market_id, market.market_id)
        self.assertEqual(bob_dashboard.markets[0].status, "open")

    async def test_place_bet_debits_balance_and_writes_audit(self) -> None:
        market = await self.alice.create_market(
            self.alice_context,
            question="Will users bet with demo credits?",
            close_after_seconds=0,
        )

        placed = await self.bob.place_bet(
            self.bob_context,
            market_id=market.market_id,
            outcome="yes",
            stake=125,
        )
        dashboard = await self.bob.dashboard(self.bob_context)
        audit = await self.bob.audit_log(
            self.bob_context,
            market_id=market.market_id,
        )

        self.assertEqual(placed.balance, 875)
        self.assertEqual(dashboard.balance, 875)
        self.assertEqual(dashboard.bets[0].bet_id, placed.bet_id)
        self.assertEqual(dashboard.markets[0].yes_total, 125)
        self.assertIn("bet_placed", [event.event_type for event in audit.events])

    async def test_business_validation_aborts(self) -> None:
        open_market = await self.alice.create_market(
            self.alice_context,
            question="Will invalid bets be rejected?",
            close_after_seconds=0,
        )
        closed_market = await self.alice.create_market(
            self.alice_context,
            question="Will closed markets reject new bets?",
            close_after_seconds=0,
        )
        await self.alice.close_market(
            self.alice_context,
            market_id=closed_market.market_id,
        )

        with self.assertRaises(Aborted):
            await self.bob.place_bet(
                self.bob_context,
                market_id=open_market.market_id,
                outcome="MAYBE",
                stake=10,
            )
        with self.assertRaises(Aborted):
            await self.bob.place_bet(
                self.bob_context,
                market_id=open_market.market_id,
                outcome="YES",
                stake=1001,
            )
        with self.assertRaises(Aborted):
            await self.bob.place_bet(
                self.bob_context,
                market_id=closed_market.market_id,
                outcome="YES",
                stake=10,
            )
        with self.assertRaises(Aborted):
            await self.bob.resolve_market(
                self.bob_context,
                market_id=closed_market.market_id,
                winning_outcome="YES",
            )

    async def test_resolution_pays_winners_double_exactly_once(self) -> None:
        market = await self.alice.create_market(
            self.alice_context,
            question="Will winners be paid after close?",
            close_after_seconds=0,
        )
        await self.bob.place_bet(
            self.bob_context,
            market_id=market.market_id,
            outcome="YES",
            stake=100,
        )
        await self.alice.close_market(self.alice_context, market_id=market.market_id)
        await self.alice.resolve_market(
            self.alice_context,
            market_id=market.market_id,
            winning_outcome="YES",
        )

        dashboard = await self.bob.dashboard(self.bob_context)
        payment_intent_id = dashboard.payments[0].payment_intent_id
        dashboard, payment = await self.wait_for_payment_status(
            payment_intent_id,
            {"succeeded"},
        )
        await asyncio.sleep(0.2)
        after_replay_window = await self.bob.dashboard(self.bob_context)

        self.assertEqual(dashboard.balance, 1100)
        self.assertEqual(after_replay_window.balance, 1100)
        self.assertEqual(dashboard.bets[0].status, "won_paid")
        self.assertEqual(payment.status, "succeeded")
        self.assertEqual(len(payment.attempts), 1)

    async def test_gateway_retry_then_success(self) -> None:
        attempts = 0

        async def retry_then_success(
            payment_intent_id: str,
            amount: int,
            idempotency_key: str,
            attempt_number: int,
        ) -> GatewayOutcome:
            nonlocal attempts
            attempts += 1
            if attempt_number < 3:
                return GatewayOutcome(
                    status="retryable_failure",
                    failure_class="timeout",
                    message="gateway timed out",
                )
            return GatewayOutcome(
                status="success",
                provider_transaction_id=f"simulated:{payment_intent_id}",
            )

        configure_gateway_for_tests(retry_then_success)
        market = await self.alice.create_market(
            self.alice_context,
            question="Will retryable payouts recover?",
            close_after_seconds=0,
        )
        await self.bob.place_bet(
            self.bob_context,
            market_id=market.market_id,
            outcome="YES",
            stake=100,
        )
        await self.alice.close_market(self.alice_context, market_id=market.market_id)
        await self.alice.resolve_market(
            self.alice_context,
            market_id=market.market_id,
            winning_outcome="YES",
        )
        payment_id = (await self.bob.dashboard(self.bob_context)).payments[0].payment_intent_id

        dashboard, payment = await self.wait_for_payment_status(
            payment_id,
            {"succeeded"},
        )
        self.assertEqual(dashboard.balance, 1100)
        self.assertEqual(payment.status, "succeeded")
        self.assertEqual([attempt.status for attempt in payment.attempts], [
            "retryable_failure",
            "retryable_failure",
            "success",
        ])
        self.assertGreaterEqual(attempts, 3)

    async def test_gateway_permanent_failure_requires_review(self) -> None:
        async def declined(
            payment_intent_id: str,
            amount: int,
            idempotency_key: str,
            attempt_number: int,
        ) -> GatewayOutcome:
            return GatewayOutcome(
                status="permanent_failure",
                failure_class="declined",
                message="provider declined payout",
            )

        configure_gateway_for_tests(declined)
        market = await self.alice.create_market(
            self.alice_context,
            question="Will permanent failures stop?",
            close_after_seconds=0,
        )
        await self.bob.place_bet(
            self.bob_context,
            market_id=market.market_id,
            outcome="YES",
            stake=100,
        )
        await self.alice.close_market(self.alice_context, market_id=market.market_id)
        await self.alice.resolve_market(
            self.alice_context,
            market_id=market.market_id,
            winning_outcome="YES",
        )
        payment_id = (await self.bob.dashboard(self.bob_context)).payments[0].payment_intent_id

        dashboard, payment = await self.wait_for_payment_status(
            payment_id,
            {"failed"},
        )
        self.assertEqual(dashboard.balance, 900)
        self.assertEqual(dashboard.markets[0].status, "review_required")
        self.assertEqual(payment.status, "failed")
        self.assertEqual(payment.failure_class, "declined")

    async def test_gateway_retry_exhaustion_requires_review(self) -> None:
        async def always_timeout(
            payment_intent_id: str,
            amount: int,
            idempotency_key: str,
            attempt_number: int,
        ) -> GatewayOutcome:
            return GatewayOutcome(
                status="retryable_failure",
                failure_class="timeout",
                message="gateway timed out",
            )

        configure_gateway_for_tests(always_timeout)
        market = await self.alice.create_market(
            self.alice_context,
            question="Will retry exhaustion surface for review?",
            close_after_seconds=0,
        )
        await self.bob.place_bet(
            self.bob_context,
            market_id=market.market_id,
            outcome="YES",
            stake=100,
        )
        await self.alice.close_market(self.alice_context, market_id=market.market_id)
        await self.alice.resolve_market(
            self.alice_context,
            market_id=market.market_id,
            winning_outcome="YES",
        )
        payment_id = (await self.bob.dashboard(self.bob_context)).payments[0].payment_intent_id

        dashboard, payment = await self.wait_for_payment_status(
            payment_id,
            {"retry_exhausted"},
        )
        self.assertEqual(dashboard.balance, 900)
        self.assertEqual(dashboard.markets[0].status, "review_required")
        self.assertEqual(payment.status, "retry_exhausted")
        self.assertEqual(len(payment.attempts), 3)

    async def test_crash_during_gateway_recovers_without_double_credit(self) -> None:
        started = asyncio.Event()
        release = asyncio.Event()

        async def stalled_success(
            payment_intent_id: str,
            amount: int,
            idempotency_key: str,
            attempt_number: int,
        ) -> GatewayOutcome:
            started.set()
            await release.wait()
            return GatewayOutcome(
                status="success",
                provider_transaction_id=f"simulated:{payment_intent_id}",
            )

        configure_gateway_for_tests(stalled_success)
        market = await self.alice.create_market(
            self.alice_context,
            question="Will crash recovery settle once?",
            close_after_seconds=0,
        )
        await self.bob.place_bet(
            self.bob_context,
            market_id=market.market_id,
            outcome="YES",
            stake=100,
        )
        await self.alice.close_market(self.alice_context, market_id=market.market_id)
        await self.alice.resolve_market(
            self.alice_context,
            market_id=market.market_id,
            winning_outcome="YES",
        )
        payment_id = (await self.bob.dashboard(self.bob_context)).payments[0].payment_intent_id

        await asyncio.wait_for(started.wait(), timeout=10)
        await self.rbt.down()
        release.set()

        reset_gateway_for_tests()
        await self.rbt.up(revision=self.revision)
        await self.refresh_contexts()

        dashboard, payment = await self.wait_for_payment_status(
            payment_id,
            {"succeeded"},
        )
        await asyncio.sleep(0.2)
        after_replay_window = await self.bob.dashboard(self.bob_context)
        self.assertEqual(dashboard.balance, 1100)
        self.assertEqual(after_replay_window.balance, 1100)
        self.assertEqual(payment.status, "succeeded")
        self.assertEqual(len(payment.attempts), 1)
