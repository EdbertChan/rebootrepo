from __future__ import annotations

from dataclasses import dataclass
from datetime import timedelta
from typing import Any, Awaitable, Callable
from uuid import uuid4

from predictions.v1.predictions import (
    AuditEventSummary,
    BetSummary,
    MarketSummary,
    PaymentAttempt,
    PaymentIntentSummary,
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
from reboot.aio.contexts import ReaderContext, TransactionContext, WorkflowContext, WriterContext
from reboot.aio.workflows import at_least_once
from reboot.std.collections.ordered_map.v1.ordered_map import OrderedMap


INITIAL_CREDITS = 1000
CATALOG_ID = "global"
CATALOG_MARKET_INDEX_ID = "catalog:markets"
PAGE_LIMIT_DEFAULT = 50
PAGE_LIMIT_MAX = 100
VALID_OUTCOMES = {"YES", "NO"}
MARKET_OPEN = "open"
MARKET_CLOSED = "closed"
MARKET_RESOLVED = "resolved"
MARKET_REVIEW_REQUIRED = "review_required"
BET_PLACED = "placed"
BET_WON_PENDING = "won_pending"
BET_WON_PAID = "won_paid"
BET_LOST = "lost"
BET_PAYMENT_FAILED = "payment_failed"
PAYMENT_PENDING = "pending"
PAYMENT_RUNNING = "running"
PAYMENT_SUCCEEDED = "succeeded"
PAYMENT_RETRY_EXHAUSTED = "retry_exhausted"
PAYMENT_FAILED = "failed"


@dataclass(frozen=True)
class GatewayOutcome:
    status: str
    failure_class: str = ""
    provider_transaction_id: str = ""
    message: str = ""


GatewayCharge = Callable[[str, int, str, int], Awaitable[GatewayOutcome]]


async def _default_gateway_charge(
    payment_intent_id: str,
    amount: int,
    idempotency_key: str,
    attempt_number: int,
) -> GatewayOutcome:
    return GatewayOutcome(
        status="success",
        provider_transaction_id=(
            f"simulated:{payment_intent_id}:{idempotency_key}:attempt-{attempt_number}"
        ),
        message="Simulated gateway accepted payout.",
    )


gateway_charge: GatewayCharge = _default_gateway_charge


def configure_gateway_for_tests(charge: GatewayCharge) -> None:
    global gateway_charge
    gateway_charge = charge


def reset_gateway_for_tests() -> None:
    global gateway_charge
    gateway_charge = _default_gateway_charge


def _prediction_error(code: str, message: str) -> PredictionError:
    return PredictionError(code=code, message=message)


def _normalize_outcome(outcome: str) -> str:
    return outcome.strip().upper()


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
    try:
        ordered_map = OrderedMap.ref(map_id)
        if cursor:
            page = (
                await ordered_map.reverse_range(
                    context,
                    start_key=cursor,
                    limit=actual_limit + 2,
                )
                if reverse
                else await ordered_map.range(
                    context,
                    start_key=cursor,
                    limit=actual_limit + 2,
                )
            )
        else:
            page = (
                await ordered_map.reverse_range(context, limit=actual_limit + 1)
                if reverse
                else await ordered_map.range(context, limit=actual_limit + 1)
            )
    except Exception:
        return [], ""

    entries = list(page.entries)
    if cursor and entries and entries[0].key == cursor:
        entries = entries[1:]

    next_cursor = ""
    if len(entries) > actual_limit:
        next_cursor = entries[actual_limit - 1].key
        entries = entries[:actual_limit]

    return [_decode_entry_id(entry) for entry in entries], next_cursor


async def _insert_id(
    context: TransactionContext | WorkflowContext,
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
    context: TransactionContext | WorkflowContext,
    *,
    market_id: str,
    audit_index_id: str,
    actor_user_id: str,
    event_type: str,
    message: str,
    bet_id: str = "",
    payment_intent_id: str = "",
    workflow_alias_prefix: str = "",
) -> str:
    event_id = str(uuid4()) if workflow_alias_prefix == "" else (
        f"{payment_intent_id}:{event_type}:{workflow_alias_prefix}"
    )
    key = _map_key("audit") if workflow_alias_prefix == "" else event_id

    if workflow_alias_prefix == "":
        await AuditEvent.create(
            context,
            event_id,
            market_id=market_id,
            actor_user_id=actor_user_id,
            event_type=event_type,
            message=message,
            bet_id=bet_id,
            payment_intent_id=payment_intent_id,
            sequence=0,
        )
        await _insert_id(context, audit_index_id, key, event_id)
        return event_id

    await AuditEvent.per_workflow(
        f"Create audit event {workflow_alias_prefix}"
    ).create(
        context,
        event_id,
        market_id=market_id,
        actor_user_id=actor_user_id,
        event_type=event_type,
        message=message,
        bet_id=bet_id,
        payment_intent_id=payment_intent_id,
        sequence=0,
    )
    await OrderedMap.ref(audit_index_id).per_workflow(
        f"Index audit event {workflow_alias_prefix}"
    ).insert(
        context,
        key=key,
        bytes=event_id.encode(),
    )
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


def _bet_summary(record: Bet.GetResponse, market_question: str = "") -> BetSummary:
    return BetSummary(
        bet_id=record.bet_id,
        market_id=record.market_id,
        market_question=market_question,
        user_id=record.user_id,
        outcome=record.outcome,
        stake=record.stake,
        payout_amount=record.payout_amount,
        status=record.status,
        payment_intent_id=record.payment_intent_id,
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

        catalog_market_ids, next_cursor = await _read_ids(
            context,
            CATALOG_MARKET_INDEX_ID,
            cursor=request.cursor,
            limit=request.limit,
            reverse=True,
        )
        markets: list[MarketSummary] = []
        for market_id in catalog_market_ids:
            market = await Market.ref(market_id).get(context)
            markets.append(_market_summary(market))

        bet_ids, _ = await _read_ids(
            context,
            self.state.bet_index_id,
            limit=PAGE_LIMIT_DEFAULT,
            reverse=True,
        )
        bets: list[BetSummary] = []
        for bet_id in bet_ids:
            bet = await Bet.ref(bet_id).get(context)
            market_question = ""
            try:
                market_question = (await Market.ref(bet.market_id).get(context)).question
            except Exception:
                market_question = ""
            bets.append(_bet_summary(bet, market_question=market_question))

        payment_ids, _ = await _read_ids(
            context,
            self.state.payment_index_id,
            limit=PAGE_LIMIT_DEFAULT,
            reverse=True,
        )
        payments = [
            await PaymentIntent.ref(payment_id).get(context)
            for payment_id in payment_ids
        ]

        return User.DashboardResponse(
            user_id=user_id,
            balance=self.state.balance,
            markets=markets,
            bets=bets,
            payments=payments,
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

        close_after_seconds = max(0, request.close_after_seconds)
        market_id = str(uuid4())
        await Market.create(
            context,
            market_id,
            creator_user_id=user_id,
            question=question,
            close_after_seconds=close_after_seconds,
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
        if close_after_seconds > 0:
            await Market.ref(market_id).schedule(
                when=timedelta(seconds=close_after_seconds),
            ).close_if_due(context)

        return User.CreateMarketResponse(market_id=market_id)

    async def place_bet(
        self,
        context: TransactionContext,
        request: User.PlaceBetRequest,
    ) -> User.PlaceBetResponse:
        user_id = self.ref().state_id
        _require_signed_in_user(context, user_id, User.PlaceBetAborted)

        outcome = _normalize_outcome(request.outcome)
        if outcome not in VALID_OUTCOMES:
            raise User.PlaceBetAborted(
                _prediction_error("invalid_outcome", "Outcome must be YES or NO.")
            )
        if request.stake <= 0:
            raise User.PlaceBetAborted(
                _prediction_error("invalid_stake", "Stake must be a positive integer.")
            )
        if self.state.balance < request.stake:
            raise User.PlaceBetAborted(
                _prediction_error("insufficient_credits", "Not enough credits.")
            )

        market = await Market.ref(request.market_id).get(context)
        if market.status != MARKET_OPEN:
            raise User.PlaceBetAborted(
                _prediction_error("market_not_open", "Market is not open for bets.")
            )

        bet_id = str(uuid4())
        self.state.balance -= request.stake
        await Bet.create(
            context,
            bet_id,
            user_id=user_id,
            market_id=request.market_id,
            outcome=outcome,
            stake=request.stake,
        )
        await Market.ref(request.market_id).add_bet(
            context,
            bet_id=bet_id,
            outcome=outcome,
            stake=request.stake,
        )
        await _insert_id(context, self.state.bet_index_id, _map_key("bet"), bet_id)
        await _insert_id(context, market.bet_index_id, _map_key("bet"), bet_id)
        await _append_audit(
            context,
            market_id=request.market_id,
            audit_index_id=market.audit_index_id,
            actor_user_id=user_id,
            event_type="bet_placed",
            message=f"{user_id} placed {request.stake} credits on {outcome}.",
            bet_id=bet_id,
        )

        return User.PlaceBetResponse(bet_id=bet_id, balance=self.state.balance)

    async def resolve_market(
        self,
        context: TransactionContext,
        request: User.ResolveMarketRequest,
    ) -> User.ResolveMarketResponse:
        user_id = self.ref().state_id
        _require_signed_in_user(context, user_id, User.ResolveMarketAborted)

        winning_outcome = _normalize_outcome(request.winning_outcome)
        if winning_outcome not in VALID_OUTCOMES:
            raise User.ResolveMarketAborted(
                _prediction_error("invalid_outcome", "Winning outcome must be YES or NO.")
            )

        market = await Market.ref(request.market_id).get(context)
        if market.creator_user_id != user_id:
            raise User.ResolveMarketAborted(
                _prediction_error("permission_denied", "Only the market creator can resolve it.")
            )
        if market.status == MARKET_OPEN:
            raise User.ResolveMarketAborted(
                _prediction_error("market_open", "Market must be closed before resolution.")
            )
        if market.status != MARKET_CLOSED:
            raise User.ResolveMarketAborted(
                _prediction_error("already_resolved", "Market has already been resolved.")
            )

        await Market.ref(request.market_id).resolve(
            context,
            winning_outcome=winning_outcome,
        )

        bet_ids, _ = await _read_ids(
            context,
            market.bet_index_id,
            limit=PAGE_LIMIT_MAX,
        )
        winner_count = 0
        loser_count = 0
        payout_count = 0
        for bet_id in bet_ids:
            bet = await Bet.ref(bet_id).get(context)
            if bet.outcome != winning_outcome:
                loser_count += 1
                await Bet.ref(bet_id).set_status(context, status=BET_LOST)
                continue

            winner_count += 1
            payout_count += 1
            payment_intent_id = str(uuid4())
            payout_amount = bet.stake * 2
            await PaymentIntent.create(
                context,
                payment_intent_id,
                user_id=bet.user_id,
                market_id=request.market_id,
                bet_id=bet_id,
                amount=payout_amount,
            )
            await Bet.ref(bet_id).mark_won(
                context,
                payout_amount=payout_amount,
                payment_intent_id=payment_intent_id,
            )
            await User.ref(bet.user_id).add_payment(
                context,
                payment_intent_id=payment_intent_id,
            )
            await Market.ref(request.market_id).record_payout_pending(
                context,
                payment_intent_id=payment_intent_id,
            )
            await PaymentIntent.ref(payment_intent_id).schedule().run(context)

        resolved_market = await Market.ref(request.market_id).get(context)
        await _append_audit(
            context,
            market_id=request.market_id,
            audit_index_id=resolved_market.audit_index_id,
            actor_user_id=user_id,
            event_type="market_resolved",
            message=f"Market resolved as {winning_outcome}.",
        )

        return User.ResolveMarketResponse(
            winner_count=winner_count,
            loser_count=loser_count,
            payout_count=payout_count,
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
        if market.status in {MARKET_RESOLVED, MARKET_REVIEW_REQUIRED}:
            raise User.CloseMarketAborted(
                _prediction_error("market_final", "Resolved markets cannot be closed again.")
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
        events = [
            await AuditEvent.ref(audit_id).get(context)
            for audit_id in audit_ids
        ]
        return User.AuditLogResponse(events=events, next_cursor=next_cursor)

    async def apply_payout(
        self,
        context: WriterContext,
        request: User.ApplyPayoutRequest,
    ) -> None:
        if request.payment_intent_id in self.state.settled_payment_intent_ids:
            return
        self.state.balance += request.amount
        self.state.settled_payment_intent_ids.append(request.payment_intent_id)

    async def add_payment(
        self,
        context: TransactionContext,
        request: User.AddPaymentRequest,
    ) -> None:
        if request.payment_intent_id == "":
            return
        await OrderedMap.ref(self.state.payment_index_id).insert(
            context,
            key=_map_key("payment"),
            bytes=request.payment_intent_id.encode(),
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
        self.state.question = _normalized_question(request.question)
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
        if request.outcome == "YES":
            self.state.yes_total += request.stake
        elif request.outcome == "NO":
            self.state.no_total += request.stake
        self.state.bet_count += 1

    async def close_if_due(self, context: WriterContext) -> None:
        if self.state.status == MARKET_OPEN:
            self.state.status = MARKET_CLOSED

    async def resolve(
        self,
        context: WriterContext,
        request: Market.ResolveRequest,
    ) -> None:
        if self.state.status != MARKET_CLOSED:
            raise Market.ResolveAborted(
                _prediction_error("market_not_closed", "Market must be closed.")
            )
        self.state.status = MARKET_RESOLVED
        self.state.winning_outcome = request.winning_outcome

    async def record_payout_pending(
        self,
        context: WriterContext,
        request: Market.RecordPayoutPendingRequest,
    ) -> None:
        self.state.payout_pending_count += 1

    async def record_payout_succeeded(
        self,
        context: WriterContext,
        request: Market.RecordPayoutSucceededRequest,
    ) -> None:
        if self.state.payout_pending_count > 0:
            self.state.payout_pending_count -= 1
        self.state.payout_succeeded_count += 1

    async def record_payout_failed(
        self,
        context: WriterContext,
        request: Market.RecordPayoutFailedRequest,
    ) -> None:
        if self.state.payout_pending_count > 0:
            self.state.payout_pending_count -= 1
        self.state.payout_failed_count += 1

    async def mark_review_required(
        self,
        context: WriterContext,
        request: Market.MarkReviewRequiredRequest,
    ) -> None:
        self.state.status = MARKET_REVIEW_REQUIRED


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
        self.state.status = BET_WON_PENDING
        self.state.payout_amount = request.payout_amount
        self.state.payment_intent_id = request.payment_intent_id

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
        self.state.status = PAYMENT_PENDING
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
        self.state.status = PAYMENT_RUNNING
        self.state.failure_class = request.failure_class
        self.state.attempts.append(
            PaymentAttempt(
                attempt_number=request.attempt_number,
                status=request.status,
                failure_class=request.failure_class,
                provider_transaction_id=request.provider_transaction_id,
                message=request.message,
            )
        )
        self.state.attempts = self.state.attempts[-10:]

    async def record_status(
        self,
        context: WriterContext,
        request: PaymentIntent.RecordStatusRequest,
    ) -> None:
        self.state.status = request.status
        self.state.failure_class = request.failure_class

    async def record_success(
        self,
        context: WriterContext,
        request: PaymentIntent.RecordSuccessRequest,
    ) -> None:
        self.state.status = PAYMENT_SUCCEEDED
        self.state.failure_class = ""

    @classmethod
    async def run(cls, context: WorkflowContext) -> None:
        payment_intent_id = context.state_id
        payment = await PaymentIntent.ref(payment_intent_id).per_workflow(
            "Load payment intent"
        ).get(context)
        if payment.status in {PAYMENT_SUCCEEDED, PAYMENT_RETRY_EXHAUSTED, PAYMENT_FAILED}:
            return

        market = await Market.ref(payment.market_id).per_workflow(
            "Load payout market"
        ).get(context)

        for attempt_number in range(1, 4):
            async def call_gateway() -> str:
                outcome = await gateway_charge(
                    payment_intent_id,
                    payment.amount,
                    payment.idempotency_key,
                    attempt_number,
                )
                return "|".join(
                    [
                        outcome.status,
                        outcome.failure_class,
                        outcome.provider_transaction_id,
                        outcome.message,
                    ]
                )

            raw_result = await at_least_once(
                f"Simulated gateway attempt {attempt_number}",
                context,
                call_gateway,
            )
            status, failure_class, provider_transaction_id, message = (
                raw_result.split("|", 3)
            )
            await PaymentIntent.ref().per_workflow(
                f"Record gateway attempt {attempt_number}"
            ).record_attempt(
                context,
                attempt_number=attempt_number,
                status=status,
                failure_class=failure_class,
                provider_transaction_id=provider_transaction_id,
                message=message,
            )

            if status == "success":
                await User.ref(payment.user_id).per_workflow(
                    "Apply winner credit"
                ).apply_payout(
                    context,
                    payment_intent_id=payment_intent_id,
                    amount=payment.amount,
                )
                await Bet.ref(payment.bet_id).per_workflow(
                    "Mark winning bet paid"
                ).set_status(
                    context,
                    status=BET_WON_PAID,
                )
                await Market.ref(payment.market_id).per_workflow(
                    "Record payout success"
                ).record_payout_succeeded(
                    context,
                    payment_intent_id=payment_intent_id,
                )
                await _append_audit(
                    context,
                    market_id=payment.market_id,
                    audit_index_id=market.audit_index_id,
                    actor_user_id=payment.user_id,
                    event_type="payout_succeeded",
                    message=f"Paid {payment.amount} credits.",
                    bet_id=payment.bet_id,
                    payment_intent_id=payment_intent_id,
                    workflow_alias_prefix="success",
                )
                await PaymentIntent.ref().per_workflow(
                    "Mark payment succeeded"
                ).record_success(
                    context,
                    provider_transaction_id=provider_transaction_id,
                )
                return

            if status == "permanent_failure":
                await Bet.ref(payment.bet_id).per_workflow(
                    "Mark winning bet payment failed"
                ).set_status(
                    context,
                    status=BET_PAYMENT_FAILED,
                )
                await Market.ref(payment.market_id).per_workflow(
                    "Record payout failure"
                ).record_payout_failed(
                    context,
                    payment_intent_id=payment_intent_id,
                )
                await Market.ref(payment.market_id).per_workflow(
                    "Mark market review required"
                ).mark_review_required(
                    context,
                    payment_intent_id=payment_intent_id,
                )
                await _append_audit(
                    context,
                    market_id=payment.market_id,
                    audit_index_id=market.audit_index_id,
                    actor_user_id=payment.user_id,
                    event_type="payout_failed",
                    message=message or "Payment failed permanently.",
                    bet_id=payment.bet_id,
                    payment_intent_id=payment_intent_id,
                    workflow_alias_prefix="permanent-failure",
                )
                await PaymentIntent.ref().per_workflow(
                    "Mark payment failed"
                ).record_status(
                    context,
                    status=PAYMENT_FAILED,
                    failure_class=failure_class,
                )
                return

        await Bet.ref(payment.bet_id).per_workflow(
            "Mark winning bet retry exhausted"
        ).set_status(
            context,
            status=BET_PAYMENT_FAILED,
        )
        await Market.ref(payment.market_id).per_workflow(
            "Record retry exhaustion"
        ).record_payout_failed(
            context,
            payment_intent_id=payment_intent_id,
        )
        await Market.ref(payment.market_id).per_workflow(
            "Mark review after exhaustion"
        ).mark_review_required(
            context,
            payment_intent_id=payment_intent_id,
        )
        await _append_audit(
            context,
            market_id=payment.market_id,
            audit_index_id=market.audit_index_id,
            actor_user_id=payment.user_id,
            event_type="payout_retry_exhausted",
            message="Payment retry budget exhausted.",
            bet_id=payment.bet_id,
            payment_intent_id=payment_intent_id,
            workflow_alias_prefix="retry-exhausted",
        )
        await PaymentIntent.ref().per_workflow(
            "Mark payment retry exhausted"
        ).record_status(
            context,
            status=PAYMENT_RETRY_EXHAUSTED,
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
