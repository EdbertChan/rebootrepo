import asyncio
import unittest

from reboot.aio.aborted import Aborted
from reboot.aio.applications import Application
from reboot.aio.contexts import EffectValidation
from reboot.aio.tests import Reboot
from reboot.std.collections.ordered_map.v1.ordered_map import ordered_map_library

from predictions.v1.predictions_rbt import User
from servicers.predictions import APPLICATION_SERVICERS


class MarketLifecycleTest(unittest.IsolatedAsyncioTestCase):
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

    async def test_create_market_appears_in_global_catalog_for_other_users(
        self,
    ) -> None:
        created = await self.alice.create_market(
            self.alice_context,
            question="  Will   markets    list globally?  ",
            close_after_seconds=0,
        )

        bob_dashboard = await self.bob.dashboard(self.bob_context)

        self.assertEqual(len(bob_dashboard.markets), 1)
        listed = bob_dashboard.markets[0]
        self.assertEqual(listed.market_id, created.market_id)
        self.assertEqual(listed.creator_user_id, "alice")
        self.assertEqual(listed.question, "Will markets list globally?")
        self.assertEqual(listed.status, "open")
        self.assertEqual(listed.yes_total, 0)
        self.assertEqual(listed.no_total, 0)
        self.assertEqual(listed.bet_count, 0)

    async def test_create_market_rejects_blank_question(self) -> None:
        with self.assertRaises(Aborted):
            await self.alice.create_market(
                self.alice_context,
                question="   ",
                close_after_seconds=0,
            )

    async def test_only_creator_can_close_their_market(self) -> None:
        market = await self.alice.create_market(
            self.alice_context,
            question="Will only the creator be able to close this?",
            close_after_seconds=0,
        )

        with self.assertRaises(Aborted):
            await self.bob.close_market(
                self.bob_context,
                market_id=market.market_id,
            )

        dashboard = await self.alice.dashboard(self.alice_context)
        self.assertEqual(dashboard.markets[0].status, "open")

        closed = await self.alice.close_market(
            self.alice_context,
            market_id=market.market_id,
        )
        self.assertEqual(closed.status, "closed")

        dashboard = await self.alice.dashboard(self.alice_context)
        self.assertEqual(dashboard.markets[0].status, "closed")

    async def test_close_market_is_idempotent_and_records_one_audit_row(
        self,
    ) -> None:
        market = await self.alice.create_market(
            self.alice_context,
            question="Will closing twice only log once?",
            close_after_seconds=0,
        )

        await self.alice.close_market(
            self.alice_context,
            market_id=market.market_id,
        )
        await self.alice.close_market(
            self.alice_context,
            market_id=market.market_id,
        )

        audit = await self.alice.audit_log(
            self.alice_context,
            market_id=market.market_id,
        )
        close_events = [
            event for event in audit.events if event.event_type == "market_closed"
        ]
        self.assertEqual(len(close_events), 1)
        event_types = [event.event_type for event in audit.events]
        self.assertEqual(event_types, ["market_created", "market_closed"])

    async def test_market_auto_closes_after_scheduled_delay(self) -> None:
        market = await self.alice.create_market(
            self.alice_context,
            question="Will this market auto-close on schedule?",
            close_after_seconds=1,
        )

        dashboard = await self.bob.dashboard(self.bob_context)
        self.assertEqual(dashboard.markets[0].status, "open")

        deadline = asyncio.get_running_loop().time() + 15
        status = ""
        while asyncio.get_running_loop().time() < deadline:
            dashboard = await self.bob.dashboard(self.bob_context)
            status = dashboard.markets[0].status
            if status == "closed":
                break
            await asyncio.sleep(0.2)

        self.assertEqual(status, "closed")
        audit = await self.alice.audit_log(
            self.alice_context,
            market_id=market.market_id,
        )
        self.assertIn("market_created", [event.event_type for event in audit.events])

    async def test_dashboard_pagination_returns_cursor_for_more_markets(
        self,
    ) -> None:
        created_ids = []
        for index in range(3):
            created = await self.alice.create_market(
                self.alice_context,
                question=f"Will page {index} load correctly?",
                close_after_seconds=0,
            )
            created_ids.append(created.market_id)

        first_page = await self.bob.dashboard(self.bob_context, limit=2)
        self.assertEqual(len(first_page.markets), 2)
        self.assertNotEqual(first_page.next_cursor, "")

        second_page = await self.bob.dashboard(
            self.bob_context,
            limit=2,
            cursor=first_page.next_cursor,
        )
        self.assertGreaterEqual(len(second_page.markets), 1)

        seen_ids = {m.market_id for m in first_page.markets} | {
            m.market_id for m in second_page.markets
        }
        self.assertTrue(set(created_ids).issubset(seen_ids))
