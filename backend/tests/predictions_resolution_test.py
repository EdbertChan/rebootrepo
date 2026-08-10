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


class MarketResolutionTest(unittest.IsolatedAsyncioTestCase):
    """Exercises `User.resolve_market`: creator-only auth, resolution state
    transitions, and that winners/losers get credited/settled exactly once.
    """

    async def asyncSetUp(self) -> None:
        self.rbt = Reboot()
        await self.rbt.start()
        self.revision = await self.rbt.up(
            self.application(),
            effect_validation=EffectValidation.DISABLED,
            local_envoy=False,
            servers=1,
        )
        self.alice_context = await self.rbt.create_external_context_as(
            "alice",
            user_id="alice",
        )
        self.bob_context = await self.rbt.create_external_context_as(
            "bob",
            user_id="bob",
        )
        self.carol_context = await self.rbt.create_external_context_as(
            "carol",
            user_id="carol",
        )
        self.alice = User.ref("alice")
        self.bob = User.ref("bob")
        self.carol = User.ref("carol")

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
        self.carol_context = await self.rbt.create_external_context_as(
            "carol",
            user_id="carol",
        )
        self.alice = User.ref("alice")
        self.bob = User.ref("bob")
        self.carol = User.ref("carol")

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

    async def test_only_creator_can_resolve_market(self) -> None:
        market = await self.alice.create_market(
            self.alice_context,
            question="Will only the creator be able to resolve this?",
            close_after_seconds=0,
        )
        await self.alice.close_market(self.alice_context, market_id=market.market_id)

        with self.assertRaises(Aborted):
            await self.bob.resolve_market(
                self.bob_context,
                market_id=market.market_id,
                winning_outcome="YES",
            )

        dashboard = await self.alice.dashboard(self.alice_context)
        self.assertEqual(dashboard.markets[0].status, "closed")
        self.assertEqual(dashboard.markets[0].winning_outcome, "")

    async def test_resolution_before_close_is_rejected(self) -> None:
        market = await self.alice.create_market(
            self.alice_context,
            question="Will resolving a still-open market be rejected?",
            close_after_seconds=0,
        )

        with self.assertRaises(Aborted):
            await self.alice.resolve_market(
                self.alice_context,
                market_id=market.market_id,
                winning_outcome="YES",
            )

        dashboard = await self.alice.dashboard(self.alice_context)
        self.assertEqual(dashboard.markets[0].status, "open")
        self.assertEqual(dashboard.markets[0].winning_outcome, "")

    async def test_double_resolution_is_rejected(self) -> None:
        market = await self.alice.create_market(
            self.alice_context,
            question="Will resolving twice be rejected?",
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

        with self.assertRaises(Aborted):
            await self.alice.resolve_market(
                self.alice_context,
                market_id=market.market_id,
                winning_outcome="NO",
            )

        dashboard = await self.alice.dashboard(self.alice_context)
        self.assertEqual(dashboard.markets[0].status, "resolved")
        self.assertEqual(dashboard.markets[0].winning_outcome, "YES")

    async def test_invalid_winning_outcome_is_rejected(self) -> None:
        market = await self.alice.create_market(
            self.alice_context,
            question="Will an invalid winning outcome be rejected?",
            close_after_seconds=0,
        )
        await self.alice.close_market(self.alice_context, market_id=market.market_id)

        with self.assertRaises(Aborted):
            await self.alice.resolve_market(
                self.alice_context,
                market_id=market.market_id,
                winning_outcome="MAYBE",
            )

        dashboard = await self.alice.dashboard(self.alice_context)
        self.assertEqual(dashboard.markets[0].status, "closed")

    async def test_winner_is_paid_double_and_loser_gets_nothing(self) -> None:
        market = await self.alice.create_market(
            self.alice_context,
            question="Will winners get double and losers get nothing?",
            close_after_seconds=0,
        )
        await self.bob.place_bet(
            self.bob_context,
            market_id=market.market_id,
            outcome="YES",
            stake=100,
        )
        await self.carol.place_bet(
            self.carol_context,
            market_id=market.market_id,
            outcome="NO",
            stake=60,
        )
        await self.alice.close_market(self.alice_context, market_id=market.market_id)

        resolution = await self.alice.resolve_market(
            self.alice_context,
            market_id=market.market_id,
            winning_outcome="YES",
        )
        self.assertEqual(resolution.winner_count, 1)
        self.assertEqual(resolution.loser_count, 1)
        self.assertEqual(resolution.payout_count, 1)

        bob_dashboard = await self.bob.dashboard(self.bob_context)
        payment_intent_id = bob_dashboard.payments[0].payment_intent_id
        bob_dashboard, payment = await self.wait_for_payment_status(
            payment_intent_id,
            {"succeeded"},
        )

        carol_dashboard = await self.carol.dashboard(self.carol_context)

        # Winner: stake was debited on bet placement (900), then paid
        # stake*2 = 200 back on payout, netting 1000 - 100 + 200 = 1100.
        self.assertEqual(bob_dashboard.balance, 1100)
        self.assertEqual(bob_dashboard.bets[0].status, "won_paid")
        self.assertEqual(bob_dashboard.bets[0].payout_amount, 200)
        self.assertEqual(payment.status, "succeeded")

        # Loser: stake stays debited, no payout ever created.
        self.assertEqual(carol_dashboard.balance, 940)
        self.assertEqual(carol_dashboard.bets[0].status, "lost")
        self.assertEqual(carol_dashboard.bets[0].payout_amount, 0)
        self.assertEqual(carol_dashboard.payments, [])

    async def test_permanent_gateway_failure_marks_market_review_required(
        self,
    ) -> None:
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
            question="Will a permanently failed payout require review?",
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

        # No credit applied: balance stays at the post-debit amount.
        self.assertEqual(dashboard.balance, 900)
        self.assertEqual(dashboard.markets[0].status, "review_required")
        self.assertEqual(dashboard.bets[0].status, "payment_failed")
        self.assertEqual(payment.status, "failed")
        self.assertEqual(payment.failure_class, "declined")

        audit = await self.alice.audit_log(
            self.alice_context,
            market_id=market.market_id,
        )
        event_types = [event.event_type for event in audit.events]
        self.assertIn("market_resolved", event_types)
        self.assertIn("payout_failed", event_types)

    async def test_resolution_payout_replay_settles_balance_exactly_once(
        self,
    ) -> None:
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
            question="Will a crash mid-payout still settle exactly once?",
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
        # Give any duplicate replay a window to (incorrectly) re-apply.
        await asyncio.sleep(0.2)
        after_replay_window = await self.bob.dashboard(self.bob_context)

        self.assertEqual(dashboard.balance, 1100)
        self.assertEqual(after_replay_window.balance, 1100)
        self.assertEqual(dashboard.bets[0].status, "won_paid")
        self.assertEqual(payment.status, "succeeded")
        self.assertEqual(len(payment.attempts), 1)


if __name__ == "__main__":
    unittest.main()
