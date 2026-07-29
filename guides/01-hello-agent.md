# Chapter 1 — Hello, Agent

## 1. Capability

At the end of this chapter you will have a registered a8s agent running on
your machine that you can message with `tell` and that messages you back.
You will have seen the whole loop — outbox, router, inbox, wake, reply —
and broken it once on purpose. No LLM is involved: your first agent is a
shell script, which is the point. An a8s agent is anything that can read a
message and send one.

## 2. Time

About 20 minutes.

## 3. Starting state

- The bin repo cloned at `~/bin` and on your PATH:

**Run**

```bash
git clone <YOUR_FORK_OR_CLONE_URL> ~/bin
source ~/bin/install.sh
```

- Python 3 installed (`python3 --version` answers).

Now type the suite's front door command:

**Run**

```bash
ark
```

You should see:

```
A R K
8 4 7
S T E

The Ark Suite — a8s routes the messages, r4t governs the teams,
k7e keeps what they learn. ark reads; each product owns its own verbs.

a8s — agent message router  (/home/you/.config/a8s)
  ✓ cli       a8s -> /home/you/bin/a8s
  ✗ registry  no registry at /home/you/.config/a8s/a8s.json   (try: a8s discover <dir>)

r4t — roster for teams  (/home/you/.config/r4t)
  ✓ cli    r4t -> /home/you/bin/r4t
  ✗ rigs   no rig config at /home/you/.config/r4t/rigs.json   (try: r4t init)
  ✗ teams  none under /home/you/.config/r4t/teams   (try: r4t init)

k7e — knowledge engine  (/home/you/.k7e)
  ✓ cli    k7e -> /home/you/bin/k7e
  ✗ store  no store at /home/you/.k7e   (try: k7e init)

next: ark doctor — probe the harnesses and tools the suite runs on
```

Three CLIs found, nothing configured — the correct fresh-machine state.
`ark` never changes anything; it reads and tells you which command owns the
next move. Take its suggestion:

**Run**

```bash
ark doctor
```

You should see:

```
ark doctor — probes only; nothing here is installed, started, or changed

Harnesses
  ✓ claude    2.1.220 (Claude Code)  (/home/you/.local/bin/claude)
  ✓ agent     2026.07.23-e383d2b  (/home/you/.local/bin/agent)
  ✓ codex     codex-cli 0.144.6  (/home/you/bin/codex)
  ✓ copilot   GitHub Copilot CLI 1.0.75.  (/home/you/bin/copilot)
  ✓ opencode  1.18.3  (/home/you/bin/opencode)
  ✓ agy       1.1.8  (/home/you/.local/bin/agy)
  ✓ ollama    ollama version is 0.32.5  (/home/you/bin/ollama)

Services
  ✓ ollama serve  3 model(s): qwen3.6:latest, qwen3:1.7b, qwen3:0.6b
  ✓ docker        daemon 29.6.2

Tooling
  ✓ git  git version 2.50.1 (Apple Git-155)

✓ core prerequisites satisfied  (10/10 probes green)
```

Your panel will show ✗ for harnesses you haven't installed — that is fine.
This chapter needs **none** of them. Chapter 2 needs exactly one: `ollama`
with a model pulled (free path) or `agent` (subscription path). If you want
to get ahead, install that one now; otherwise keep going.

## 4. The change

Two directories: one agent that replies, and one seat for you to speak
from.

The agent is a script. When a message arrives, a8s wakes the agent by
running the command in its *definition* — a small JSON file — substituting
`$SENDER` and `$MESSAGE`. Our script replies with the one verb every agent
gets: `tell`.

**Run**

```bash
mkdir -p ~/ark/hello ~/ark/me
```

**Create** `~/ark/hello/reply.sh`

```bash
#!/usr/bin/env bash
sender="$1"
message="$2"
tell "$sender" "hello received: $message"
```

**Create** `~/ark/hello/hello.json`

```json
{
  "description": "hello agent — replies to every tell with a script",
  "invoke": ["bash", "reply.sh", "$SENDER", "$MESSAGE"]
}
```

**Run**

```bash
chmod +x ~/ark/hello/reply.sh
```

`~/ark/me` is your **filedrop seat**: a directory a8s delivers your mail
into, with no CLI to wake — you read it with `tells`. Register both, then
look at the roster:

**Run**

```bash
a8s add me ~/ark/me filedrop
a8s add hello ~/ark/hello ~/ark/hello/hello.json
a8s ls
```

You should see:

```
added me -> /home/you/ark/me
definition: /home/you/bin/apps/a8s/definitions/filedrop.json  (explicit)
added hello -> /home/you/ark/hello
definition: /home/you/ark/hello/hello.json  (explicit)
NAME    STATUS    DEFINITION   ROOT
hello   stopped   hello        /home/you/ark/hello
me      stopped   filedrop     /home/you/ark/me
```

Registered but stopped: nothing routes until a handler process is attached.
Start one for each:

**Run**

```bash
a8s start hello
a8s start me
```

You should see:

```
started hello as PID 14186
started me as PID 14191
```

## 5. Run it

Speak from your seat. `tell` figures out who you are from the directory you
stand in, and `tells` waits by your inbox for what comes back:

**Run**

```bash
cd ~/ark/me
tell hello "are you alive?" && tells --timeout 30
```

## 6. Expected receipt

You should see:

```
tell -> hello: are you alive?
hello: hello received: are you alive?
```

The reply lands in a few seconds; `tells` prints it as it arrives and exits
when its window closes. That round trip crossed the full machinery: your
envelope was written to `~/ark/me/.outbox/`, the router stamped you as the
sender and moved it to hello's inbox, hello's handler woke `reply.sh`, and
the reply rode the same road back into `~/ark/me/.inbox/`.

## 7. Break it

Message an agent that does not exist:

**Run**

```bash
tell gost "are you alive?"
```

You should see:

```
tell: no agent or alias named 'gost'
```

The command exits nonzero and **nothing is written anywhere** — no
envelope, no dead letter, no queued retry. a8s validates the recipient
against the registry before accepting the message, so a typo fails at your
prompt instead of vanishing into a mailbox.

## 8. Diagnose

Two reads settle any "where did my message go?" question. The registry
tells you which names exist:

**Run**

```bash
a8s ls -q
```

You should see:

```
hello
me
```

And the per-agent log tells you what actually moved. Every accepted send,
route, and wake leaves a line:

**Run**

```bash
a8s logs hello --tail 5
```

You should see:

```
2026-07-29T04:45:58.330587Z received from me: are you alive?
2026-07-29T04:45:58.360655Z [hello] waking from 01ABC....json: are you alive?
2026-07-29T04:45:58.361043Z [hello] exec: bash reply.sh me 'are you alive?'
2026-07-29T04:45:58.439346Z tell -> me: hello received: are you alive?
2026-07-29T04:45:59.377730Z routed: hello -> me: hello received: are you alive?
```

The `gost` attempt appears in neither place — it was refused at the
boundary, so there is nothing to clean up.

## 9. Fix

Spell the name right and send again:

**Run**

```bash
tell hello "are you alive?" && tells --timeout 30
```

You should see:

```
tell -> hello: are you alive?
hello: hello received: are you alive?
```

## 10. Check

Ask the front door where the suite stands now:

**Run**

```bash
ark
```

You should see (a8s section):

```
a8s — agent message router  (/home/you/.config/a8s)
  ✓ cli       a8s -> /home/you/bin/a8s
  ✓ registry  2 agent(s), 0 alias(es), 0 namespace(s)
  ✓ router    attached: hello, me
```

The registry has your two agents and both handlers are attached. The r4t
and k7e sections still show ✗ — those are chapters 2 and 3.

## 11. Customize

Make the reply yours. One bounded edit — the agent's behavior is exactly
its script:

**Replace** `~/ark/hello/reply.sh` (whole file)

```bash
#!/usr/bin/env bash
sender="$1"
message="$2"
tell "$sender" "hello heard you loud and clear: ${message} (word count: $(echo "$message" | wc -w | tr -d ' '))"
```

No restart needed — the definition runs the script fresh on every wake:

**Run**

```bash
cd ~/ark/me
tell hello "one small step" && tells --timeout 30
```

You should see:

```
tell -> hello: one small step
hello: hello heard you loud and clear: one small step (word count: 3)
```

## 12. Commit point

Your agent is two files; keep them under version control like anything
else you built:

**Run**

```bash
cd ~/ark/hello
git init -q
git add reply.sh hello.json
git commit -q -m "hello agent: script + a8s definition"
```

Leave `hello` and `me` running — chapter 2 registers a third node beside
them and gives it something none of your agents have yet: a mind.
