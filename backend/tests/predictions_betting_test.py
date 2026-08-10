import asyncio
import unittest

from reboot.aio.aborted import Aborted
from reboot.aio.applications import Application
from reboot.aio.contexts import EffectValidation
from reboot.aio.tests import Reboot
from reboot.std.collections.ordered_map.v1.ordered_map import ordered_map_library

from predictions.v1.predictions_rbt import User
from servicers.predictions import APPLICATION_SERVICERS


class BetPlacementTest(unittest.IsolatedAsyncioTestCase):
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

    async def test_place_bet_debits_balance_exactly_once_and_creates_bet_record(
        self,
    ) -> None:
        market = await self.alice.create_market(
            self.alice_context,
            question="Will a valid bet debit credits exactly once?",
            close_after_seconds=0,
        )

        placed = await self.bob.place_bet(
            self.bob_context,
            market_id=market.market_id,
            outcome="YES",
            stake=200,
        )

        self.assertEqual(placed.balance, 800)
        dashboard = await self.bob.dashboard(self.bob_context)
        self.assertEqual(dashboard.balance, 800)
        self.assertEqual(len(dashboard.bets), 1)
        self.assertEqual(dashboard.bets[0].bet_id, placed.bet_id)
        self.assertEqual(dashboard.bets[0].stake, 200)
        self.assertEqual(dashboard.bets[0].outcome, "YES")
        self.assertEqual(dashboard.markets[0].yes_total, 200)
        self.assertEqual(dashboard.markets[0].bet_count, 1)

    async def test_insufficient_credits_rejected_without_mutating_state(
        self,
    ) -> None:
        market = await self.alice.create_market(
            self.alice_context,
            question="Will overdraft bets be rejected?",
            close_after_seconds=0,
        )

        with self.assertRaises(Aborted):
            await self.bob.place_bet(
                self.bob_context,
                market_id=market.market_id,
                outcome="YES",
                stake=1001,
            )

        dashboard = await self.bob.dashboard(self.bob_context)
        self.assertEqual(dashboard.balance, 1000)
        self.assertEqual(dashboard.bets, [])
        self.assertEqual(dashboard.markets[0].yes_total, 0)
        self.assertEqual(dashboard.markets[0].bet_count, 0)

    async def test_closed_market_rejects_new_bets(self) -> None:
        market = await self.alice.create_market(
            self.alice_context,
            question="Will closed markets reject new bets?",
            close_after_seconds=0,
        )
        await self.alice.close_market(
            self.alice_context,
            market_id=market.market_id,
        )

        with self.assertRaises(Aborted):
            await self.bob.place_bet(
                self.bob_context,
                market_id=market.market_id,
                outcome="YES",
                stake=100,
            )

        dashboard = await self.bob.dashboard(self.bob_context)
        self.assertEqual(dashboard.balance, 1000)
        self.assertEqual(dashboard.bets, [])

    async def test_invalid_outcome_rejected(self) -> None:
        market = await self.alice.create_market(
            self.alice_context,
            question="Will invalid outcomes be rejected?",
            close_after_seconds=0,
        )

        with self.assertRaises(Aborted):
            await self.bob.place_bet(
                self.bob_context,
                market_id=market.market_id,
                outcome="MAYBE",
                stake=100,
            )

        dashboard = await self.bob.dashboard(self.bob_context)
        self.assertEqual(dashboard.balance, 1000)
        self.assertEqual(dashboard.bets, [])

    async def test_cross_user_isolation(self) -> None:
        market = await self.alice.create_market(
            self.alice_context,
            question="Will bets stay isolated between users?",
            close_after_seconds=0,
        )

        # Use a throwaway `Context` for the illegitimate attempt: reusing
        # `self.alice_context` for a later legitimate mutation after an
        # aborted mutating call leaves the SDK unable to tell whether that
        # later call is an unsafe retry.
        impostor_context = await self.rbt.create_external_context_as(
            "impostor-attempt",
            user_id="alice",
        )
        with self.assertRaises(Aborted):
            await User.ref("bob").place_bet(
                impostor_context,
                market_id=market.market_id,
                outcome="YES",
                stake=100,
            )

        bob_dashboard = await self.bob.dashboard(self.bob_context)
        self.assertEqual(bob_dashboard.balance, 1000)
        self.assertEqual(bob_dashboard.bets, [])

        await self.bob.place_bet(
            self.bob_context,
            market_id=market.market_id,
            outcome="YES",
            stake=300,
        )
        await self.alice.place_bet(
            self.alice_context,
            market_id=market.market_id,
            outcome="NO",
            stake=150,
        )

        bob_dashboard = await self.bob.dashboard(self.bob_context)
        alice_dashboard = await self.alice.dashboard(self.alice_context)
        self.assertEqual(bob_dashboard.balance, 700)
        self.assertEqual(len(bob_dashboard.bets), 1)
        self.assertEqual(bob_dashboard.bets[0].user_id, "bob")
        self.assertEqual(alice_dashboard.balance, 850)
        self.assertEqual(len(alice_dashboard.bets), 1)
        self.assertEqual(alice_dashboard.bets[0].user_id, "alice")

    async def test_concurrent_same_user_bets_never_overdraw_balance(self) -> None:
        market = await self.alice.create_market(
            self.alice_context,
            question="Will concurrent same-user bets stay race-free?",
            close_after_seconds=0,
        )
        stake = 300
        attempts = 5  # 5 * 300 = 1500 > 1000 starting credits.

        results = await asyncio.gather(
            *[
                self.bob.place_bet(
                    self.bob_context,
                    market_id=market.market_id,
                    outcome="YES",
                    stake=stake,
                )
                for _ in range(attempts)
            ],
            return_exceptions=True,
        )

        successes = [r for r in results if not isinstance(r, BaseException)]
        failures = [r for r in results if isinstance(r, BaseException)]
        for failure in failures:
            self.assertIsInstance(failure, Aborted)

        # floor(1000 / 300) == 3 bets can be afforded; no more, no fewer.
        self.assertEqual(len(successes), 3)
        self.assertEqual(len(failures), 2)

        dashboard = await self.bob.dashboard(self.bob_context)
        self.assertEqual(dashboard.balance, 1000 - 3 * stake)
        self.assertGreaterEqual(dashboard.balance, 0)
        self.assertEqual(len(dashboard.bets), 3)
        self.assertEqual(dashboard.markets[0].bet_count, 3)
        self.assertEqual(dashboard.markets[0].yes_total, 3 * stake)


if __name__ == "__main__":
    unittest.main()
