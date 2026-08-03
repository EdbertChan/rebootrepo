from __future__ import annotations

from dataclasses import dataclass
from datetime import timedelta
import uuid

from predictions.v1.predictions import AuditEventSummary, MarketSummary, PaymentAttempt, PredictionError
from predictions.v1.predictions_rbt import (
    AuditEvent,
    Bet,
    Market,
    MarketCatalog,
    PaymentIntent,
    User,
)
from reboot.aio.aborted import Aborted
from reboot.aio.contexts import ReaderContext, TransactionContext, WorkflowContext, WriterContext
from reboot.std.collections.ordered_map.v1.ordered_map import OrderedMap


INITIAL_CREDITS = 0
CATALOG_ID = "catalog"
CATALOG_MARKET_INDEX_ID = "catalog:markets"
DEFAULT_PAGE_LIMIT = 50
OPEN_STATUS = "open"
CLOSED_STATUS = "closed"


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


def _audit_event_id(market_id: str, sequence: int) -> str:
    return f"{market_id}:audit:{sequence:020d}"


def _audit_key(sequence: int, audit_event_id: str) -> str:
    return f"{sequence:020d}:{audit_event_id}"


def _page_limit(limit: int) -> int:
    if limit <= 0:
        return DEFAULT_PAGE_LIMIT
    return min(limit, DEFAULT_PAGE_LIMIT)


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


async def _range_ids(
    context: ReaderContext,
    map_id: str,
    *,
    cursor: str,
    limit: int,
) -> tuple[list[str], str]:
    page_limit = _page_limit(limit)
    try:
        page = await OrderedMap.ref(map_id).range(
            context,
            start_key=cursor,
            limit=page_limit + 1,
        )
    except Aborted:
        return [], ""

    entries = list(page.entries)
    next_cursor = ""
    if len(entries) > page_limit:
        next_cursor = entries[page_limit - 1].key + "\0"
        entries = entries[:page_limit]

    return [entry.bytes.decode() for entry in entries], next_cursor


def _market_summary(market: Market.GetResponse) -> MarketSummary:
    return MarketSummary(
        market_id=market.market_id,
        creator_user_id=market.creator_user_id,
        question=market.question,
        status=market.status,
        close_after_seconds=market.close_after_seconds,
        winning_outcome=market.winning_outcome,
        yes_total=market.yes_total,
        no_total=market.no_total,
        bet_count=market.bet_count,
        payout_pending_count=market.payout_pending_count,
        payout_succeeded_count=market.payout_succeeded_count,
        payout_failed_count=market.payout_failed_count,
    )


def _audit_summary(event: AuditEvent.GetResponse) -> AuditEventSummary:
    return AuditEventSummary(
        audit_event_id=event.audit_event_id,
        market_id=event.market_id,
        actor_user_id=event.actor_user_id,
        event_type=event.event_type,
        message=event.message,
        bet_id=event.bet_id,
        payment_intent_id=event.payment_intent_id,
        sequence=event.sequence,
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
        market_ids, next_cursor = await _range_ids(
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
                PredictionError(
                    code="invalid_question",
                    message="Market question is required.",
                )
            )
        if request.close_after_seconds < 0:
            raise User.CreateMarketAborted(
                PredictionError(
                    code="invalid_close_after_seconds",
                    message="Market close delay cannot be negative.",
                )
            )

        user_id = self.ref().state_id
        if self.state.created_market_index_id == "":
            self.state.created_market_index_id = _user_created_market_index_id(user_id)

        market_id = str(uuid.uuid4())
        market_ref, _ = await Market.create(
            context,
            market_id,
            creator_user_id=user_id,
            question=question,
            close_after_seconds=request.close_after_seconds,
        )
        await _insert_id(
            context,
            self.state.created_market_index_id,
            market_id,
            market_id,
        )
        await MarketCatalog.ref(CATALOG_ID).ensure(context)
        await MarketCatalog.ref(CATALOG_ID).add_market(context, market_id=market_id)

        created_audit_event_id = _audit_event_id(market_id, 1)
        closed_audit_event_id = _audit_event_id(market_id, 2)
        await AuditEvent.create(
            context,
            created_audit_event_id,
            market_id=market_id,
            actor_user_id=user_id,
            event_type="market_created",
            message="Market created.",
            sequence=1,
        )
        await AuditEvent.create(
            context,
            closed_audit_event_id,
            market_id=market_id,
            actor_user_id=user_id,
            event_type="market_close_scheduled",
            message="Market close is scheduled.",
            sequence=2,
        )
        audit_index_id = _market_audit_index_id(market_id)
        await _insert_id(
            context,
            audit_index_id,
            _audit_key(1, created_audit_event_id),
            created_audit_event_id,
        )
        await _insert_id(
            context,
            audit_index_id,
            _audit_key(2, closed_audit_event_id),
            closed_audit_event_id,
        )

        if request.close_after_seconds > 0:
            await market_ref.schedule(
                when=timedelta(seconds=request.close_after_seconds)
            ).close_if_due(context)

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
        market = await Market.ref(request.market_id).get(context)
        if market.creator_user_id != self.ref().state_id:
            raise User.CloseMarketAborted(
                PredictionError(
                    code="not_market_creator",
                    message="Only the market creator can close the market.",
                )
            )
        await Market.ref(request.market_id).close_if_due(context)
        closed = await Market.ref(request.market_id).get(context)
        return User.CloseMarketResponse(status=closed.status)

    async def audit_log(
        self,
        context: ReaderContext,
        request: User.AuditLogRequest,
    ) -> User.AuditLogResponse:
        event_ids, next_cursor = await _range_ids(
            context,
            _market_audit_index_id(request.market_id),
            cursor=request.cursor,
            limit=request.limit,
        )
        events = [
            _audit_summary(await AuditEvent.ref(event_id).get(context))
            for event_id in event_ids
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
        if self.state.market_index_id == "":
            self.state.market_index_id = CATALOG_MARKET_INDEX_ID
            await OrderedMap.ref(self.state.market_index_id).create(
                context,
                maintain_size=True,
            )
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
        self.state.status = OPEN_STATUS
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
        if self.state.status != OPEN_STATUS:
            return
        self.state.status = CLOSED_STATUS
        await AuditEvent.ref(_audit_event_id(self.ref().state_id, 2)).update(
            context,
            event_type="market_closed",
            message="Market closed.",
            bet_id="",
            payment_intent_id="",
            sequence=2,
        )

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
