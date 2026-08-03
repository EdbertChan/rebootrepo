from __future__ import annotations

from dataclasses import dataclass
from datetime import timedelta
from uuid import uuid4

from predictions.v1.predictions import AuditEventSummary, MarketSummary, PaymentAttempt, PredictionError
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
CATALOG_STATE_ID = "catalog"
DEFAULT_PAGE_SIZE = 50
MAX_PAGE_SIZE = 100
MARKET_STATUS_OPEN = "open"
MARKET_STATUS_CLOSED = "closed"


@dataclass(frozen=True)
class GatewayOutcome:
    status: str
    failure_class: str = ""
    provider_transaction_id: str = ""
    message: str = ""


def configure_gateway_for_tests(charge: object) -> None:
    del charge


def reset_gateway_for_tests() -> None:
    return None


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


def _page_size(limit: int) -> int:
    if limit <= 0:
        return DEFAULT_PAGE_SIZE
    return min(limit, MAX_PAGE_SIZE)


async def _ordered_ids(
    context: ReaderContext | TransactionContext,
    map_id: str,
    *,
    cursor: str = "",
    limit: int = DEFAULT_PAGE_SIZE,
) -> tuple[list[str], str]:
    response = await OrderedMap.ref(map_id).range(
        context,
        start_key=cursor,
        limit=_page_size(limit),
    )
    ids = [entry.bytes.decode() for entry in response.entries]
    next_cursor = ""
    if len(response.entries) == _page_size(limit):
        next_cursor = f"{response.entries[-1].key}\0"
    return ids, next_cursor


def _market_summary(record: Market.GetResponse) -> MarketSummary:
    return MarketSummary(
        market_id=record.market_id,
        creator_user_id=record.creator_user_id,
        question=record.question,
        status=record.status,
        close_after_seconds=record.close_after_seconds,
        winning_outcome=record.winning_outcome,
        yes_total=record.yes_total,
        no_total=record.no_total,
        bet_count=record.bet_count,
        payout_pending_count=record.payout_pending_count,
        payout_succeeded_count=record.payout_succeeded_count,
        payout_failed_count=record.payout_failed_count,
    )


async def _audit_event_count(
    context: ReaderContext | TransactionContext,
    audit_index_id: str,
) -> int:
    ids, _ = await _ordered_ids(
        context,
        audit_index_id,
        limit=MAX_PAGE_SIZE,
    )
    return len(ids)


async def _append_audit_event(
    context: TransactionContext,
    *,
    market_id: str,
    actor_user_id: str,
    event_type: str,
    message: str,
) -> None:
    audit_index_id = _market_audit_index_id(market_id)
    sequence = await _audit_event_count(context, audit_index_id) + 1
    audit_event_id = f"audit:{market_id}:{sequence:020d}"
    await AuditEvent.create(
        context,
        audit_event_id,
        market_id=market_id,
        actor_user_id=actor_user_id,
        event_type=event_type,
        message=message,
        bet_id="",
        payment_intent_id="",
        sequence=sequence,
    )
    await _insert_id(
        context,
        audit_index_id,
        f"{sequence:020d}",
        audit_event_id,
    )


def _prediction_error(code: str, message: str) -> PredictionError:
    return PredictionError(code=code, message=message)


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
        market_ids, next_cursor = await _ordered_ids(
            context,
            CATALOG_MARKET_INDEX_ID,
            cursor=request.cursor,
            limit=request.limit,
        )
        markets = [
            _market_summary(await Market.ref(market_id).get(context))
            for market_id in market_ids
        ]
        return User.DashboardResponse(
            user_id=self.ref().state_id,
            balance=self.state.balance,
            markets=markets,
            bets=[],
            payments=[],
            next_cursor=next_cursor,
        )

    async def create_market(
        self,
        context: TransactionContext,
        request: User.CreateMarketRequest,
    ) -> User.CreateMarketResponse:
        question = request.question.strip()
        if question == "":
            raise User.CreateMarketAborted(
                _prediction_error("invalid_market", "Question is required."),
                message="Question is required.",
            )
        if request.close_after_seconds < 0:
            raise User.CreateMarketAborted(
                _prediction_error(
                    "invalid_market",
                    "close_after_seconds must be non-negative.",
                ),
                message="close_after_seconds must be non-negative.",
            )

        user_id = self.ref().state_id
        market_id = f"market:{uuid4().hex}"
        await Market.create(
            context,
            market_id,
            creator_user_id=user_id,
            question=question,
            close_after_seconds=request.close_after_seconds,
        )
        if self.state.created_market_index_id == "":
            self.state.created_market_index_id = _user_created_market_index_id(user_id)
        await _insert_id(
            context,
            self.state.created_market_index_id,
            market_id,
            market_id,
        )
        await MarketCatalog.ref(CATALOG_STATE_ID).add_market(
            context,
            market_id=market_id,
        )
        await _append_audit_event(
            context,
            market_id=market_id,
            actor_user_id=user_id,
            event_type="market_created",
            message="Market created.",
        )
        if request.close_after_seconds > 0:
            await User.ref(user_id).schedule(
                when=timedelta(seconds=request.close_after_seconds),
            ).close_market(
                context,
                market_id=market_id,
            )
        return User.CreateMarketResponse(market_id=market_id)

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
        if request.market_id == "":
            raise User.CloseMarketAborted(
                _prediction_error("invalid_market", "market_id is required."),
                message="market_id is required.",
            )
        user_id = self.ref().state_id
        market = await Market.ref(request.market_id).get(context)
        if market.creator_user_id != user_id:
            raise User.CloseMarketAborted(
                _prediction_error("forbidden", "Only the creator can close this market."),
                message="Only the creator can close this market.",
            )
        if market.status == MARKET_STATUS_OPEN:
            await Market.ref(request.market_id).close_if_due(context)
            await _append_audit_event(
                context,
                market_id=request.market_id,
                actor_user_id=user_id,
                event_type="market_closed",
                message="Market closed.",
            )
            return User.CloseMarketResponse(status=MARKET_STATUS_CLOSED)
        return User.CloseMarketResponse(status=market.status)

    async def audit_log(
        self,
        context: ReaderContext,
        request: User.AuditLogRequest,
    ) -> User.AuditLogResponse:
        event_ids, next_cursor = await _ordered_ids(
            context,
            _market_audit_index_id(request.market_id),
            cursor=request.cursor,
            limit=request.limit,
        )
        events = [
            AuditEventSummary(
                audit_event_id=event.audit_event_id,
                market_id=event.market_id,
                actor_user_id=event.actor_user_id,
                event_type=event.event_type,
                message=event.message,
                bet_id=event.bet_id,
                payment_intent_id=event.payment_intent_id,
                sequence=event.sequence,
            )
            for event_id in event_ids
            for event in [await AuditEvent.ref(event_id).get(context)]
        ]
        return User.AuditLogResponse(events=events, next_cursor=next_cursor)

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
        self.state.status = MARKET_STATUS_OPEN
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
        if self.state.status == MARKET_STATUS_OPEN:
            self.state.status = MARKET_STATUS_CLOSED

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
        self.state.status = ""
        self.state.idempotency_key = ""
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
        self.state.attempts.append(
            PaymentAttempt(
                attempt_number=request.attempt_number,
                status=request.status,
                failure_class=request.failure_class,
                provider_transaction_id=request.provider_transaction_id,
                message=request.message,
            )
        )

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
        self.state.status = ""
        self.state.failure_class = ""

    @classmethod
    async def run(cls, context: WorkflowContext) -> None:
        del context


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
