# Governance: keeping agent teams from running amok

r4t is the single execution chokepoint for a team's turns. Every governance
mechanism below is enforced at dispatch or at outbox release — never inside
the LLM. Prompt etiquette ("don't send ack-only messages") is kept as a
courtesy, but no mechanism depends on an agent obeying it.

Everything here runs autonomously. There are knobs (config) and lenses
(`r4t status`, the dead-letter dir, a8s logs) but no gates: the system never
parks work waiting for a human. Where a human message appears in a chain, it
*licenses* more work (see the deliberate-decision rule); absence of a human
never blocks it.

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
   posts as fresh notifications, stripping any per-conversation metadata.
   Hop counters and task budgets reset on every bounce, so a two-agent
   loop through a room evades both.
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
the transport layer can neither attribute nor police traffic. Email faced
exactly this: two dumb autoresponders looping through a mailing list that
strips conversation context. The email stack's answer — classification +
content-keyed suppression + rate floors, with hop counting only as a
backstop — is the blueprint for r4t's, and notably hop counting was never
the mechanism that fixed the mailing-list case.

## The stack

Ordered from most to least load-bearing. Each layer names its prior art;
parameters were verified against primary sources (references at bottom).

### 1. Content-keyed pair suppression

The same (sender → recipient) pair sending substantially the same content
within a suppression window gets one delivery; the rest are dead-lettered.
The key is synthesized from *what is being sent to whom* — origin agent,
destination, normalized content hash — so it needs nothing a re-broadcaster
strips. This is the layer that kills room ping-pong.

Prior art: RFC 3834 §2 ("SHOULD NOT issue the same response to the same
sender more than once within a period of several days... 7-day period
RECOMMENDED"); Sieve vacation (RFC 5230 §4.2) tracks a *hash of the
response + recipient*, not conversation state; syslogd's "last message
repeated N times"; gemini-cli's `LoopDetectionService` (50-char chunk
hashes, loop declared at 10 repeats) is the same idea shipped in an LLM
harness. Agents work on much shorter cycles than vacationing humans, so
the window is minutes, not days — the mechanism, not the constant, is
what transfers.

### 2. Message-class marking

r4t stamps every agent-originated envelope as automated at outbox release
(the `Auto-Submitted: auto-replied` analog) and classifies inbound messages
from re-broadcaster nodes (h4l rooms) as bulk (the `List-Id` /
`Precedence: list` analog). Dispatch rule: a bulk-triggered turn may post
back to that room at most once per suppression window. Two agents cannot
loop through a hall — the exact composition that ended autoresponder
storms through mailing lists. This works because both r4t and h4l are our
infrastructure: the *router* carries the class stamp, not the payload the
room re-emits.

Prior art: RFC 3834 (auto-responders MUST NOT reply to `Auto-Submitted`
≠ no); procmail's `X-Loop` self-marker; BSD vacation(1) refusing
`Precedence: bulk/list/junk`.

### 3. Reply-privilege token bucket

Each agent has a bucket. Suppression/limit events drain it by 1; clean
turns earn back a fraction (~0.1). Below half-full, the agent's turns stop
running — inbound messages are still recorded to its history, so nothing
is lost — until the bucket recovers. A misbehaving agent mutes itself and
self-heals; ten clean turns buy back one strike.

Prior art: gRPC retry throttling (gRFC A6) — failures cost 1 token,
successes earn `tokenRatio` (~0.1), retries disabled below `maxTokens/2`.
Failure-dominated traffic shuts its own retries off with no operator in
the loop.

### 4. Per-task turn budgets with forced synthesis

Every task (conversation chain) has a weighted turn budget. Exhaustion
does not park or error: the *leader* is woken one final time with
"budget exhausted — respond to the originator with what you have." Tasks
always terminate in an answer.

Implementation tracks `synthesis_state` on the task ledger:
`pending` → `running` → `done`. The task closes when synthesis is
queued; exactly one leader turn may run while `pending`; concurrent
overflow arrivals defer or dead-letter rather than spawning a second
synthesis. When the leader's reply to the *task creator* is staged,
that envelope bypasses closed-task, hop-cut, and budget gates — otherwise
the final answer could never reach the originator. Only the first such
reply in a multi-recipient release gets the bypass.

Prior art: the multi-agent framework consensus band is 10–100 automated
exchanges before forced stop — OpenAI Agents SDK `max_turns=10`, LangGraph
`recursion_limit=25`, CrewAI `max_iter=20`, AutoGen
`max_consecutive_auto_reply=100`. CrewAI's semantics are the ones worth
copying: at the cap it forces a best answer rather than raising.

### 5. Hop limit

r4t stamps a task/hop header into envelope content at outbox release and
increments it on redispatch; past the limit the chain is cut and the task
originator informed once. Cheap, and defeated by re-broadcast (the room
mints a fresh message) — which is why it is the backstop, not the defense.

Prior art: RFC 5321 §6.3 (count Received headers, reject "normally at
least 100"; "servers MUST contain provisions for detecting and stopping
trivial loops"); Postfix `hopcount_limit=50`; RabbitMQ federation
`max-hops` (default 1).

### 6. Cadence throttle and concurrency cap

Team-wide floor on burn rate: a minimum interval between turn starts and
a cap on concurrent turns. Content- and topology-blind, so nothing evades
it; a perfectly evasive storm degrades into a slow, visible drip. This is
also the operator's "merry-go-round" lever — slow the team down enough to
watch it and reach in.

Prior art: IRC flood control — RFC 1459 §8.10 (10s of burst credit, 2s
penalty per message; excess queues rather than drops), UnrealIRCd fake
lag, solanum's flood tunables. The design lesson is graduated
degradation: slow first, queue second, drop only on overflow.

### 7. Governed recovery

Crash recovery (the idle-pass active list re-waking agents with
unfinished business) is itself rate-limited: at most N nudges per agent
per period, then the task is closed out through the leader with what
exists. Recovery machinery that retries without a ceiling is just another
storm generator.

Prior art: Erlang/OTP supervision restart intensity (default 1 restart
per 5 seconds; exceed it and the supervisor stops fixing and escalates) —
a rate limit on the recovery actions themselves.

### 8. Per-agent failure breaker

A member whose turns keep failing outright (nonzero exit or timeout —
a bad flag after a CLI update, a revoked key, a dead local model) would
otherwise burn a full turn on every inbound message while the asker
waits forever. After `breaker_cap` consecutive failed turns the member's
turns pause: inbound still records to history, but the message
dead-letters and its task closes through forced synthesis so the
originator gets an answer now. One probe turn is let through per
`breaker_cooldown_seconds`; the first clean turn closes the breaker. A
deliberate (non-`auto`) message resets it immediately — same license as
the budget reset.

Prior art: systemd start rate limiting (`StartLimitBurst`/
`StartLimitIntervalSec`, default 5 starts in 10s — exceed it and the
unit stops being restarted) and the circuit-breaker pattern's
closed/open/half-open probe cycle; the failure counter doubling as the
trip state mirrors SQS's `ReceiveCount` vs `maxReceiveCount` redrive
test.

### 9. The deliberate-decision rule

Cycles in which every step was automatic are killed; a human message
anywhere in a chain resets its budgets. Human attention is the license
for more work.

Prior art: RabbitMQ dead-letter cycle detection — "will detect a cycle
and drop the message if there was no rejection in the entire cycle,"
i.e. purely automatic loops die, loops containing a deliberate consumer
decision live.

## Disposal and observability

Suppressed and cut messages are never silently dropped: they move to a
dead-letter directory under the team's state dir with an x-death-style
record (reason, count, sender, recipient, task, time) — RabbitMQ's audit
pattern. That is a lens and a replay source, not a queue anyone waits on.

Observability rides on a8s rather than duplicating it:

- Traffic: every message, with full `team:member` addresses, is already
  in a8s's transaction log and convo history — r4t adds no transport.
- Decisions: r4t's dispatch stdout is captured into the a8s node log by
  the wake machinery, so every governance action is one structured line
  (`r4t: CUT task=.. hop=5 fiona->bob`) in the stream `a8s logs` already
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
