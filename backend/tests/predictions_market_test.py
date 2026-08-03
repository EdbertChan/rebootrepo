import asyncio
import unittest

from reboot.aio.applications import Application
from reboot.aio.contexts import EffectValidation
from reboot.aio.tests import Reboot
from reboot.std.collections.ordered_map.v1.ordered_map import ordered_map_library

from predictions.v1.predictions_rbt import Market, User
from servicers.predictions import APPLICATION_SERVICERS


class PredictionMarketLifecycleTest(unittest.IsolatedAsyncioTestCase):
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

    async def wait_for_market_status(
        self,
        market_id: str,
        status: str,
        *,
        timeout_seconds: float = 10,
    ):
        deadline = asyncio.get_running_loop().time() + timeout_seconds
        while asyncio.get_running_loop().time() < deadline:
            market = await Market.ref(market_id).get(self.alice_context)
            if market.status == status:
                return market
            await asyncio.sleep(0.1)
        self.fail(f"Market {market_id} did not reach status {status}.")

    async def test_create_lists_and_audits_market_without_financial_side_effects(
        self,
    ) -> None:
        initial_dashboard = await self.alice.dashboard(self.alice_context)
        self.assertEqual(initial_dashboard.balance, 0)
        self.assertEqual(initial_dashboard.markets, [])
        self.assertEqual(initial_dashboard.bets, [])
        self.assertEqual(initial_dashboard.payments, [])

        created = await self.alice.create_market(
            self.alice_context,
            question="Will the lifecycle slice ship?",
            close_after_seconds=0,
        )
        market = await Market.ref(created.market_id).get(self.alice_context)
        bob_dashboard = await self.bob.dashboard(self.bob_context)
        alice_dashboard = await self.alice.dashboard(self.alice_context)
        audit = await self.alice.audit_log(
            self.alice_context,
            market_id=created.market_id,
        )

        self.assertEqual(market.creator_user_id, "alice")
        self.assertEqual(market.question, "Will the lifecycle slice ship?")
        self.assertEqual(market.status, "open")
        self.assertEqual([m.market_id for m in bob_dashboard.markets], [created.market_id])
        self.assertEqual([m.market_id for m in alice_dashboard.markets], [created.market_id])
        self.assertEqual(bob_dashboard.balance, 0)
        self.assertEqual(bob_dashboard.bets, [])
        self.assertEqual(bob_dashboard.payments, [])
        self.assertIn("market_created", [event.event_type for event in audit.events])

    async def test_creator_can_close_market_and_audit_row_updates(self) -> None:
        created = await self.alice.create_market(
            self.alice_context,
            question="Will manual close work?",
            close_after_seconds=0,
        )

        closed = await self.alice.close_market(
            self.alice_context,
            market_id=created.market_id,
        )
        market = await Market.ref(created.market_id).get(self.alice_context)
        audit = await self.alice.audit_log(
            self.alice_context,
            market_id=created.market_id,
        )

        self.assertEqual(closed.status, "closed")
        self.assertEqual(market.status, "closed")
        self.assertIn("market_closed", [event.event_type for event in audit.events])

    async def test_scheduled_close_closes_market(self) -> None:
        created = await self.alice.create_market(
            self.alice_context,
            question="Will scheduled close work?",
            close_after_seconds=1,
        )

        market = await self.wait_for_market_status(created.market_id, "closed")
        audit = await self.alice.audit_log(
            self.alice_context,
            market_id=created.market_id,
        )

        self.assertEqual(market.status, "closed")
        self.assertIn("market_closed", [event.event_type for event in audit.events])


if __name__ == "__main__":
    unittest.main()
