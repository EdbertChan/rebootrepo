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
    ):
        deadline = asyncio.get_running_loop().time() + timeout_seconds
        while asyncio.get_running_loop().time() < deadline:
            market = await Market.ref(market_id).get(self.alice_context)
            if market.status == status:
                return market
            await asyncio.sleep(0.1)
        self.fail(f"Market {market_id} did not reach status {status!r}.")

    async def test_create_list_read_and_manual_close_market(self) -> None:
        created = await self.alice.create_market(
            self.alice_context,
            question="Will the market lifecycle work?",
            close_after_seconds=0,
        )

        listed_for_bob = await self.bob.dashboard(self.bob_context)
        listed_for_alice = await self.alice.dashboard(self.alice_context)
        market = await Market.ref(created.market_id).get(self.alice_context)

        self.assertEqual([summary.market_id for summary in listed_for_bob.markets], [created.market_id])
        self.assertEqual([summary.market_id for summary in listed_for_alice.markets], [created.market_id])
        self.assertEqual(market.creator_user_id, "alice")
        self.assertEqual(market.question, "Will the market lifecycle work?")
        self.assertEqual(market.status, "open")

        with self.assertRaises(Aborted):
            await self.bob.close_market(
                self.bob_context,
                market_id=created.market_id,
            )

        closed = await self.alice.close_market(
            self.alice_context,
            market_id=created.market_id,
        )
        audit = await self.alice.audit_log(
            self.alice_context,
            market_id=created.market_id,
        )

        self.assertEqual(closed.status, "closed")
        self.assertEqual(
            [event.event_type for event in audit.events],
            ["market_created", "market_closed"],
        )

    async def test_market_auto_closes_after_schedule(self) -> None:
        created = await self.alice.create_market(
            self.alice_context,
            question="Will scheduled close run?",
            close_after_seconds=1,
        )
        market = await Market.ref(created.market_id).get(self.alice_context)
        self.assertEqual(market.status, "open")

        closed = await self.wait_for_market_status(created.market_id, "closed")
        audit = await self.alice.audit_log(
            self.alice_context,
            market_id=created.market_id,
        )

        self.assertEqual(closed.status, "closed")
        self.assertEqual(
            [event.event_type for event in audit.events],
            ["market_created", "market_closed"],
        )

