import unittest

from reboot.aio.aborted import Aborted
from reboot.aio.applications import Application
from reboot.aio.contexts import EffectValidation
from reboot.aio.tests import Reboot
from reboot.std.collections.ordered_map.v1.ordered_map import ordered_map_library

from predictions.v1.predictions_rbt import User
from servicers.predictions import APPLICATION_SERVICERS


class PredictionMarketSkeletonTest(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self) -> None:
        self.rbt = Reboot()
        await self.rbt.start()
        await self.rbt.up(
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
        await self.rbt.stop()

    def application(self) -> Application:
        return Application(
            servicers=APPLICATION_SERVICERS,
            libraries=[ordered_map_library()],
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

    async def test_place_bet_is_recorded_without_debiting_credits(self) -> None:
        # This slice is a skeleton: placing a bet records it, but does not
        # move credits. Debiting is trading behavior for a later slice.
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

        self.assertEqual(placed.balance, 1000)
        self.assertEqual(dashboard.balance, 1000)
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

    async def test_resolve_market_records_outcome_without_spawning_payouts(self) -> None:
        # This slice is a skeleton: resolving a market records the winning
        # outcome, but does not tally winners/losers or spawn payouts.
        # That resolution behavior belongs to a later slice.
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
        resolved = await self.alice.resolve_market(
            self.alice_context,
            market_id=market.market_id,
            winning_outcome="YES",
        )

        self.assertEqual(resolved.winner_count, 0)
        self.assertEqual(resolved.loser_count, 0)
        self.assertEqual(resolved.payout_count, 0)

        dashboard = await self.bob.dashboard(self.bob_context)
        self.assertEqual(dashboard.balance, 1000)
        self.assertEqual(dashboard.payments, [])
        self.assertEqual(dashboard.markets[0].status, "resolved")
        self.assertEqual(dashboard.markets[0].winning_outcome, "YES")
