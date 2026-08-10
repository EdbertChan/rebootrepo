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
  LogOut,
  Plus,
  Scale,
  ShieldCheck,
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
type Surface = "web" | "mcp";
type Outcome = "YES" | "NO";
type MarketTab = "open" | "closed" | "resolved";

const MARKET_TABS: MarketTab[] = ["open", "closed", "resolved"];
type AbortedLike = {
  error?: { code?: string; message?: string; type?: string };
  message?: string;
};

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

export function PredictionMarketApp({ surface }: { surface: Surface }) {
  const { user, isLoading } = useUser();
  const signIn = useSignIn();
  const signOut = useSignOut();

  if (isLoading) {
    return (
      <main className="pm-center">
        <Loader2 className="pm-spin" size={22} />
      </main>
    );
  }

  if (user === undefined) {
    return (
      <main className="pm-center">
        <section className="pm-signin">
          <Scale size={26} />
          <h1>Prediction Markets</h1>
          {surface === "web" ? (
            <button className="pm-button pm-button-primary" onClick={() => signIn()}>
              <ShieldCheck size={16} />
              Sign in
            </button>
          ) : (
            <p>Sign in required</p>
          )}
        </section>
      </main>
    );
  }

  return (
    <PredictionWorkspace
      user={user}
      surface={surface}
      onSignOut={() => signOut()}
    />
  );
}

function PredictionWorkspace({
  user,
  surface,
  onSignOut,
}: {
  user: UseUserApi;
  surface: Surface;
  onSignOut: () => void;
}) {
  const { response, isLoading, aborted } = user.useDashboard({
    cursor: "",
    limit: 100,
  });
  const [question, setQuestion] = useState("");
  const [closeAfterSeconds, setCloseAfterSeconds] = useState(0);
  const [selectedMarketId, setSelectedMarketId] = useState("");
  const [marketTab, setMarketTab] = useState<MarketTab>("open");
  const [error, setError] = useState("");
  const [isCreating, setIsCreating] = useState(false);

  const markets = response?.markets ?? [];
  const bets = response?.bets ?? [];
  const payments = response?.payments ?? [];
  const selectedMarket = useMemo(
    () => markets.find((market) => market.marketId === selectedMarketId),
    [markets, selectedMarketId],
  );
  const marketsByTab = useMemo(
    () => markets.filter((market) => market.status === marketTab),
    [markets, marketTab],
  );
  const openMarkets = markets.filter((market) => market.status === "open").length;
  const exposure = bets
    .filter((bet) => bet.status === "placed")
    .reduce((sum, bet) => sum + bet.stake, 0);

  useEffect(() => {
    if (aborted !== undefined) setError(friendlyError(aborted));
  }, [aborted]);

  useEffect(() => {
    if (selectedMarketId === "" && markets.length > 0) {
      setSelectedMarketId(markets[0].marketId);
    }
  }, [markets, selectedMarketId]);

  async function createMarket(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    if (question.trim() === "") return;
    setIsCreating(true);
    setError("");
    const result = await user.createMarket({
      question,
      closeAfterSeconds,
    });
    setIsCreating(false);
    if (result.aborted !== undefined) {
      setError(friendlyError(result.aborted));
      return;
    }
    setQuestion("");
    setCloseAfterSeconds(0);
    if (result.response !== undefined) {
      setSelectedMarketId(result.response.marketId);
    }
  }

  return (
    <main className={`pm-shell pm-${surface}`}>
      <header className="pm-topbar">
        <div>
          <p className="pm-eyebrow">Demo credits</p>
          <h1>Prediction Markets</h1>
        </div>
        {surface === "web" && (
          <button className="pm-button pm-button-quiet" onClick={onSignOut}>
            <LogOut size={16} />
            Sign out
          </button>
        )}
      </header>

      <section className="pm-metrics" aria-label="Account summary">
        <Metric icon={<CircleDollarSign size={18} />} label="Balance" value={credits(response?.balance ?? 0)} />
        <Metric icon={<ArrowDownUp size={18} />} label="Open exposure" value={credits(exposure)} />
        <Metric icon={<Clock size={18} />} label="Open markets" value={openMarkets.toString()} />
        <Metric icon={<FileText size={18} />} label="Audit target" value={selectedMarket?.status ? formatStatus(selectedMarket.status) : "none"} />
      </section>

      <section className="pm-grid">
        <aside className="pm-left">
          <form className="pm-panel pm-create" onSubmit={createMarket}>
            <div className="pm-panel-head">
              <h2>Create Market</h2>
              {isLoading && <Loader2 className="pm-spin pm-muted-icon" size={16} />}
            </div>
            <label>
              <span>Question</span>
              <input
                value={question}
                onChange={(event) => setQuestion(event.target.value)}
                placeholder="Will the next deploy pass?"
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
            <button className="pm-button pm-button-primary" disabled={isCreating}>
              {isCreating ? <Loader2 className="pm-spin" size={16} /> : <Plus size={16} />}
              Create
            </button>
          </form>

          <BetsPanel bets={bets} />
          <PaymentsPanel payments={payments} />
        </aside>

        <section className="pm-main">
          {error !== "" && (
            <div className="pm-error" role="alert">
              <AlertTriangle size={16} />
              {error}
            </div>
          )}

          <div className="pm-panel">
            <div className="pm-panel-head">
              <h2>Markets</h2>
              <span>{marketsByTab.length}</span>
            </div>
            <div className="pm-tabs" role="tablist" aria-label="Market status">
              {MARKET_TABS.map((tab) => (
                <button
                  key={tab}
                  type="button"
                  role="tab"
                  aria-selected={tab === marketTab}
                  className={tab === marketTab ? "active" : ""}
                  onClick={() => setMarketTab(tab)}
                >
                  {formatStatus(tab)}
                  <em>{markets.filter((market) => market.status === tab).length}</em>
                </button>
              ))}
            </div>
            <div className="pm-market-list">
              {marketsByTab.map((market) => (
                <MarketRow
                  key={market.marketId}
                  market={market}
                  selected={market.marketId === selectedMarketId}
                  user={user}
                  currentUserId={response?.userId ?? ""}
                  onSelect={() => setSelectedMarketId(market.marketId)}
                  onError={setError}
                />
              ))}
              {marketsByTab.length === 0 && (
                <div className="pm-empty">
                  <Scale size={20} />
                  <span>No {formatStatus(marketTab)} markets</span>
                </div>
              )}
            </div>
          </div>

          <AuditPanel
            user={user}
            market={selectedMarket}
            onError={setError}
          />
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
    <div className="pm-metric">
      {icon}
      <span>{label}</span>
      <strong>{value}</strong>
    </div>
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
  const canBet = market.status === "open";
  const canClose = isCreator && market.status === "open";
  const canResolve = isCreator && market.status === "closed";

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
    if (!canBet || stake <= 0) return;
    await mutate(() =>
      user.placeBet({
        marketId: market.marketId,
        outcome,
        stake,
      }),
    );
  }

  return (
    <article className={`pm-market ${selected ? "pm-selected" : ""}`}>
      <button className="pm-market-select" onClick={onSelect} title="Select audit log">
        <ChevronRight size={16} />
      </button>
      <div className="pm-market-body">
        <div className="pm-market-title">
          <h3>{market.question}</h3>
          <span className={`pm-badge pm-${market.status}`}>
            {formatStatus(market.status)}
          </span>
        </div>
        <div className="pm-market-stats">
          <span>YES {credits(market.yesTotal)}</span>
          <span>NO {credits(market.noTotal)}</span>
          <span>{market.betCount} bets</span>
          {market.winningOutcome !== "" && <span>{market.winningOutcome} won</span>}
        </div>

        <form className="pm-bet-form" onSubmit={placeBet}>
          <div className="pm-segmented" aria-label="Outcome">
            <button
              type="button"
              className={outcome === "YES" ? "active" : ""}
              onClick={() => setOutcome("YES")}
              disabled={!canBet}
            >
              YES
            </button>
            <button
              type="button"
              className={outcome === "NO" ? "active" : ""}
              onClick={() => setOutcome("NO")}
              disabled={!canBet}
            >
              NO
            </button>
          </div>
          <input
            aria-label="Stake"
            min={1}
            step={1}
            type="number"
            value={stake}
            onChange={(event) => setStake(Math.max(1, Number(event.target.value) || 1))}
            disabled={!canBet}
          />
          <button className="pm-button pm-button-secondary" disabled={!canBet || isPending}>
            {isPending ? <Loader2 className="pm-spin" size={15} /> : <CircleDollarSign size={15} />}
            Bet
          </button>
        </form>

        {(canClose || canResolve) && (
          <div className="pm-market-actions">
            {canClose && (
              <button
                className="pm-button pm-button-quiet"
                disabled={isPending}
                onClick={() =>
                  void mutate(() =>
                    user.closeMarket({ marketId: market.marketId }),
                  )
                }
              >
                <Clock size={15} />
                Close
              </button>
            )}
            {canResolve && (
              <>
                <div className="pm-segmented pm-small" aria-label="Resolution">
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
                  className="pm-button pm-button-primary"
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
              </>
            )}
          </div>
        )}
      </div>
    </article>
  );
}

function BetsPanel({ bets }: { bets: BetSummary[] }) {
  return (
    <section className="pm-panel">
      <div className="pm-panel-head">
        <h2>Bets</h2>
        <span>{bets.length}</span>
      </div>
      <div className="pm-compact-list">
        {bets.slice(0, 6).map((bet) => (
          <div className="pm-compact-row" key={bet.betId}>
            <div>
              <strong>{bet.outcome}</strong>
              <span>{bet.marketQuestion || bet.marketId}</span>
            </div>
            <em>{credits(bet.stake)} / {formatStatus(bet.status)}</em>
          </div>
        ))}
        {bets.length === 0 && <div className="pm-empty pm-small-empty">No bets</div>}
      </div>
    </section>
  );
}

function PaymentsPanel({ payments }: { payments: PaymentSummary[] }) {
  return (
    <section className="pm-panel">
      <div className="pm-panel-head">
        <h2>Payments</h2>
        <span>{payments.length}</span>
      </div>
      <div className="pm-compact-list">
        {payments.slice(0, 6).map((payment) => (
          <div className="pm-compact-row" key={payment.paymentIntentId}>
            <div>
              <strong>{credits(payment.amount)}</strong>
              <span>{formatStatus(payment.status)}</span>
            </div>
            <em>{payment.attempts.length} attempts</em>
          </div>
        ))}
        {payments.length === 0 && <div className="pm-empty pm-small-empty">No payments</div>}
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
      <section className="pm-panel">
        <div className="pm-panel-head">
          <h2>Audit</h2>
          <span>0</span>
        </div>
        <div className="pm-empty">
          <FileText size={20} />
          <span>No market selected</span>
        </div>
      </section>
    );
  }

  return <AuditLog user={user} market={market} onError={onError} />;
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
    <section className="pm-panel">
      <div className="pm-panel-head">
        <h2>Audit</h2>
        {isLoading ? <Loader2 className="pm-spin pm-muted-icon" size={16} /> : <span>{events.length}</span>}
      </div>
      <div className="pm-audit-list">
        {events.map((event) => (
          <AuditRow event={event} key={event.auditEventId} />
        ))}
        {events.length === 0 && (
          <div className="pm-empty">
            <FileText size={20} />
            <span>No audit events</span>
          </div>
        )}
      </div>
    </section>
  );
}

function AuditRow({ event }: { event: AuditEvent }) {
  return (
    <article className="pm-audit-row">
      <span>{formatStatus(event.eventType)}</span>
      <p>{event.message}</p>
    </article>
  );
}
