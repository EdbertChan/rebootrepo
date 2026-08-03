import unittest

from reboot.aio.aborted import Aborted
from reboot.aio.applications import Application
from reboot.aio.contexts import EffectValidation
from reboot.aio.tests import Reboot
from reboot.std.collections.ordered_map.v1.ordered_map import ordered_map_library

from predictions.v1.predictions_rbt import User
from servicers.predictions import APPLICATION_SERVICERS


class PredictionMarketContractTest(unittest.IsolatedAsyncioTestCase):
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

    async def test_user_auto_constructs_with_initial_credits(self) -> None:
        dashboard = await self.alice.dashboard(self.alice_context)
        self.assertEqual(dashboard.balance, 1000)
        self.assertEqual(dashboard.markets, [])
        self.assertEqual(dashboard.bets, [])
        self.assertEqual(dashboard.payments, [])

    async def test_create_market_appears_in_every_users_dashboard(self) -> None:
        created = await self.alice.create_market(
            self.alice_context,
            question="Will the state contracts slice stay skeleton-only?",
            close_after_seconds=0,
        )

        bob_dashboard = await self.bob.dashboard(self.bob_context)

        self.assertEqual(len(bob_dashboard.markets), 1)
        self.assertEqual(bob_dashboard.markets[0].market_id, created.market_id)
        self.assertEqual(bob_dashboard.markets[0].status, "open")
        self.assertEqual(bob_dashboard.markets[0].yes_total, 0)
        self.assertEqual(bob_dashboard.markets[0].no_total, 0)

    async def test_create_market_writes_an_audit_event(self) -> None:
        created = await self.alice.create_market(
            self.alice_context,
            question="Does market creation write an audit trail?",
            close_after_seconds=0,
        )

        audit = await self.alice.audit_log(
            self.alice_context,
            market_id=created.market_id,
        )

        self.assertEqual(len(audit.events), 1)
        self.assertEqual(audit.events[0].event_type, "market_created")

    async def test_creator_can_close_an_open_market(self) -> None:
        created = await self.alice.create_market(
            self.alice_context,
            question="Can the creator close their own market?",
            close_after_seconds=0,
        )

        await self.alice.close_market(self.alice_context, market_id=created.market_id)
        dashboard = await self.alice.dashboard(self.alice_context)

        self.assertEqual(dashboard.markets[0].status, "closed")

    async def test_non_creator_cannot_close_market(self) -> None:
        created = await self.alice.create_market(
            self.alice_context,
            question="Can a stranger close someone else's market?",
            close_after_seconds=0,
        )

        with self.assertRaises(Aborted):
            await self.bob.close_market(self.bob_context, market_id=created.market_id)

    async def test_place_bet_is_not_yet_implemented(self) -> None:
        created = await self.alice.create_market(
            self.alice_context,
            question="Is bet placement live in this slice?",
            close_after_seconds=0,
        )

        with self.assertRaises(Aborted):
            await self.bob.place_bet(
                self.bob_context,
                market_id=created.market_id,
                outcome="YES",
                stake=10,
            )

        # No credits moved: the stub never touches the balance.
        dashboard = await self.bob.dashboard(self.bob_context)
        self.assertEqual(dashboard.balance, 1000)

    async def test_resolve_market_is_not_yet_implemented(self) -> None:
        created = await self.alice.create_market(
            self.alice_context,
            question="Is resolution live in this slice?",
            close_after_seconds=0,
        )
        await self.alice.close_market(self.alice_context, market_id=created.market_id)

        with self.assertRaises(Aborted):
            await self.alice.resolve_market(
                self.alice_context,
                market_id=created.market_id,
                winning_outcome="YES",
            )
