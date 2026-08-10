# Inspecting the prediction-market audit trail

This app does not ship a separate observability product. For the MVP, the
source of truth for "what happened" is the durable Reboot actor state
itself — `AuditEvent` actors plus the app-level `User.audit_log` API. This
doc explains how operators and users can read that trail, either through
the app or directly against the running Reboot cluster with `rbt inspect`.

**Audit events are a record of what happened, not a source of truth for
current state.** Balances, bet status, and payout status all live on
their own actors (`User`, `Bet`, `PaymentIntent`, `Market`) and are
computed independently of the audit log. Never derive credit or payout
decisions from `AuditEvent` — only from those actors' own state.

## Durable actor types

| Actor | Purpose |
| --- | --- |
| `User` | One per signed-in user. Holds credit balance and index IDs into that user's created markets, bets, and payments. Front door for the MCP/web UI. |
| `MarketCatalog` | Singleton (`global`) index of every market ID, so markets can be listed without loading `User` state. |
| `Market` | One per market. Holds question, status (`open` / `closed` / `resolved` / `review_required`), YES/NO totals, payout counters, and the market's own `audit_index_id`. Also owns the `audit_sequence` counter used to order that market's audit trail. |
| `Bet` | One per placed bet. Holds the bettor, outcome, stake, payout amount, and status (`placed` / `won_pending` / `won_paid` / `lost` / `payment_failed`). |
| `PaymentIntent` | One per winning payout. Runs as a Reboot workflow (`PaymentIntent.run`) against a simulated gateway, with `at_least_once` attempts and a stable idempotency key. Holds attempt history and failure class. |
| `AuditEvent` | One per logged event. Immutable once created: market id, actor user id, event type, message, and optional `bet_id` / `payment_intent_id`, plus a market-scoped `sequence` number. |

## Reading the audit log through the app

Call `User.audit_log(market_id, cursor="", limit=0)` as any signed-in
user. It returns events for that market in chronological order (oldest
first), plus a `next_cursor` for paging through longer histories. This is
also exposed as an MCP tool ("Read audit log").

A market's full lifecycle produces events in this order:

1. `market_created` — emitted by `User.create_market`.
2. `bet_placed` — emitted by `User.place_bet`, once per bet.
3. `market_closed` — emitted by `User.close_market`, or by the scheduled
   `close_if_due` call when a market's close timer fires. Idempotent: closing
   an already-closed market does not add a second event.
4. `market_resolved` — emitted by `User.resolve_market` once the creator
   picks a winning outcome.
5. `payout_attempted` — emitted once per gateway attempt inside
   `PaymentIntent.run`, before the outcome is known.
6. `payout_succeeded`, `payout_failed`, or `payout_retry_exhausted` —
   emitted once the payout workflow reaches a terminal state for that
   attempt loop.

Events are ordered using a durable per-market `audit_sequence` counter on
the `Market` actor rather than wall-clock time. Payout events are appended
from inside a Reboot workflow, and workflow calls must be deterministic
across replays — a timestamp read at replay time would not match the
original run, so a durable counter is what keeps the log gapless and
correctly ordered even after a crash/replay.

## Reading state directly with `rbt inspect`

For lower-level debugging (e.g. confirming an audit event or actor exists
independently of the app's API), use the Reboot CLI against a running
`rbt dev run` or `rbt serve run` instance. `rbt dev run` prints the URL to
pass as `--application-url` (typically `http://127.0.0.1:9991`):

```sh
# List every registered actor type in this app.
rbt inspect type list --application-url=http://127.0.0.1:9991

# predictions.v1.MarketCatalog
# predictions.v1.User
# predictions.v1.Market
# predictions.v1.Bet
# predictions.v1.PaymentIntent
# predictions.v1.AuditEvent

# List state IDs for a given actor type.
rbt inspect state list --application-url=http://127.0.0.1:9991 \
  --type=predictions.v1.Bet

# Fetch a specific actor's current state by its state ID.
rbt inspect state get --application-url=http://127.0.0.1:9991 \
  --type=predictions.v1.Market --id=<market_id>
rbt inspect state get --application-url=http://127.0.0.1:9991 \
  --type=predictions.v1.AuditEvent --id=<audit_event_id>
rbt inspect state get --application-url=http://127.0.0.1:9991 \
  --type=predictions.v1.PaymentIntent --id=<payment_intent_id>
```

`AuditEvent` state IDs are either a random UUID (events written from a
transaction: `market_created`, `bet_placed`, `market_closed`,
`market_resolved`) or a deterministic id of the form
`<payment_intent_id>:<event_type>:<workflow_alias_suffix>` (events written
from the `PaymentIntent.run` workflow: `payout_attempted`,
`payout_succeeded`, `payout_failed`, `payout_retry_exhausted`) — the
deterministic form is what keeps payout audit writes safe to retry.

`rbt inspect` reads state directly; it does not go through `User`'s
authorization checks. Use the app API (`User.audit_log`) for anything
user-facing, and `rbt inspect` only for local operator debugging.
