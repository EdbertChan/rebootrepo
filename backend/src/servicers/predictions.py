from __future__ import annotations

from dataclasses import dataclass
from typing import Awaitable, Callable

from predictions.v1.predictions import PredictionError
from predictions.v1.predictions_rbt import (
    AuditEvent,
    Bet,
    Market,
    MarketCatalog,
    PaymentIntent,
    User,
)
from reboot.aio.contexts import ReaderContext, TransactionContext, WorkflowContext, WriterContext
from reboot.std.collections.ordered_map.v1.ordered_map import OrderedMap


INITIAL_CREDITS = 0
CATALOG_MARKET_INDEX_ID = "catalog:markets"


@dataclass(frozen=True)
class GatewayOutcome:
    status: str
    failure_class: str = ""
    provider_transaction_id: str = ""
    message: str = ""


# Extension point for a future payment-execution slice. Unused by this
# skeleton: `PaymentIntentServicer.run` never calls it.
GatewayCharge = Callable[[str, int, str, int], Awaitable[GatewayOutcome]]


async def _default_gateway_charge(
    payment_intent_id: str,
    amount: int,
    idempotency_key: str,
    attempt_number: int,
) -> GatewayOutcome:
    return GatewayOutcome(status="success")


gateway_charge: GatewayCharge = _default_gateway_charge


def configure_gateway_for_tests(charge: GatewayCharge) -> None:
    global gateway_charge
    gateway_charge = charge


def reset_gateway_for_tests() -> None:
    global gateway_charge
    gateway_charge = _default_gateway_charge


def _prediction_error(code: str, message: str) -> PredictionError:
    return PredictionError(code=code, message=message)


def _require_signed_in_user(
    context: ReaderContext | TransactionContext | WriterContext,
    expected_user_id: str,
    aborted_type: type[Exception],
) -> str:
    if context.auth is None or context.auth.user_id is None:
        raise aborted_type(
            _prediction_error("unauthenticated", "Sign in before using markets.")
        )
    if context.auth.user_id != expected_user_id:
        raise aborted_type(
            _prediction_error("permission_denied", "Use your own User actor.")
        )
    return context.auth.user_id


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
        user_id = self.ref().state_id
        _require_signed_in_user(context, user_id, User.DashboardAborted)
        return User.DashboardResponse(
            user_id=user_id,
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
        user_id = self.ref().state_id
        _require_signed_in_user(context, user_id, User.CreateMarketAborted)
        return User.CreateMarketResponse(market_id="")

    async def place_bet(
        self,
        context: TransactionContext,
        request: User.PlaceBetRequest,
    ) -> User.PlaceBetResponse:
        user_id = self.ref().state_id
        _require_signed_in_user(context, user_id, User.PlaceBetAborted)
        return User.PlaceBetResponse(bet_id="", balance=self.state.balance)

    async def resolve_market(
        self,
        context: TransactionContext,
        request: User.ResolveMarketRequest,
    ) -> User.ResolveMarketResponse:
        user_id = self.ref().state_id
        _require_signed_in_user(context, user_id, User.ResolveMarketAborted)
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
        user_id = self.ref().state_id
        _require_signed_in_user(context, user_id, User.CloseMarketAborted)
        return User.CloseMarketResponse(status="")

    async def audit_log(
        self,
        context: ReaderContext,
        request: User.AuditLogRequest,
    ) -> User.AuditLogResponse:
        user_id = self.ref().state_id
        _require_signed_in_user(context, user_id, User.AuditLogAborted)
        return User.AuditLogResponse(events=[], next_cursor="")

    async def apply_payout(
        self,
        context: WriterContext,
        request: User.ApplyPayoutRequest,
    ) -> None:
        pass

    async def add_payment(
        self,
        context: TransactionContext,
        request: User.AddPaymentRequest,
    ) -> None:
        pass


class MarketCatalogServicer(MarketCatalog.Servicer):
    async def ensure(
        self,
        context: TransactionContext,
    ) -> MarketCatalog.EnsureResponse:
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
        pass

    async def close_if_due(self, context: WriterContext) -> None:
        pass

    @classmethod
    async def spawn_payouts(cls, context: WorkflowContext) -> None:
        pass

    async def resolve(
        self,
        context: WriterContext,
        request: Market.ResolveRequest,
    ) -> None:
        pass

    async def record_payout_pending(
        self,
        context: WriterContext,
        request: Market.RecordPayoutPendingRequest,
    ) -> None:
        pass

    async def record_payout_succeeded(
        self,
        context: WriterContext,
        request: Market.RecordPayoutSucceededRequest,
    ) -> None:
        pass

    async def record_payout_failed(
        self,
        context: WriterContext,
        request: Market.RecordPayoutFailedRequest,
    ) -> None:
        pass

    async def mark_review_required(
        self,
        context: WriterContext,
        request: Market.MarkReviewRequiredRequest,
    ) -> None:
        pass


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
        pass

    async def set_status(
        self,
        context: WriterContext,
        request: Bet.SetStatusRequest,
    ) -> None:
        pass


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
        self.state.status = ""
        self.state.idempotency_key = ""
        self.state.failure_class = ""
        self.state.attempts = []

    async def get(self, context: ReaderContext) -> PaymentIntent.GetResponse:
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
        pass

    async def record_status(
        self,
        context: WriterContext,
        request: PaymentIntent.RecordStatusRequest,
    ) -> None:
        pass

    async def record_success(
        self,
        context: WriterContext,
        request: PaymentIntent.RecordSuccessRequest,
    ) -> None:
        pass

    @classmethod
    async def run(cls, context: WorkflowContext) -> None:
        pass


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
        pass


APPLICATION_SERVICERS = [
    UserServicer,
    MarketCatalogServicer,
    MarketServicer,
    BetServicer,
    PaymentIntentServicer,
    AuditEventServicer,
]
