# Governance: keeping agent teams from running amok

r4t is the single execution chokepoint for a team's turns. Every governance
mechanism below is enforced at dispatch or at outbox release — never inside
the LLM. Prompt etiquette ("don't send ack-only messages") is kept as a
courtesy, but no mechanism depends on an agent obeying it.

Everything here runs autonomously. There are knobs (config) and lenses
(`r4t status`, the dead-letter dir, a8s logs) but no gates: the system never
parks work waiting for a human, and it never drops a deliverable message.
The economics are *budgets, not cuts* — an agent that is out of budget does
not run (its mail queues), rather than having its mail thrown away.

## The failure modes

These are not hypothetical. An early in-repo agent dispatcher produced all
of them within days:

1. **Ack storms.** Agents acknowledging each other's acknowledgments —
   107 files matching `*reciprocal*`, turns burned restating the same
   facts, a real-money quota jump from ~10% to ~40% of a monthly plan.
   The CAMEL paper (NeurIPS 2023) documents the same phenomenon in
   role-playing agent pairs: "repeatedly thanking each other or saying
   goodbye without progressing the task," sometimes aware they are stuck
   but unable to break out. LLM self-awareness does not stop the loop;
   external enforcement does.
2. **Ping-pong through a re-broadcaster.** A chat room (h4l) re-emits
   posts as fresh notifications, stripping any per-conversation metadata —
   so anything keyed on conversation state is defeated on every bounce. The
   defense has to be cost (budgets) and idempotent arrival (duplicate
   collapse), not chain-length bookkeeping.
3. **Runaway fan-out.** One turn messages N teammates; each of those
   messages N more. Unbounded width, multiplied by depth.
4. **Stalled fan-in.** A leader delegates, a subordinate's process dies,
   and the leader never answers the human because nothing re-engages
   either side.
5. **Quota burn without work.** All of the above cost real tokens/credits
   while producing nothing.

## Why the transport can't help

All messages — including teammate-to-teammate — round-trip through a8s.
a8s is deliberately a dumb pipe (recipient opacity is its core invariant),
messages can originate from unmanaged senders, and rooms re-broadcast. So
the transport layer can neither attribute nor police traffic. r4t's answer
is to make *cost*, not message-dropping, the throttle: a durable per-member
queue absorbs every arrival, and spend budgets decide when a member is
allowed to spend a turn draining it.

## The stack

Ordered from most to least load-bearing. Each layer names its prior art;
parameters were verified against primary sources (references at bottom).

### 1. The durable member queue (nothing is ever dropped)

Every inbound message to a member enqueues, unconditionally, into
`agents/<member>/queue/`. No gate drops or dead-letters a deliverable
message — dead letters are reserved for genuinely *undeliverable* mail
(unknown recipient, disabled member, a rig that will not resolve). Work
waits; messages never die. This is the keystone the rest of the stack
hangs off: budgets, no-cutting, and batch invoke are all one data structure.

### 2. Duplicate collapse (the only "suppression")

When a message enqueues, if the NEWEST queued entry has the same sender and
identical normalized body, the arrival collapses into it — one entry, a
bumped `repeats` count — instead of adding a file. Collapsing loses no
information (the prompt notes "sent N times"), so it needs no window and
no dead letter. It replaces content-keyed pair suppression outright.

Prior art: RFC 3834 §2 (do not issue the same response to the same sender
repeatedly); syslogd's "last message repeated N times"; gemini-cli's
`LoopDetectionService`. The mechanism transfers; the difference is that
collapse *keeps* the count rather than discarding the repeats.

### 3. Batch invoke

A turn drains the WHOLE queue at pick time: one prompt renders every waiting
message chronologically ("Messages since your last turn"). An agent that
sees "teammates discussed X, then the lead overrode with Y" in one reading
pivots to the current state instead of burning a turn reacting to each
message in sequence. It is both a quota saver and a storm damper — a burst
of N arrivals costs one turn, not N.

### 4. Spend budgets (member + cell)

A bifurcated token bucket, refilled lazily by elapsed wall-clock time. Each
member has its own bucket (`budget_max` / `budget_earn_per_hour`) and the
whole cell shares one (`cell_budget_max` / `cell_budget_earn_per_hour`). A
turn costs 1 member unit AND 1 cell unit, regardless of how many messages it
consumes. Both must hold ≥1 for a member to run; an empty bucket means the
member is *resting* — its queue holds and it runs again when the bucket
refills. Put frontier rigs on a low budget (they run slowly and smartly) and
local rigs on a high one (near-free) so nothing goes to waste.

Prior art: real AI subscription plans (a rolling window plus a shared cap);
gRPC retry throttling (gRFC A6 — a token bucket that disables spend below a
floor); IRC flood control's burst-credit model (RFC 1459 §8.10). The design
lesson is graduated degradation: slow first, queue second, never drop.

### 5. Cadence throttle and concurrency cap

Team-wide floor on burn rate: a minimum interval between turn starts and a
cap on concurrent turns. Content- and topology-blind, so nothing evades it;
a perfectly evasive storm degrades into a slow, visible drip. A member that
can't start yet keeps its queue and runs on a later pass.

Prior art: IRC flood control — RFC 1459 §8.10 (burst credit, per-message
penalty; excess queues rather than drops), UnrealIRCd fake lag.

### 6. Per-member failure breaker

A member whose turns keep failing outright (nonzero exit or timeout — a bad
flag after a CLI update, a revoked key, a dead local model) would otherwise
burn a full turn on every arrival. After `breaker_cap` consecutive failed
turns the member's turns pause; its queue simply holds (nothing is dropped).
One probe turn is let through per `breaker_cooldown_seconds`; the first
clean turn closes the breaker.

Prior art: systemd start rate limiting (`StartLimitBurst`/
`StartLimitIntervalSec`) and the circuit-breaker pattern's
closed/open/half-open probe cycle.

### 7. Quiet-thread sweep (the termination backstop)

A thread (conversation label) can go quiet with its originator never having
heard back — a turn succeeds while staging no reply, or a chain stalls. When
an open thread whose originator is unanswered sees no activity for
`quiet_task_seconds`, the leader is woken with a nudge to report current
state — NOT to force-finish the work. The human, or the leader, decides what
"done" means; r4t only makes sure the originator is not left in silence.

Prior art: Erlang/OTP supervision — a bounded, rate-limited recovery action
rather than an unbounded retry loop.

## Disposal and observability

Undeliverable mail and per-turn send-quota overflow are never silently
dropped: they move to a dead-letter directory under the team's state dir
with an x-death-style record (reason, count, sender, recipient, thread,
time) — RabbitMQ's audit pattern. That is a lens and a replay source, not a
queue anyone waits on. Deliverable messages never land here; they queue.

Observability rides on a8s rather than duplicating it:

- Traffic: every message, with full `team:member` addresses, is already
  in a8s's transaction log and convo history — r4t adds no transport.
- Decisions: r4t's dispatch stdout is captured into the a8s node log by
  the wake machinery, so every governance action is one structured line
  (`r4t: RESTING bob — resting (member budget 0.0, ready in ~14 min) (3 queued)`) in the stream `a8s logs` already
  tails.
- State only r4t can have (locks, buckets, ledgers, dead letters):
  `r4t status` and the state dir.

## References

- RFC 3834, Recommendations for Automatic Responses to Electronic Mail —
  https://www.rfc-editor.org/rfc/rfc3834
- RFC 5230, Sieve Vacation Extension — https://www.rfc-editor.org/rfc/rfc5230
- RFC 5321 §6.3, SMTP loop detection — https://www.rfc-editor.org/rfc/rfc5321
- Postfix `hopcount_limit` — https://www.postfix.org/postconf.5.html
- procmail X-Loop convention — https://linux.die.net/man/5/procmailex
- RFC 1459 §8.10, IRC flood control — https://www.rfc-editor.org/rfc/rfc1459
- UnrealIRCd anti-flood — https://www.unrealircd.org/docs/Anti-flood_settings
- RabbitMQ federation max-hops — https://www.rabbitmq.com/docs/federation-reference
- RabbitMQ dead-letter exchanges — https://www.rabbitmq.com/docs/dlx
- Erlang/OTP supervision principles — https://www.erlang.org/doc/system/sup_princ.html
- systemd unit start rate limiting — https://www.freedesktop.org/software/systemd/man/latest/systemd.unit.html#StartLimitIntervalSec=interval
- AWS SQS dead-letter queue redrive (`maxReceiveCount`) — https://docs.aws.amazon.com/AWSSimpleQueueService/latest/SQSDeveloperGuide/sqs-dead-letter-queues.html
- gRFC A6, gRPC client retries — https://github.com/grpc/proposal/blob/master/A6-client-retries.md
- RFC 5681, TCP congestion control (AIMD) — https://www.rfc-editor.org/rfc/rfc5681
- AWS, Exponential Backoff and Jitter —
  https://aws.amazon.com/blogs/architecture/exponential-backoff-and-jitter/
- gemini-cli LoopDetectionService —
  https://github.com/google-gemini/gemini-cli/blob/main/packages/core/src/services/loopDetectionService.ts
- CAMEL: Communicative Agents (NeurIPS 2023) — https://arxiv.org/abs/2303.17760
- Anthropic, How we built our multi-agent research system —
  https://www.anthropic.com/engineering/multi-agent-research-system
- OpenAI Agents SDK `max_turns` —
  https://openai.github.io/openai-agents-python/running_agents/
- LangGraph `recursion_limit` —
  https://docs.langchain.com/oss/python/langgraph/errors/GRAPH_RECURSION_LIMIT
- CrewAI agent attributes — https://docs.crewai.com/en/concepts/agents
- AutoGen `max_consecutive_auto_reply` —
  https://microsoft.github.io/autogen/0.2/docs/reference/agentchat/conversable_agent/
