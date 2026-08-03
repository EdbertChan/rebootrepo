from __future__ import annotations

from dataclasses import dataclass
from typing import Awaitable, Callable, Optional
from uuid import NAMESPACE_URL, uuid5

from predictions.v1.predictions import PaymentAttempt
from predictions.v1.predictions_rbt import (
    AuditEvent,
    Bet,
    Market,
    MarketCatalog,
    PaymentIntent,
    User,
)
from reboot.aio.contexts import ReaderContext, TransactionContext, WorkflowContext, WriterContext
from reboot.aio.workflows import PER_WORKFLOW, at_least_once
from reboot.std.collections.ordered_map.v1.ordered_map import OrderedMap


INITIAL_CREDITS = 0
CATALOG_MARKET_INDEX_ID = "catalog:markets"
MAX_PAYMENT_ATTEMPTS = 3
MAX_PAYMENT_ATTEMPT_HISTORY = 3


@dataclass(frozen=True)
class GatewayOutcome:
    status: str
    failure_class: str = ""
    provider_transaction_id: str = ""
    message: str = ""


PaymentGateway = Callable[[str, int, str, int], Awaitable[GatewayOutcome]]


async def _default_gateway_charge(
    payment_intent_id: str,
    amount: int,
    idempotency_key: str,
    attempt_number: int,
) -> GatewayOutcome:
    del amount, idempotency_key, attempt_number
    return GatewayOutcome(
        status="success",
        provider_transaction_id=f"simulated:{payment_intent_id}",
    )


_gateway_charge: PaymentGateway = _default_gateway_charge


def configure_gateway_for_tests(charge: PaymentGateway) -> None:
    global _gateway_charge
    _gateway_charge = charge


def reset_gateway_for_tests() -> None:
    global _gateway_charge
    _gateway_charge = _default_gateway_charge


def _payment_idempotency_key(payment_intent_id: str) -> str:
    return str(uuid5(NAMESPACE_URL, f"payment-intent:{payment_intent_id}"))


def _user_created_market_index_id(user_id: str) -> str:
    return f"user:{user_id}:created-markets"


def _user_bet_index_id(user_id: str) -> str:
    return f"user:{user_id}:bets"


def _user_payment_index_id(user_id: str) -> str:
    return f"user:{user_id}:payments"


def _market_bet_index_id(market_id: str) -> str:
    return f"market:{market_id}:bets"


def _market_audit_index_id(market_id: str) -> str:
    return f"market:{market_id}:audit"


async def _insert_id(
    context: TransactionContext,
    map_id: str,
    key: str,
    value_id: str,
) -> None:
    await OrderedMap.ref(map_id).insert(
        context,
        key=key,
        bytes=value_id.encode(),
    )


class UserServicer(User.Servicer):
    async def create(self, context: TransactionContext) -> None:
        if context.constructor:
            user_id = self.ref().state_id
            self.state.balance = INITIAL_CREDITS
            self.state.created_market_index_id = _user_created_market_index_id(user_id)
            self.state.bet_index_id = _user_bet_index_id(user_id)
            self.state.payment_index_id = _user_payment_index_id(user_id)
            self.state.settled_payment_intent_ids = []

    async def dashboard(
        self,
        context: ReaderContext,
        request: User.DashboardRequest,
    ) -> User.DashboardResponse:
        del context, request
        return User.DashboardResponse(
            user_id=self.ref().state_id,
            balance=self.state.balance,
            markets=[],
            bets=[],
            payments=[],
            next_cursor="",
        )

    async def create_market(
        self,
        context: TransactionContext,
        request: User.CreateMarketRequest,
    ) -> User.CreateMarketResponse:
        del context, request
        return User.CreateMarketResponse(market_id="")

    async def place_bet(
        self,
        context: TransactionContext,
        request: User.PlaceBetRequest,
    ) -> User.PlaceBetResponse:
        del context, request
        return User.PlaceBetResponse(bet_id="", balance=self.state.balance)

    async def resolve_market(
        self,
        context: TransactionContext,
        request: User.ResolveMarketRequest,
    ) -> User.ResolveMarketResponse:
        del context, request
        return User.ResolveMarketResponse(
            winner_count=0,
            loser_count=0,
            payout_count=0,
        )

    async def close_market(
        self,
        context: TransactionContext,
        request: User.CloseMarketRequest,
    ) -> User.CloseMarketResponse:
        del context, request
        return User.CloseMarketResponse(status="")

    async def audit_log(
        self,
        context: ReaderContext,
        request: User.AuditLogRequest,
    ) -> User.AuditLogResponse:
        del context, request
        return User.AuditLogResponse(events=[], next_cursor="")

    async def apply_payout(
        self,
        context: WriterContext,
        request: User.ApplyPayoutRequest,
    ) -> None:
        del context, request

    async def add_payment(
        self,
        context: TransactionContext,
        request: User.AddPaymentRequest,
    ) -> None:
        if request.payment_intent_id == "":
            return
        if self.state.payment_index_id == "":
            self.state.payment_index_id = _user_payment_index_id(self.ref().state_id)
        await _insert_id(
            context,
            self.state.payment_index_id,
            request.payment_intent_id,
            request.payment_intent_id,
        )


class MarketCatalogServicer(MarketCatalog.Servicer):
    async def ensure(
        self,
        context: TransactionContext,
    ) -> MarketCatalog.EnsureResponse:
        del context
        if self.state.market_index_id == "":
            self.state.market_index_id = CATALOG_MARKET_INDEX_ID
        return MarketCatalog.EnsureResponse(
            market_index_id=self.state.market_index_id,
        )

    async def add_market(
        self,
        context: TransactionContext,
        request: MarketCatalog.AddMarketRequest,
    ) -> None:
        if self.state.market_index_id == "":
            self.state.market_index_id = CATALOG_MARKET_INDEX_ID
        if request.market_id == "":
            return
        await _insert_id(
            context,
            self.state.market_index_id,
            request.market_id,
            request.market_id,
        )


class MarketServicer(Market.Servicer):
    async def create(
        self,
        context: WriterContext,
        request: Market.CreateRequest,
    ) -> None:
        if not context.constructor:
            return
        market_id = self.ref().state_id
        self.state.creator_user_id = request.creator_user_id
        self.state.question = request.question
        self.state.status = ""
        self.state.close_after_seconds = request.close_after_seconds
        self.state.winning_outcome = ""
        self.state.bet_index_id = _market_bet_index_id(market_id)
        self.state.audit_index_id = _market_audit_index_id(market_id)
        self.state.yes_total = 0
        self.state.no_total = 0
        self.state.bet_count = 0
        self.state.audit_sequence = 0
        self.state.payout_pending_count = 0
        self.state.payout_succeeded_count = 0
        self.state.payout_failed_count = 0

    async def get(self, context: ReaderContext) -> Market.GetResponse:
        del context
        return Market.GetResponse(
            market_id=self.ref().state_id,
            creator_user_id=self.state.creator_user_id,
            question=self.state.question,
            status=self.state.status,
            close_after_seconds=self.state.close_after_seconds,
            winning_outcome=self.state.winning_outcome,
            yes_total=self.state.yes_total,
            no_total=self.state.no_total,
            bet_count=self.state.bet_count,
            payout_pending_count=self.state.payout_pending_count,
            payout_succeeded_count=self.state.payout_succeeded_count,
            payout_failed_count=self.state.payout_failed_count,
            bet_index_id=self.state.bet_index_id,
            audit_index_id=self.state.audit_index_id,
        )

    async def add_bet(
        self,
        context: WriterContext,
        request: Market.AddBetRequest,
    ) -> None:
        del context, request

    async def close_if_due(self, context: WriterContext) -> None:
        del context

    @classmethod
    async def spawn_payouts(cls, context: WorkflowContext) -> None:
        del context

    async def resolve(
        self,
        context: WriterContext,
        request: Market.ResolveRequest,
    ) -> None:
        del context, request

    async def record_payout_pending(
        self,
        context: WriterContext,
        request: Market.RecordPayoutPendingRequest,
    ) -> None:
        del context, request

    async def record_payout_succeeded(
        self,
        context: WriterContext,
        request: Market.RecordPayoutSucceededRequest,
    ) -> None:
        del context, request

    async def record_payout_failed(
        self,
        context: WriterContext,
        request: Market.RecordPayoutFailedRequest,
    ) -> None:
        del context, request

    async def mark_review_required(
        self,
        context: WriterContext,
        request: Market.MarkReviewRequiredRequest,
    ) -> None:
        del context, request


class BetServicer(Bet.Servicer):
    async def create(
        self,
        context: WriterContext,
        request: Bet.CreateRequest,
    ) -> None:
        if not context.constructor:
            return
        self.state.user_id = request.user_id
        self.state.market_id = request.market_id
        self.state.outcome = request.outcome
        self.state.stake = request.stake
        self.state.payout_amount = 0
        self.state.status = ""
        self.state.payment_intent_id = ""

    async def get(self, context: ReaderContext) -> Bet.GetResponse:
        del context
        return Bet.GetResponse(
            bet_id=self.ref().state_id,
            user_id=self.state.user_id,
            market_id=self.state.market_id,
            outcome=self.state.outcome,
            stake=self.state.stake,
            payout_amount=self.state.payout_amount,
            status=self.state.status,
            payment_intent_id=self.state.payment_intent_id,
        )

    async def mark_won(
        self,
        context: WriterContext,
        request: Bet.MarkWonRequest,
    ) -> None:
        del context, request

    async def set_status(
        self,
        context: WriterContext,
        request: Bet.SetStatusRequest,
    ) -> None:
        del context, request


class PaymentIntentServicer(PaymentIntent.Servicer):
    async def create(
        self,
        context: WriterContext,
        request: PaymentIntent.CreateRequest,
    ) -> None:
        if not context.constructor:
            return
        self.state.user_id = request.user_id
        self.state.market_id = request.market_id
        self.state.bet_id = request.bet_id
        self.state.amount = request.amount
        self.state.status = "pending"
        self.state.idempotency_key = _payment_idempotency_key(self.ref().state_id)
        self.state.failure_class = ""
        self.state.attempts = []

    async def get(self, context: ReaderContext) -> PaymentIntent.GetResponse:
        del context
        return PaymentIntent.GetResponse(
            payment_intent_id=self.ref().state_id,
            bet_id=self.state.bet_id,
            market_id=self.state.market_id,
            user_id=self.state.user_id,
            amount=self.state.amount,
            status=self.state.status,
            idempotency_key=self.state.idempotency_key,
            failure_class=self.state.failure_class,
            attempts=self.state.attempts,
        )

    async def record_attempt(
        self,
        context: WriterContext,
        request: PaymentIntent.RecordAttemptRequest,
    ) -> None:
        del context
        if any(
            attempt.attempt_number == request.attempt_number
            for attempt in self.state.attempts
        ):
            return
        self.state.attempts.append(
            PaymentAttempt(
                attempt_number=request.attempt_number,
                status=request.status,
                failure_class=request.failure_class,
                provider_transaction_id=request.provider_transaction_id,
                message=request.message,
            )
        )
        self.state.attempts = self.state.attempts[-MAX_PAYMENT_ATTEMPT_HISTORY:]

    async def record_status(
        self,
        context: WriterContext,
        request: PaymentIntent.RecordStatusRequest,
    ) -> None:
        del context
        self.state.status = request.status
        self.state.failure_class = request.failure_class

    async def record_success(
        self,
        context: WriterContext,
        request: PaymentIntent.RecordSuccessRequest,
    ) -> None:
        del context, request
        self.state.status = "succeeded"
        self.state.failure_class = ""

    @classmethod
    async def run(cls, context: WorkflowContext) -> None:
        payment_intent_id = context.state_id
        payment = PaymentIntent.ref(payment_intent_id)

        current = await payment.per_workflow("load-payment-intent").get(context)
        if current.status in {"succeeded", "failed", "retry_exhausted"}:
            return

        await payment.per_workflow("mark-processing").record_status(
            context,
            status="processing",
            failure_class="",
        )

        idempotency_key = current.idempotency_key
        if idempotency_key == "":
            idempotency_key = _payment_idempotency_key(payment_intent_id)

        next_attempt = len(current.attempts) + 1
        while next_attempt <= MAX_PAYMENT_ATTEMPTS:
            attempt_number = next_attempt

            async def charge() -> GatewayOutcome:
                return await _gateway_charge(
                    payment_intent_id,
                    current.amount,
                    idempotency_key,
                    attempt_number,
                )

            outcome = await at_least_once(
                (f"charge-gateway-attempt-{attempt_number}", PER_WORKFLOW),
                context,
                charge,
                type=GatewayOutcome,
            )
            await payment.per_workflow(
                f"record-gateway-attempt-{attempt_number}"
            ).record_attempt(
                context,
                attempt_number=attempt_number,
                status=outcome.status,
                failure_class=outcome.failure_class,
                provider_transaction_id=outcome.provider_transaction_id,
                message=outcome.message,
            )

            if outcome.status == "success":
                await payment.per_workflow("mark-succeeded").record_success(
                    context,
                    provider_transaction_id=outcome.provider_transaction_id,
                )
                return

            if outcome.status in {"permanent_failure", "business_failure"}:
                await payment.per_workflow("mark-failed").record_status(
                    context,
                    status="failed",
                    failure_class=outcome.failure_class,
                )
                return

            if outcome.status != "retryable_failure":
                await payment.per_workflow("mark-unknown-failed").record_status(
                    context,
                    status="failed",
                    failure_class=outcome.failure_class or "unknown_gateway_status",
                )
                return

            next_attempt += 1

        await payment.per_workflow("mark-retry-exhausted").record_status(
            context,
            status="retry_exhausted",
            failure_class="retry_exhausted",
        )


class AuditEventServicer(AuditEvent.Servicer):
    async def create(
        self,
        context: WriterContext,
        request: AuditEvent.CreateRequest,
    ) -> None:
        if not context.constructor:
            return
        self.state.market_id = request.market_id
        self.state.actor_user_id = request.actor_user_id
        self.state.event_type = request.event_type
        self.state.message = request.message
        self.state.bet_id = request.bet_id
        self.state.payment_intent_id = request.payment_intent_id
        self.state.sequence = request.sequence

    async def get(self, context: ReaderContext) -> AuditEvent.GetResponse:
        del context
        return AuditEvent.GetResponse(
            audit_event_id=self.ref().state_id,
            market_id=self.state.market_id,
            actor_user_id=self.state.actor_user_id,
            event_type=self.state.event_type,
            message=self.state.message,
            bet_id=self.state.bet_id,
            payment_intent_id=self.state.payment_intent_id,
            sequence=self.state.sequence,
        )

    async def update(
        self,
        context: WriterContext,
        request: AuditEvent.UpdateRequest,
    ) -> None:
        del context
        self.state.event_type = request.event_type
        self.state.message = request.message
        self.state.bet_id = request.bet_id
        self.state.payment_intent_id = request.payment_intent_id
        self.state.sequence = request.sequence


APPLICATION_SERVICERS = [
    UserServicer,
    MarketCatalogServicer,
    MarketServicer,
    BetServicer,
    PaymentIntentServicer,
    AuditEventServicer,
]
