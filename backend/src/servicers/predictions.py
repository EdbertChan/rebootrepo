from __future__ import annotations

from typing import Any
from uuid import uuid4

from predictions.v1.predictions import (
    AuditEventSummary,
    MarketSummary,
    PredictionError,
)
from predictions.v1.predictions_rbt import (
    AuditEvent,
    Bet,
    Market,
    MarketCatalog,
    PaymentIntent,
    User,
)
from reboot.aio.contexts import (
    ReaderContext,
    TransactionContext,
    WorkflowContext,
    WriterContext,
)
from reboot.std.collections.ordered_map.v1.ordered_map import OrderedMap


# Demo credits granted when a `User` is first constructed. Not a debit: no
# bet, resolution, or payout logic in this slice ever changes this balance.
INITIAL_CREDITS = 1000
CATALOG_ID = "global"
CATALOG_MARKET_INDEX_ID = "catalog:markets"
PAGE_LIMIT_DEFAULT = 50
PAGE_LIMIT_MAX = 100
MARKET_OPEN = "open"
MARKET_CLOSED = "closed"
MARKET_RESOLVED = "resolved"
BET_PLACED = "placed"


def _prediction_error(code: str, message: str) -> PredictionError:
    return PredictionError(code=code, message=message)


def _not_implemented(message: str) -> PredictionError:
    return _prediction_error("not_implemented", message)


def _normalized_question(question: str) -> str:
    return " ".join(question.strip().split())


def _clamped_limit(limit: int) -> int:
    if limit <= 0:
        return PAGE_LIMIT_DEFAULT
    return max(1, min(limit, PAGE_LIMIT_MAX))


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


def _map_key(prefix: str) -> str:
    return f"{prefix}:{uuid4()}"


def _decode_entry_id(entry: Any) -> str:
    return bytes(entry.bytes).decode()


async def _read_ids(
    context: ReaderContext | TransactionContext,
    map_id: str,
    *,
    cursor: str = "",
    limit: int = PAGE_LIMIT_DEFAULT,
    reverse: bool = False,
) -> tuple[list[str], str]:
    if map_id == "":
        return [], ""

    actual_limit = _clamped_limit(limit)
    ordered_map = OrderedMap.ref(map_id)
    if cursor:
        page = (
            await ordered_map.reverse_range(
                context,
                start_key=cursor,
                limit=actual_limit + 2,
            ) if reverse else await ordered_map.range(
                context,
                start_key=cursor,
                limit=actual_limit + 2,
            )
        )
    else:
        page = (
            await ordered_map.reverse_range(context, limit=actual_limit + 1)
            if reverse else await ordered_map.range(context, limit=actual_limit + 1)
        )

    entries = list(page.entries)
    if cursor and entries and entries[0].key == cursor:
        entries = entries[1:]

    next_cursor = ""
    if len(entries) > actual_limit:
        next_cursor = entries[actual_limit - 1].key
        entries = entries[:actual_limit]

    return [_decode_entry_id(entry) for entry in entries], next_cursor


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


async def _append_audit(
    context: TransactionContext,
    *,
    market_id: str,
    audit_index_id: str,
    actor_user_id: str,
    event_type: str,
    message: str,
) -> str:
    event_id = str(uuid4())
    await AuditEvent.create(
        context,
        event_id,
        market_id=market_id,
        actor_user_id=actor_user_id,
        event_type=event_type,
        message=message,
        bet_id="",
        payment_intent_id="",
        sequence=0,
    )
    await _insert_id(context, audit_index_id, _map_key("audit"), event_id)
    return event_id


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


class UserServicer(User.Servicer):

    async def create(self, context: TransactionContext) -> None:
        if not context.constructor:
            return
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

        market_ids, next_cursor = await _read_ids(
            context,
            CATALOG_MARKET_INDEX_ID,
            cursor=request.cursor,
            limit=request.limit,
            reverse=True,
        )
        markets: list[MarketSummary] = []
        for market_id in market_ids:
            market = await Market.ref(market_id).get(context)
            markets.append(_market_summary(market))

        # No bet or payment behavior exists yet in this slice, so those
        # lists are always empty.
        return User.DashboardResponse(
            user_id=user_id,
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
        user_id = self.ref().state_id
        _require_signed_in_user(context, user_id, User.CreateMarketAborted)

        question = _normalized_question(request.question)
        if question == "":
            raise User.CreateMarketAborted(
                _prediction_error("invalid_question", "Market question is required.")
            )

        market_id = str(uuid4())
        await Market.create(
            context,
            market_id,
            creator_user_id=user_id,
            question=question,
            close_after_seconds=max(0, request.close_after_seconds),
        )
        await MarketCatalog.ref(CATALOG_ID).ensure(context)
        await MarketCatalog.ref(CATALOG_ID).add_market(context, market_id=market_id)
        await _insert_id(
            context,
            self.state.created_market_index_id,
            _map_key("created-market"),
            market_id,
        )

        market = await Market.ref(market_id).get(context)
        await _append_audit(
            context,
            market_id=market_id,
            audit_index_id=market.audit_index_id,
            actor_user_id=user_id,
            event_type="market_created",
            message=f"Market created: {question}",
        )

        return User.CreateMarketResponse(market_id=market_id)

    async def place_bet(
        self,
        context: TransactionContext,
        request: User.PlaceBetRequest,
    ) -> User.PlaceBetResponse:
        # Betting moves credits between actors, which this contract-only
        # slice must not do. A later slice implements real bet placement.
        user_id = self.ref().state_id
        _require_signed_in_user(context, user_id, User.PlaceBetAborted)
        raise User.PlaceBetAborted(
            _not_implemented("Bet placement ships in a later slice.")
        )

    async def resolve_market(
        self,
        context: TransactionContext,
        request: User.ResolveMarketRequest,
    ) -> User.ResolveMarketResponse:
        # Resolution decides winners and spawns payouts, which this
        # contract-only slice must not do. A later slice implements it.
        user_id = self.ref().state_id
        _require_signed_in_user(context, user_id, User.ResolveMarketAborted)
        raise User.ResolveMarketAborted(
            _not_implemented("Market resolution ships in a later slice.")
        )

    async def close_market(
        self,
        context: TransactionContext,
        request: User.CloseMarketRequest,
    ) -> User.CloseMarketResponse:
        user_id = self.ref().state_id
        _require_signed_in_user(context, user_id, User.CloseMarketAborted)

        market = await Market.ref(request.market_id).get(context)
        if market.creator_user_id != user_id:
            raise User.CloseMarketAborted(
                _prediction_error("permission_denied", "Only the market creator can close it.")
            )
        if market.status == MARKET_OPEN:
            await Market.ref(request.market_id).close_if_due(context)
            await _append_audit(
                context,
                market_id=request.market_id,
                audit_index_id=market.audit_index_id,
                actor_user_id=user_id,
                event_type="market_closed",
                message="Market closed by creator.",
            )

        return User.CloseMarketResponse(status=MARKET_CLOSED)

    async def audit_log(
        self,
        context: ReaderContext,
        request: User.AuditLogRequest,
    ) -> User.AuditLogResponse:
        user_id = self.ref().state_id
        _require_signed_in_user(context, user_id, User.AuditLogAborted)
        if request.market_id == "":
            raise User.AuditLogAborted(
                _prediction_error("missing_market_id", "Market ID is required.")
            )
        market = await Market.ref(request.market_id).get(context)
        audit_ids, next_cursor = await _read_ids(
            context,
            market.audit_index_id,
            cursor=request.cursor,
            limit=request.limit,
        )
        events: list[AuditEventSummary] = [
            await AuditEvent.ref(audit_id).get(context) for audit_id in audit_ids
        ]
        return User.AuditLogResponse(events=events, next_cursor=next_cursor)

    async def apply_payout(
        self,
        context: WriterContext,
        request: User.ApplyPayoutRequest,
    ) -> None:
        # No payout behavior in this slice: never mutates the balance.
        pass

    async def add_payment(
        self,
        context: TransactionContext,
        request: User.AddPaymentRequest,
    ) -> None:
        if request.payment_intent_id == "":
            return
        await _insert_id(
            context,
            self.state.payment_index_id,
            _map_key("payment"),
            request.payment_intent_id,
        )


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
        await _insert_id(
            context,
            self.state.market_index_id,
            _map_key("market"),
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
        self.state.status = MARKET_OPEN
        self.state.close_after_seconds = max(0, request.close_after_seconds)
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
        # Unused until a later slice implements real bet placement.
        pass

    async def close_if_due(self, context: WriterContext) -> None:
        if self.state.status == MARKET_OPEN:
            self.state.status = MARKET_CLOSED

    async def resolve(
        self,
        context: WriterContext,
        request: Market.ResolveRequest,
    ) -> None:
        # Resolution behavior ships in a later slice.
        raise Market.ResolveAborted(
            _not_implemented("Market resolution ships in a later slice.")
        )

    async def record_payout_pending(
        self,
        context: WriterContext,
        request: Market.RecordPayoutPendingRequest,
    ) -> None:
        # No payout workflow in this slice.
        pass

    async def record_payout_succeeded(
        self,
        context: WriterContext,
        request: Market.RecordPayoutSucceededRequest,
    ) -> None:
        # No payout workflow in this slice.
        pass

    async def record_payout_failed(
        self,
        context: WriterContext,
        request: Market.RecordPayoutFailedRequest,
    ) -> None:
        # No payout workflow in this slice.
        pass

    async def mark_review_required(
        self,
        context: WriterContext,
        request: Market.MarkReviewRequiredRequest,
    ) -> None:
        # No payout workflow in this slice.
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
        self.state.status = BET_PLACED
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
        # Unused until a later slice implements resolution and payouts.
        pass

    async def set_status(
        self,
        context: WriterContext,
        request: Bet.SetStatusRequest,
    ) -> None:
        self.state.status = request.status


class PaymentIntentServicer(PaymentIntent.Servicer):

    async def create(
        self,
        context: WriterContext,
        request: PaymentIntent.CreateRequest,
    ) -> None:
        if not context.constructor:
            return
        payment_intent_id = self.ref().state_id
        self.state.user_id = request.user_id
        self.state.market_id = request.market_id
        self.state.bet_id = request.bet_id
        self.state.amount = request.amount
        self.state.status = "pending"
        self.state.idempotency_key = f"payout:{payment_intent_id}"
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
        # Unused until a later slice implements payment execution.
        pass

    async def record_status(
        self,
        context: WriterContext,
        request: PaymentIntent.RecordStatusRequest,
    ) -> None:
        # Unused until a later slice implements payment execution.
        pass

    async def record_success(
        self,
        context: WriterContext,
        request: PaymentIntent.RecordSuccessRequest,
    ) -> None:
        # Unused until a later slice implements payment execution.
        pass

    @classmethod
    async def run(cls, context: WorkflowContext) -> None:
        # The payout workflow ships in a later slice; this contract-only
        # slice never schedules `run`, so this is unreached but present
        # so the method signature is stable for downstream branches.
        return None


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


APPLICATION_SERVICERS = [
    UserServicer,
    MarketCatalogServicer,
    MarketServicer,
    BetServicer,
    PaymentIntentServicer,
    AuditEventServicer,
]
