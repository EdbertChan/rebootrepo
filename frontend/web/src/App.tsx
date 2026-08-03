import { useEffect, useMemo, useState, type FormEvent, type ReactNode } from "react";
import { useSignIn, useSignOut } from "@reboot-dev/reboot-react";
import {
  AlertTriangle,
  ArrowDownUp,
  Check,
  ChevronRight,
  CircleDollarSign,
  Clock,
  FileText,
  Loader2,
  LogIn,
  LogOut,
  Plus,
  Scale,
  ShieldCheck,
  Trophy,
  WalletCards,
} from "lucide-react";
import {
  type UseUserApi,
  useUser,
} from "@api/predictions/v1/predictions_rbt_react";

type DashboardResult = NonNullable<
  ReturnType<UseUserApi["useDashboard"]>["response"]
>;
type MarketSummary = DashboardResult["markets"][number];
type BetSummary = DashboardResult["bets"][number];
type PaymentSummary = DashboardResult["payments"][number];
type AuditResult = NonNullable<ReturnType<UseUserApi["useAuditLog"]>["response"]>;
type AuditEvent = AuditResult["events"][number];
type Outcome = "YES" | "NO";
type MarketTab = "open" | "closed" | "resolved";
type AbortedLike = {
  error?: { code?: string; message?: string; type?: string };
  message?: string;
};

const MARKET_TABS: MarketTab[] = ["open", "closed", "resolved"];

function friendlyError(aborted: AbortedLike): string {
  return (
    aborted.error?.message ??
    aborted.error?.code ??
    aborted.message ??
    "Request failed."
  );
}

function formatStatus(status: string): string {
  if (status === "") return "unknown";
  return status.replaceAll("_", " ");
}

function credits(value: number): string {
  return `${value.toLocaleString()} cr`;
}

function marketBucket(market: MarketSummary): MarketTab {
  if (market.status === "resolved") return "resolved";
  if (market.status === "closed") return "closed";
  return "open";
}

function countMarkets(markets: MarketSummary[], tab: MarketTab): number {
  return markets.filter((market) => marketBucket(market) === tab).length;
}

export function App() {
  const { user, isLoading } = useUser();
  const signIn = useSignIn();
  const signOut = useSignOut();

  if (isLoading) {
    return (
      <main className="app-center">
        <Loader2 className="spin" size={24} />
      </main>
    );
  }

  if (user === undefined) {
    return (
      <main className="app-center">
        <section className="signin-panel" aria-label="Sign in">
          <div className="signin-mark">
            <Scale size={28} />
          </div>
          <h1>Prediction Markets</h1>
          <p>Sign in to create markets, place bets, resolve outcomes, and review payouts.</p>
          <button className="button button-primary" onClick={() => signIn()}>
            <LogIn size={16} />
            Sign in
          </button>
        </section>
      </main>
    );
  }

  return <PredictionDashboard user={user} onSignOut={() => signOut()} />;
}

function PredictionDashboard({
  user,
  onSignOut,
}: {
  user: UseUserApi;
  onSignOut: () => void;
}) {
  const { response, isLoading, aborted } = user.useDashboard({
    cursor: "",
    limit: 100,
  });
  const [question, setQuestion] = useState("");
  const [closeAfterSeconds, setCloseAfterSeconds] = useState(0);
  const [selectedTab, setSelectedTab] = useState<MarketTab>("open");
  const [selectedMarketId, setSelectedMarketId] = useState("");
  const [error, setError] = useState("");
  const [isCreating, setIsCreating] = useState(false);

  const markets = response?.markets ?? [];
  const bets = response?.bets ?? [];
  const payments = response?.payments ?? [];
  const selectedMarket = useMemo(
    () => markets.find((market) => market.marketId === selectedMarketId),
    [markets, selectedMarketId],
  );
  const visibleMarkets = useMemo(
    () => markets.filter((market) => marketBucket(market) === selectedTab),
    [markets, selectedTab],
  );
  const activeBets = bets.filter((bet) => bet.status === "placed").length;
  const pendingPayouts = payments.filter(
    (payment) => payment.status === "pending",
  ).length;

  useEffect(() => {
    if (aborted !== undefined) setError(friendlyError(aborted));
  }, [aborted]);

  useEffect(() => {
    const stillVisible = visibleMarkets.some(
      (market) => market.marketId === selectedMarketId,
    );
    if (!stillVisible) {
      setSelectedMarketId(visibleMarkets[0]?.marketId ?? markets[0]?.marketId ?? "");
    }
  }, [markets, selectedMarketId, visibleMarkets]);

  async function createMarket(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    const trimmedQuestion = question.trim();
    if (trimmedQuestion === "") return;

    setIsCreating(true);
    setError("");
    const result = await user.createMarket({
      question: trimmedQuestion,
      closeAfterSeconds,
    });
    setIsCreating(false);

    if (result.aborted !== undefined) {
      setError(friendlyError(result.aborted));
      return;
    }

    setQuestion("");
    setCloseAfterSeconds(0);
    setSelectedTab("open");
    if (result.response !== undefined) {
      setSelectedMarketId(result.response.marketId);
    }
  }

  return (
    <main className="market-shell">
      <header className="topbar">
        <div className="brand-block">
          <div className="brand-icon">
            <Scale size={22} />
          </div>
          <div>
            <p>Signed-in dashboard</p>
            <h1>Prediction Markets</h1>
          </div>
        </div>
        <button className="button button-quiet" onClick={onSignOut}>
          <LogOut size={16} />
          Sign out
        </button>
      </header>

      <section className="metric-grid" aria-label="Account overview">
        <Metric
          icon={<WalletCards size={18} />}
          label="Account credits"
          value={credits(response?.balance ?? 0)}
        />
        <Metric
          icon={<ArrowDownUp size={18} />}
          label="Active bets"
          value={activeBets.toString()}
        />
        <Metric
          icon={<Clock size={18} />}
          label="Open markets"
          value={countMarkets(markets, "open").toString()}
        />
        <Metric
          icon={<ShieldCheck size={18} />}
          label="Pending payouts"
          value={pendingPayouts.toString()}
        />
      </section>

      <section className="workspace-grid">
        <aside className="side-stack">
          <form className="panel create-panel" onSubmit={createMarket}>
            <div className="panel-head">
              <div>
                <p>Creator</p>
                <h2>New Market</h2>
              </div>
              {isLoading ? <Loader2 className="spin muted-icon" size={16} /> : null}
            </div>
            <label>
              <span>Question</span>
              <input
                value={question}
                onChange={(event) => setQuestion(event.target.value)}
                placeholder="Will the release train finish today?"
                maxLength={180}
              />
            </label>
            <label>
              <span>Auto close seconds</span>
              <input
                min={0}
                step={1}
                type="number"
                value={closeAfterSeconds}
                onChange={(event) =>
                  setCloseAfterSeconds(Math.max(0, Number(event.target.value) || 0))
                }
              />
            </label>
            <button
              className="button button-primary"
              disabled={isCreating || question.trim() === ""}
            >
              {isCreating ? <Loader2 className="spin" size={16} /> : <Plus size={16} />}
              Create
            </button>
          </form>

          <BetsPanel bets={bets} />
          <PayoutPanel payments={payments} />
        </aside>

        <section className="main-stack">
          {error !== "" ? (
            <div className="error-banner" role="alert">
              <AlertTriangle size={16} />
              <span>{error}</span>
            </div>
          ) : null}

          <section className="panel markets-panel">
            <div className="panel-head">
              <div>
                <p>Order book</p>
                <h2>Markets</h2>
              </div>
              <span className="count-pill">{markets.length}</span>
            </div>

            <div className="tabs" aria-label="Market state">
              {MARKET_TABS.map((tab) => (
                <button
                  key={tab}
                  className={selectedTab === tab ? "active" : ""}
                  onClick={() => setSelectedTab(tab)}
                >
                  {tab}
                  <span>{countMarkets(markets, tab)}</span>
                </button>
              ))}
            </div>

            <div className="market-list">
              {visibleMarkets.map((market) => (
                <MarketRow
                  key={market.marketId}
                  currentUserId={response?.userId ?? ""}
                  market={market}
                  onError={setError}
                  onSelect={() => setSelectedMarketId(market.marketId)}
                  selected={market.marketId === selectedMarketId}
                  user={user}
                />
              ))}
              {visibleMarkets.length === 0 ? (
                <EmptyState
                  icon={<Scale size={22} />}
                  text={`No ${selectedTab} markets`}
                />
              ) : null}
            </div>
          </section>

          <AuditPanel market={selectedMarket} onError={setError} user={user} />
        </section>
      </section>
    </main>
  );
}

function Metric({
  icon,
  label,
  value,
}: {
  icon: ReactNode;
  label: string;
  value: string;
}) {
  return (
    <article className="metric">
      <div>{icon}</div>
      <span>{label}</span>
      <strong>{value}</strong>
    </article>
  );
}

function MarketRow({
  market,
  selected,
  user,
  currentUserId,
  onSelect,
  onError,
}: {
  market: MarketSummary;
  selected: boolean;
  user: UseUserApi;
  currentUserId: string;
  onSelect: () => void;
  onError: (message: string) => void;
}) {
  const [stake, setStake] = useState(25);
  const [outcome, setOutcome] = useState<Outcome>("YES");
  const [resolution, setResolution] = useState<Outcome>("YES");
  const [isPending, setIsPending] = useState(false);
  const isCreator = market.creatorUserId === currentUserId;
  const isOpen = marketBucket(market) === "open";
  const isClosed = marketBucket(market) === "closed";
  const canClose = isCreator && isOpen;
  const canResolve = isCreator && isClosed;
  const yesShare =
    market.yesTotal === 0 && market.noTotal === 0
      ? 50
      : Math.round((market.yesTotal / (market.yesTotal + market.noTotal)) * 100);

  async function mutate(action: () => Promise<{ aborted?: AbortedLike }>) {
    setIsPending(true);
    onError("");
    const result = await action();
    setIsPending(false);
    if (result.aborted !== undefined) {
      onError(friendlyError(result.aborted));
      return false;
    }
    return true;
  }

  async function placeBet(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    if (!isOpen || stake <= 0) return;
    await mutate(() =>
      user.placeBet({
        marketId: market.marketId,
        outcome,
        stake,
      }),
    );
  }

  return (
    <article className={`market-row ${selected ? "selected" : ""}`}>
      <button className="select-audit" onClick={onSelect} title="Select audit log">
        <ChevronRight size={16} />
      </button>

      <div className="market-content">
        <div className="market-title-row">
          <div>
            <h3>{market.question}</h3>
            <div className="market-meta">
              <span>{market.betCount} bets</span>
              {market.winningOutcome !== "" ? (
                <span>{market.winningOutcome} won</span>
              ) : null}
            </div>
          </div>
          <span className={`status-badge status-${marketBucket(market)}`}>
            {formatStatus(market.status)}
          </span>
        </div>

        <div className="market-depth" aria-label="Market depth">
          <div className="depth-bar">
            <span style={{ width: `${yesShare}%` }} />
          </div>
          <div className="depth-labels">
            <span>YES {credits(market.yesTotal)}</span>
            <span>NO {credits(market.noTotal)}</span>
          </div>
        </div>

        <form className="trade-row" onSubmit={placeBet}>
          <div className="segmented" aria-label="Bet outcome">
            <button
              type="button"
              className={outcome === "YES" ? "active" : ""}
              disabled={!isOpen}
              onClick={() => setOutcome("YES")}
            >
              YES
            </button>
            <button
              type="button"
              className={outcome === "NO" ? "active" : ""}
              disabled={!isOpen}
              onClick={() => setOutcome("NO")}
            >
              NO
            </button>
          </div>
          <input
            aria-label="Stake"
            disabled={!isOpen}
            min={1}
            step={1}
            type="number"
            value={stake}
            onChange={(event) => setStake(Math.max(1, Number(event.target.value) || 1))}
          />
          <button className="button button-secondary" disabled={!isOpen || isPending}>
            {isPending ? <Loader2 className="spin" size={15} /> : <CircleDollarSign size={15} />}
            Bet
          </button>
        </form>

        {canClose ? (
          <div className="resolve-row">
            <button
              className="button button-quiet"
              disabled={isPending}
              onClick={() =>
                void mutate(() =>
                  user.closeMarket({
                    marketId: market.marketId,
                  }),
                )
              }
            >
              <Clock size={15} />
              Close
            </button>
          </div>
        ) : null}

        {canResolve ? (
          <div className="resolve-row">
            <div className="segmented compact" aria-label="Resolution outcome">
              <button
                type="button"
                className={resolution === "YES" ? "active" : ""}
                onClick={() => setResolution("YES")}
              >
                YES
              </button>
              <button
                type="button"
                className={resolution === "NO" ? "active" : ""}
                onClick={() => setResolution("NO")}
              >
                NO
              </button>
            </div>
            <button
              className="button button-primary"
              disabled={isPending}
              onClick={() =>
                void mutate(() =>
                  user.resolveMarket({
                    marketId: market.marketId,
                    winningOutcome: resolution,
                  }),
                )
              }
            >
              <Check size={15} />
              Resolve
            </button>
          </div>
        ) : null}
      </div>
    </article>
  );
}

function BetsPanel({ bets }: { bets: BetSummary[] }) {
  return (
    <section className="panel compact-panel">
      <div className="panel-head">
        <div>
          <p>Positions</p>
          <h2>Bets</h2>
        </div>
        <span className="count-pill">{bets.length}</span>
      </div>
      <div className="compact-list">
        {bets.slice(0, 7).map((bet) => (
          <div className="compact-row" key={bet.betId}>
            <div>
              <strong>{bet.outcome}</strong>
              <span>{bet.marketQuestion || bet.marketId}</span>
            </div>
            <em>{credits(bet.stake)} / {formatStatus(bet.status)}</em>
          </div>
        ))}
        {bets.length === 0 ? <EmptyState text="No bets" /> : null}
      </div>
    </section>
  );
}

function PayoutPanel({ payments }: { payments: PaymentSummary[] }) {
  return (
    <section className="panel compact-panel">
      <div className="panel-head">
        <div>
          <p>Settlement</p>
          <h2>Payouts</h2>
        </div>
        <span className="count-pill">{payments.length}</span>
      </div>
      <div className="compact-list">
        {payments.slice(0, 7).map((payment) => (
          <div className="compact-row" key={payment.paymentIntentId}>
            <div>
              <strong>{credits(payment.amount)}</strong>
              <span>{formatStatus(payment.status)}</span>
            </div>
            <em>{payment.attempts.length} attempts</em>
          </div>
        ))}
        {payments.length === 0 ? <EmptyState text="No payouts" /> : null}
      </div>
    </section>
  );
}

function AuditPanel({
  user,
  market,
  onError,
}: {
  user: UseUserApi;
  market?: MarketSummary;
  onError: (message: string) => void;
}) {
  if (market === undefined) {
    return (
      <section className="panel audit-panel">
        <div className="panel-head">
          <div>
            <p>Trace</p>
            <h2>Audit</h2>
          </div>
          <span className="count-pill">0</span>
        </div>
        <EmptyState icon={<FileText size={22} />} text="No market selected" />
      </section>
    );
  }

  return <AuditLog market={market} onError={onError} user={user} />;
}

function AuditLog({
  user,
  market,
  onError,
}: {
  user: UseUserApi;
  market: MarketSummary;
  onError: (message: string) => void;
}) {
  const { response, isLoading, aborted } = user.useAuditLog({
    marketId: market.marketId,
    cursor: "",
    limit: 60,
  });
  const events = response?.events ?? [];

  useEffect(() => {
    if (aborted !== undefined) onError(friendlyError(aborted));
  }, [aborted, onError]);

  return (
    <section className="panel audit-panel">
      <div className="panel-head">
        <div>
          <p>Trace</p>
          <h2>Audit</h2>
        </div>
        {isLoading ? (
          <Loader2 className="spin muted-icon" size={16} />
        ) : (
          <span className="count-pill">{events.length}</span>
        )}
      </div>
      <div className="audit-list">
        {events.map((event) => (
          <AuditRow event={event} key={event.auditEventId} />
        ))}
        {events.length === 0 ? (
          <EmptyState icon={<FileText size={22} />} text="No audit events" />
        ) : null}
      </div>
    </section>
  );
}

function AuditRow({ event }: { event: AuditEvent }) {
  return (
    <article className="audit-row">
      <div>
        <span>{event.sequence}</span>
        <strong>{formatStatus(event.eventType)}</strong>
      </div>
      <p>{event.message}</p>
      <em>{event.actorUserId}</em>
    </article>
  );
}

function EmptyState({ icon, text }: { icon?: ReactNode; text: string }) {
  return (
    <div className="empty-state">
      {icon ?? <Trophy size={20} />}
      <span>{text}</span>
    </div>
  );
}
