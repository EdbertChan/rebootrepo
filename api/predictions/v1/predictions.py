from reboot.api import (
    API,
    Field,
    Methods,
    Model,
    Reader,
    Tool,
    Transaction,
    Type,
    UI,
    Workflow,
    Writer,
)


class PredictionError(Model):
    code: str = Field(tag=1, default="")
    message: str = Field(tag=2, default="")


class PaymentAttempt(Model):
    attempt_number: int = Field(tag=1, default=0)
    status: str = Field(tag=2, default="")
    failure_class: str = Field(tag=3, default="")
    provider_transaction_id: str = Field(tag=4, default="")
    message: str = Field(tag=5, default="")


class MarketSummary(Model):
    market_id: str = Field(tag=1, default="")
    creator_user_id: str = Field(tag=2, default="")
    question: str = Field(tag=3, default="")
    status: str = Field(tag=4, default="")
    close_after_seconds: int = Field(tag=5, default=0)
    winning_outcome: str = Field(tag=6, default="")
    yes_total: int = Field(tag=7, default=0)
    no_total: int = Field(tag=8, default=0)
    bet_count: int = Field(tag=9, default=0)
    payout_pending_count: int = Field(tag=10, default=0)
    payout_succeeded_count: int = Field(tag=11, default=0)
    payout_failed_count: int = Field(tag=12, default=0)


class BetSummary(Model):
    bet_id: str = Field(tag=1, default="")
    market_id: str = Field(tag=2, default="")
    market_question: str = Field(tag=3, default="")
    user_id: str = Field(tag=4, default="")
    outcome: str = Field(tag=5, default="")
    stake: int = Field(tag=6, default=0)
    payout_amount: int = Field(tag=7, default=0)
    status: str = Field(tag=8, default="")
    payment_intent_id: str = Field(tag=9, default="")


class PaymentIntentSummary(Model):
    payment_intent_id: str = Field(tag=1, default="")
    bet_id: str = Field(tag=2, default="")
    market_id: str = Field(tag=3, default="")
    user_id: str = Field(tag=4, default="")
    amount: int = Field(tag=5, default=0)
    status: str = Field(tag=6, default="")
    idempotency_key: str = Field(tag=7, default="")
    failure_class: str = Field(tag=8, default="")
    attempts: list[PaymentAttempt] = Field(tag=9, default_factory=list)


class AuditEventSummary(Model):
    audit_event_id: str = Field(tag=1, default="")
    market_id: str = Field(tag=2, default="")
    actor_user_id: str = Field(tag=3, default="")
    event_type: str = Field(tag=4, default="")
    message: str = Field(tag=5, default="")
    bet_id: str = Field(tag=6, default="")
    payment_intent_id: str = Field(tag=7, default="")
    sequence: int = Field(tag=8, default=0)


class DashboardRequest(Model):
    cursor: str = Field(tag=1, default="")
    limit: int = Field(tag=2, default=0)


class DashboardResponse(Model):
    user_id: str = Field(tag=1, default="")
    balance: int = Field(tag=2, default=0)
    markets: list[MarketSummary] = Field(tag=3, default_factory=list)
    bets: list[BetSummary] = Field(tag=4, default_factory=list)
    payments: list[PaymentIntentSummary] = Field(tag=5, default_factory=list)
    next_cursor: str = Field(tag=6, default="")


class CreateMarketRequest(Model):
    question: str = Field(tag=1, default="")
    close_after_seconds: int = Field(tag=2, default=0)


class CreateMarketResponse(Model):
    market_id: str = Field(tag=1, default="")


class PlaceBetRequest(Model):
    market_id: str = Field(tag=1, default="")
    outcome: str = Field(tag=2, default="")
    stake: int = Field(tag=3, default=0)


class PlaceBetResponse(Model):
    bet_id: str = Field(tag=1, default="")
    balance: int = Field(tag=2, default=0)


class ResolveMarketRequest(Model):
    market_id: str = Field(tag=1, default="")
    winning_outcome: str = Field(tag=2, default="")


class CloseMarketRequest(Model):
    market_id: str = Field(tag=1, default="")


class CloseMarketResponse(Model):
    status: str = Field(tag=1, default="")


class ResolveMarketResponse(Model):
    winner_count: int = Field(tag=1, default=0)
    loser_count: int = Field(tag=2, default=0)
    payout_count: int = Field(tag=3, default=0)


class AuditLogRequest(Model):
    market_id: str = Field(tag=1, default="")
    cursor: str = Field(tag=2, default="")
    limit: int = Field(tag=3, default=0)


class AuditLogResponse(Model):
    events: list[AuditEventSummary] = Field(tag=1, default_factory=list)
    next_cursor: str = Field(tag=2, default="")


class UserState(Model):
    balance: int = Field(tag=1, default=0)
    created_market_index_id: str = Field(tag=2, default="")
    bet_index_id: str = Field(tag=3, default="")
    payment_index_id: str = Field(tag=4, default="")
    settled_payment_intent_ids: list[str] = Field(tag=5, default_factory=list)


class ApplyPayoutRequest(Model):
    payment_intent_id: str = Field(tag=1, default="")
    amount: int = Field(tag=2, default=0)


class AddPaymentRequest(Model):
    payment_intent_id: str = Field(tag=1, default="")


class MarketCatalogState(Model):
    market_index_id: str = Field(tag=1, default="")


class MarketCatalogEnsureResponse(Model):
    market_index_id: str = Field(tag=1, default="")


class MarketCatalogAddMarketRequest(Model):
    market_id: str = Field(tag=1, default="")


class MarketState(Model):
    creator_user_id: str = Field(tag=1, default="")
    question: str = Field(tag=2, default="")
    status: str = Field(tag=3, default="")
    close_after_seconds: int = Field(tag=4, default=0)
    winning_outcome: str = Field(tag=5, default="")
    bet_index_id: str = Field(tag=6, default="")
    audit_index_id: str = Field(tag=7, default="")
    yes_total: int = Field(tag=8, default=0)
    no_total: int = Field(tag=9, default=0)
    bet_count: int = Field(tag=10, default=0)
    audit_sequence: int = Field(tag=11, default=0)
    payout_pending_count: int = Field(tag=12, default=0)
    payout_succeeded_count: int = Field(tag=13, default=0)
    payout_failed_count: int = Field(tag=14, default=0)


class MarketCreateRequest(Model):
    creator_user_id: str = Field(tag=1, default="")
    question: str = Field(tag=2, default="")
    close_after_seconds: int = Field(tag=3, default=0)


class MarketRecord(Model):
    market_id: str = Field(tag=1, default="")
    creator_user_id: str = Field(tag=2, default="")
    question: str = Field(tag=3, default="")
    status: str = Field(tag=4, default="")
    close_after_seconds: int = Field(tag=5, default=0)
    winning_outcome: str = Field(tag=6, default="")
    yes_total: int = Field(tag=7, default=0)
    no_total: int = Field(tag=8, default=0)
    bet_count: int = Field(tag=9, default=0)
    payout_pending_count: int = Field(tag=10, default=0)
    payout_succeeded_count: int = Field(tag=11, default=0)
    payout_failed_count: int = Field(tag=12, default=0)
    bet_index_id: str = Field(tag=13, default="")
    audit_index_id: str = Field(tag=14, default="")


class MarketAddBetRequest(Model):
    bet_id: str = Field(tag=1, default="")
    outcome: str = Field(tag=2, default="")
    stake: int = Field(tag=3, default=0)


class MarketResolveRequest(Model):
    winning_outcome: str = Field(tag=1, default="")


class MarketRecordPayoutRequest(Model):
    payment_intent_id: str = Field(tag=1, default="")


class MarketNextAuditSequenceResponse(Model):
    sequence: int = Field(tag=1, default=0)


class BetState(Model):
    user_id: str = Field(tag=1, default="")
    market_id: str = Field(tag=2, default="")
    outcome: str = Field(tag=3, default="")
    stake: int = Field(tag=4, default=0)
    payout_amount: int = Field(tag=5, default=0)
    status: str = Field(tag=6, default="")
    payment_intent_id: str = Field(tag=7, default="")


class BetCreateRequest(Model):
    user_id: str = Field(tag=1, default="")
    market_id: str = Field(tag=2, default="")
    outcome: str = Field(tag=3, default="")
    stake: int = Field(tag=4, default=0)


class BetRecord(Model):
    bet_id: str = Field(tag=1, default="")
    user_id: str = Field(tag=2, default="")
    market_id: str = Field(tag=3, default="")
    outcome: str = Field(tag=4, default="")
    stake: int = Field(tag=5, default=0)
    payout_amount: int = Field(tag=6, default=0)
    status: str = Field(tag=7, default="")
    payment_intent_id: str = Field(tag=8, default="")


class BetMarkWonRequest(Model):
    payout_amount: int = Field(tag=1, default=0)
    payment_intent_id: str = Field(tag=2, default="")


class BetSetStatusRequest(Model):
    status: str = Field(tag=1, default="")


class PaymentIntentState(Model):
    user_id: str = Field(tag=1, default="")
    market_id: str = Field(tag=2, default="")
    bet_id: str = Field(tag=3, default="")
    amount: int = Field(tag=4, default=0)
    status: str = Field(tag=5, default="")
    idempotency_key: str = Field(tag=6, default="")
    failure_class: str = Field(tag=7, default="")
    attempts: list[PaymentAttempt] = Field(tag=8, default_factory=list)


class PaymentIntentCreateRequest(Model):
    user_id: str = Field(tag=1, default="")
    market_id: str = Field(tag=2, default="")
    bet_id: str = Field(tag=3, default="")
    amount: int = Field(tag=4, default=0)


class PaymentIntentRecordAttemptRequest(Model):
    attempt_number: int = Field(tag=1, default=0)
    status: str = Field(tag=2, default="")
    failure_class: str = Field(tag=3, default="")
    provider_transaction_id: str = Field(tag=4, default="")
    message: str = Field(tag=5, default="")


class PaymentIntentRecordStatusRequest(Model):
    status: str = Field(tag=1, default="")
    failure_class: str = Field(tag=2, default="")


class PaymentIntentRecordSuccessRequest(Model):
    provider_transaction_id: str = Field(tag=1, default="")


class AuditEventState(Model):
    market_id: str = Field(tag=1, default="")
    actor_user_id: str = Field(tag=2, default="")
    event_type: str = Field(tag=3, default="")
    message: str = Field(tag=4, default="")
    bet_id: str = Field(tag=5, default="")
    payment_intent_id: str = Field(tag=6, default="")
    sequence: int = Field(tag=7, default=0)


class AuditEventCreateRequest(Model):
    market_id: str = Field(tag=1, default="")
    actor_user_id: str = Field(tag=2, default="")
    event_type: str = Field(tag=3, default="")
    message: str = Field(tag=4, default="")
    bet_id: str = Field(tag=5, default="")
    payment_intent_id: str = Field(tag=6, default="")
    sequence: int = Field(tag=7, default=0)


UserMethods = Methods(
    show=UI(
        request=None,
        path="frontend/mcp/predictions",
        title="Prediction Markets",
        description="Operate prediction markets for the signed-in user.",
    ),
    dashboard=Reader(
        request=DashboardRequest,
        response=DashboardResponse,
        description="Open the signed-in user's prediction-market dashboard.",
        errors=[PredictionError],
        mcp=Tool(title="Dashboard"),
    ),
    create_market=Transaction(
        request=CreateMarketRequest,
        response=CreateMarketResponse,
        description="Create a binary YES/NO market.",
        errors=[PredictionError],
        mcp=Tool(title="Create market"),
    ),
    place_bet=Transaction(
        request=PlaceBetRequest,
        response=PlaceBetResponse,
        description="Place an integer-credit YES/NO bet.",
        errors=[PredictionError],
        mcp=Tool(title="Place bet"),
    ),
    resolve_market=Transaction(
        request=ResolveMarketRequest,
        response=ResolveMarketResponse,
        description="Resolve a closed market as its creator.",
        errors=[PredictionError],
        mcp=Tool(title="Resolve market"),
    ),
    close_market=Transaction(
        request=CloseMarketRequest,
        response=CloseMarketResponse,
        description="Close an open market as its creator.",
        errors=[PredictionError],
        mcp=Tool(title="Close market"),
    ),
    audit_log=Reader(
        request=AuditLogRequest,
        response=AuditLogResponse,
        description="Read a market audit log.",
        errors=[PredictionError],
        mcp=Tool(title="Read audit log"),
    ),
    apply_payout=Writer(
        request=ApplyPayoutRequest,
        response=None,
        mcp=None,
    ),
    add_payment=Transaction(
        request=AddPaymentRequest,
        response=None,
        mcp=None,
    ),
)


MarketCatalogMethods = Methods(
    ensure=Transaction(
        request=None,
        response=MarketCatalogEnsureResponse,
        mcp=None,
    ),
    add_market=Transaction(
        request=MarketCatalogAddMarketRequest,
        response=None,
        mcp=None,
    ),
)


MarketMethods = Methods(
    create=Writer(
        request=MarketCreateRequest,
        response=None,
        factory=True,
        mcp=None,
    ),
    get=Reader(
        request=None,
        response=MarketRecord,
        mcp=None,
    ),
    add_bet=Writer(
        request=MarketAddBetRequest,
        response=None,
        mcp=None,
    ),
    close_if_due=Writer(
        request=None,
        response=None,
        mcp=None,
    ),
    resolve=Writer(
        request=MarketResolveRequest,
        response=None,
        errors=[PredictionError],
        mcp=None,
    ),
    record_payout_pending=Writer(
        request=MarketRecordPayoutRequest,
        response=None,
        mcp=None,
    ),
    record_payout_succeeded=Writer(
        request=MarketRecordPayoutRequest,
        response=None,
        mcp=None,
    ),
    record_payout_failed=Writer(
        request=MarketRecordPayoutRequest,
        response=None,
        mcp=None,
    ),
    mark_review_required=Writer(
        request=MarketRecordPayoutRequest,
        response=None,
        mcp=None,
    ),
    next_audit_sequence=Writer(
        request=None,
        response=MarketNextAuditSequenceResponse,
        mcp=None,
    ),
)


BetMethods = Methods(
    create=Writer(
        request=BetCreateRequest,
        response=None,
        factory=True,
        mcp=None,
    ),
    get=Reader(
        request=None,
        response=BetRecord,
        mcp=None,
    ),
    mark_won=Writer(
        request=BetMarkWonRequest,
        response=None,
        mcp=None,
    ),
    set_status=Writer(
        request=BetSetStatusRequest,
        response=None,
        mcp=None,
    ),
)


PaymentIntentMethods = Methods(
    create=Writer(
        request=PaymentIntentCreateRequest,
        response=None,
        factory=True,
        mcp=None,
    ),
    get=Reader(
        request=None,
        response=PaymentIntentSummary,
        mcp=None,
    ),
    record_attempt=Writer(
        request=PaymentIntentRecordAttemptRequest,
        response=None,
        mcp=None,
    ),
    record_status=Writer(
        request=PaymentIntentRecordStatusRequest,
        response=None,
        mcp=None,
    ),
    record_success=Writer(
        request=PaymentIntentRecordSuccessRequest,
        response=None,
        mcp=None,
    ),
    run=Workflow(
        request=None,
        response=None,
        mcp=None,
    ),
)


AuditEventMethods = Methods(
    create=Writer(
        request=AuditEventCreateRequest,
        response=None,
        factory=True,
        mcp=None,
    ),
    get=Reader(
        request=None,
        response=AuditEventSummary,
        mcp=None,
    ),
)


api = API(
    User=Type(
        state=UserState,
        methods=UserMethods,
    ),
    MarketCatalog=Type(
        state=MarketCatalogState,
        methods=MarketCatalogMethods,
    ),
    Market=Type(
        state=MarketState,
        methods=MarketMethods,
    ),
    Bet=Type(
        state=BetState,
        methods=BetMethods,
    ),
    PaymentIntent=Type(
        state=PaymentIntentState,
        methods=PaymentIntentMethods,
    ),
    AuditEvent=Type(
        state=AuditEventState,
        methods=AuditEventMethods,
    ),
)
