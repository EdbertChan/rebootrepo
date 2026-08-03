import asyncio
import unittest

from reboot.aio.aborted import Aborted
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
    ) -> None:
        deadline = asyncio.get_running_loop().time() + timeout_seconds
        while asyncio.get_running_loop().time() < deadline:
            market = await Market.ref(market_id).get(self.alice_context)
            if market.status == status:
                return
            await asyncio.sleep(0.1)
        self.fail(f"Market {market_id} did not reach status {status}.")

    async def test_create_market_lists_globally_and_records_audit(self) -> None:
        empty_dashboard = await self.alice.dashboard(self.alice_context)
        self.assertEqual(empty_dashboard.markets, [])

        created = await self.alice.create_market(
            self.alice_context,
            question="  Will lifecycle behavior ship?  ",
            close_after_seconds=0,
        )

        market = await Market.ref(created.market_id).get(self.alice_context)
        alice_dashboard = await self.alice.dashboard(self.alice_context)
        bob_dashboard = await self.bob.dashboard(self.bob_context)
        audit = await self.alice.audit_log(
            self.alice_context,
            market_id=created.market_id,
        )

        self.assertEqual(market.market_id, created.market_id)
        self.assertEqual(market.creator_user_id, "alice")
        self.assertEqual(market.question, "Will lifecycle behavior ship?")
        self.assertEqual(market.status, "open")
        self.assertEqual(
            [market_summary.market_id for market_summary in alice_dashboard.markets],
            [created.market_id],
        )
        self.assertEqual(
            [market_summary.market_id for market_summary in bob_dashboard.markets],
            [created.market_id],
        )
        self.assertEqual(alice_dashboard.balance, empty_dashboard.balance)
        self.assertEqual(
            [
                (event.sequence, event.event_type, event.actor_user_id)
                for event in audit.events
            ],
            [(1, "market_created", "alice")],
        )

    async def test_only_creator_can_close_market_and_audit_is_ordered(self) -> None:
        created = await self.alice.create_market(
            self.alice_context,
            question="Will creator close controls work?",
            close_after_seconds=0,
        )

        with self.assertRaises(Aborted):
            await self.bob.close_market(
                self.bob_context,
                market_id=created.market_id,
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
        self.assertEqual(
            [(event.sequence, event.event_type) for event in audit.events],
            [(1, "market_created"), (2, "market_closed")],
        )

    async def test_market_auto_closes_after_scheduled_delay(self) -> None:
        created = await self.alice.create_market(
            self.alice_context,
            question="Will scheduled close run?",
            close_after_seconds=1,
        )

        await self.wait_for_market_status(created.market_id, "closed")
        dashboard = await self.bob.dashboard(self.bob_context)
        audit = await self.alice.audit_log(
            self.alice_context,
            market_id=created.market_id,
        )

        self.assertEqual(dashboard.markets[0].status, "closed")
        self.assertEqual(
            [(event.sequence, event.event_type) for event in audit.events],
            [(1, "market_created"), (2, "market_closed")],
        )
