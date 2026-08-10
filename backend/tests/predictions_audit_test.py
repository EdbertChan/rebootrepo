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
    reset_gateway_for_tests,
)


class AuditLogTest(unittest.IsolatedAsyncioTestCase):
    """Proves `User.audit_log` returns a durable, chronologically ordered
    timeline of `AuditEvent` actors covering the full market lifecycle,
    from creation through bet placement, resolution, and payout.
    """

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
        reset_gateway_for_tests()
        await self.rbt.stop()

    def application(self) -> Application:
        return Application(
            servicers=APPLICATION_SERVICERS,
            libraries=[ordered_map_library()],
        )

    async def wait_for_event_types(
        self,
        market_id: str,
        expected_event_types: list[str],
        *,
        timeout_seconds: float = 20,
    ):
        deadline = asyncio.get_running_loop().time() + timeout_seconds
        audit = await self.alice.audit_log(self.alice_context, market_id=market_id)
        while asyncio.get_running_loop().time() < deadline:
            event_types = [event.event_type for event in audit.events]
            if event_types == expected_event_types:
                return audit
            await asyncio.sleep(0.1)
            audit = await self.alice.audit_log(
                self.alice_context, market_id=market_id
            )
        self.fail(
            "Audit log never reached the expected event order.\n"
            f"expected: {expected_event_types}\n"
            f"actual:   {[event.event_type for event in audit.events]}"
        )

    async def test_audit_log_order_for_a_successful_payout(self) -> None:
        async def always_succeeds(
            payment_intent_id: str,
            amount: int,
            idempotency_key: str,
            attempt_number: int,
        ) -> GatewayOutcome:
            return GatewayOutcome(
                status="success",
                provider_transaction_id=f"simulated:{payment_intent_id}",
            )

        configure_gateway_for_tests(always_succeeds)

        market = await self.alice.create_market(
            self.alice_context,
            question="Will the audit log capture a full winning lifecycle?",
            close_after_seconds=0,
        )
        bet = await self.bob.place_bet(
            self.bob_context,
            market_id=market.market_id,
            outcome="YES",
            stake=100,
        )
        await self.alice.close_market(
            self.alice_context,
            market_id=market.market_id,
        )
        await self.alice.resolve_market(
            self.alice_context,
            market_id=market.market_id,
            winning_outcome="YES",
        )

        audit = await self.wait_for_event_types(
            market.market_id,
            [
                "market_created",
                "bet_placed",
                "market_closed",
                "market_resolved",
                "payout_attempted",
                "payout_succeeded",
            ],
        )

        bet_placed_event = audit.events[1]
        self.assertEqual(bet_placed_event.bet_id, bet.bet_id)
        self.assertEqual(bet_placed_event.actor_user_id, "bob")

        payout_events = audit.events[4:]
        for event in payout_events:
            self.assertEqual(event.bet_id, bet.bet_id)
            self.assertNotEqual(event.payment_intent_id, "")

    async def test_audit_log_order_for_a_failed_payout(self) -> None:
        async def always_declines(
            payment_intent_id: str,
            amount: int,
            idempotency_key: str,
            attempt_number: int,
        ) -> GatewayOutcome:
            return GatewayOutcome(
                status="permanent_failure",
                failure_class="invalid_account",
                message="destination account no longer exists",
            )

        configure_gateway_for_tests(always_declines)

        market = await self.alice.create_market(
            self.alice_context,
            question="Will the audit log capture a failed payout?",
            close_after_seconds=0,
        )
        await self.bob.place_bet(
            self.bob_context,
            market_id=market.market_id,
            outcome="YES",
            stake=100,
        )
        await self.alice.close_market(
            self.alice_context,
            market_id=market.market_id,
        )
        await self.alice.resolve_market(
            self.alice_context,
            market_id=market.market_id,
            winning_outcome="YES",
        )

        await self.wait_for_event_types(
            market.market_id,
            [
                "market_created",
                "bet_placed",
                "market_closed",
                "market_resolved",
                "payout_attempted",
                "payout_failed",
            ],
        )

    async def test_audit_log_is_scoped_per_market(self) -> None:
        market_a = await self.alice.create_market(
            self.alice_context,
            question="Will market A's audit log stay separate from B's?",
            close_after_seconds=0,
        )
        market_b = await self.alice.create_market(
            self.alice_context,
            question="Will market B's audit log stay separate from A's?",
            close_after_seconds=0,
        )

        audit_a = await self.alice.audit_log(
            self.alice_context, market_id=market_a.market_id
        )
        audit_b = await self.alice.audit_log(
            self.alice_context, market_id=market_b.market_id
        )

        self.assertEqual(
            [event.event_type for event in audit_a.events], ["market_created"]
        )
        self.assertEqual(
            [event.event_type for event in audit_b.events], ["market_created"]
        )
        self.assertNotEqual(
            audit_a.events[0].audit_event_id, audit_b.events[0].audit_event_id
        )


if __name__ == "__main__":
    unittest.main()
