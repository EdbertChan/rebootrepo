import asyncio
import unittest

from reboot.aio.aborted import Aborted
from reboot.aio.applications import Application
from reboot.aio.contexts import EffectValidation
from reboot.aio.tests import Reboot
from reboot.std.collections.ordered_map.v1.ordered_map import ordered_map_library

from predictions.v1.predictions_rbt import User
from servicers.predictions import APPLICATION_SERVICERS


class RaceConditionTest(unittest.IsolatedAsyncioTestCase):
    """Fires concurrent, independently-authenticated calls at the same
    market to prove Reboot's per-actor serialization keeps resolution,
    closure, and market-total bookkeeping race-free, in addition to the
    balance race already covered for bet placement.
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
        await self.rbt.stop()

    def application(self) -> Application:
        return Application(
            servicers=APPLICATION_SERVICERS,
            libraries=[ordered_map_library()],
        )

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

    async def test_concurrent_resolve_calls_settle_the_market_exactly_once(
        self,
    ) -> None:
        market = await self.alice.create_market(
            self.alice_context,
            question="Will concurrent resolve calls settle exactly once?",
            close_after_seconds=0,
        )
        await self.bob.place_bet(
            self.bob_context,
            market_id=market.market_id,
            outcome="YES",
            stake=100,
        )
        await self.alice.close_market(self.alice_context, market_id=market.market_id)

        attempts = 5
        # Each concurrent attempt needs its own external context: reusing
        # one context for several concurrent mutating calls makes the SDK
        # treat later ones as retries of the first instead of independent
        # racing callers.
        contexts = [
            await self.rbt.create_external_context_as(
                f"resolve-attempt-{i}",
                user_id="alice",
            )
            for i in range(attempts)
        ]

        results = await asyncio.gather(
            *[
                # A fresh `User.ref("alice")` per concurrent call: reusing
                # one `WeakReference` across concurrent contexts raises
                # `MixedContextsError` rather than exercising the race.
                User.ref("alice").resolve_market(
                    context,
                    market_id=market.market_id,
                    winning_outcome="YES",
                )
                for context in contexts
            ],
            return_exceptions=True,
        )

        successes = [r for r in results if not isinstance(r, BaseException)]
        failures = [r for r in results if isinstance(r, BaseException)]
        for failure in failures:
            self.assertIsInstance(failure, Aborted)

        self.assertEqual(len(successes), 1)
        self.assertEqual(len(failures), attempts - 1)
        self.assertEqual(successes[0].winner_count, 1)
        self.assertEqual(successes[0].payout_count, 1)

        alice_dashboard = await self.alice.dashboard(self.alice_context)
        self.assertEqual(alice_dashboard.markets[0].status, "resolved")

        bob_dashboard = await self.bob.dashboard(self.bob_context)
        # Exactly one PaymentIntent was ever created for the winning bet,
        # regardless of how many resolve attempts raced.
        self.assertEqual(len(bob_dashboard.payments), 1)

        payment_id = bob_dashboard.payments[0].payment_intent_id
        bob_dashboard, payment = await self.wait_for_payment_status(
            payment_id, {"succeeded"}
        )
        self.assertEqual(bob_dashboard.balance, 1100)
        self.assertEqual(payment.status, "succeeded")

    async def test_concurrent_close_calls_record_audit_exactly_once(self) -> None:
        market = await self.alice.create_market(
            self.alice_context,
            question="Will concurrent close calls append the audit event exactly once?",
            close_after_seconds=0,
        )

        attempts = 2
        contexts = [
            await self.rbt.create_external_context_as(
                f"close-attempt-{i}",
                user_id="alice",
            )
            for i in range(attempts)
        ]

        results = await asyncio.gather(
            *[
                User.ref("alice").close_market(context, market_id=market.market_id)
                for context in contexts
            ],
            return_exceptions=True,
        )

        successes = [r for r in results if not isinstance(r, BaseException)]
        failures = [r for r in results if isinstance(r, BaseException)]
        self.assertEqual(failures, [])
        for result in successes:
            self.assertEqual(result.status, "closed")

        dashboard = await self.alice.dashboard(self.alice_context)
        self.assertEqual(dashboard.markets[0].status, "closed")

        audit = await self.alice.audit_log(
            self.alice_context,
            market_id=market.market_id,
        )
        closed_events = [
            event for event in audit.events if event.event_type == "market_closed"
        ]
        self.assertEqual(len(closed_events), 1)

    async def test_concurrent_bets_from_different_users_never_lose_market_totals(
        self,
    ) -> None:
        market = await self.alice.create_market(
            self.alice_context,
            question=(
                "Will concurrent bets from different users update market "
                "totals without lost writes?"
            ),
            close_after_seconds=0,
        )

        bettor_count = 8
        stake = 50
        contexts = []
        users = []
        for i in range(bettor_count):
            user_id = f"bettor-{i}"
            context = await self.rbt.create_external_context_as(
                user_id,
                user_id=user_id,
            )
            contexts.append(context)
            users.append(User.ref(user_id))

        results = await asyncio.gather(
            *[
                user.place_bet(
                    context,
                    market_id=market.market_id,
                    outcome="YES" if i % 2 == 0 else "NO",
                    stake=stake,
                )
                for i, (user, context) in enumerate(zip(users, contexts))
            ],
            return_exceptions=True,
        )

        failures = [r for r in results if isinstance(r, BaseException)]
        self.assertEqual(failures, [])

        dashboard = await self.alice.dashboard(self.alice_context)
        yes_bettors = sum(1 for i in range(bettor_count) if i % 2 == 0)
        no_bettors = bettor_count - yes_bettors

        self.assertEqual(dashboard.markets[0].bet_count, bettor_count)
        self.assertEqual(
            dashboard.markets[0].yes_total + dashboard.markets[0].no_total,
            bettor_count * stake,
        )
        self.assertEqual(dashboard.markets[0].yes_total, yes_bettors * stake)
        self.assertEqual(dashboard.markets[0].no_total, no_bettors * stake)


if __name__ == "__main__":
    unittest.main()
