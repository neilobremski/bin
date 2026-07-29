# The Ark Raising

```
A R K
8 4 7
S T E
```

You will raise a small crew of AI agents from nothing, chapter by chapter,
on machinery you own. Each chapter builds one working thing — an agent you
can message, a governed roster of one, a team that remembers — on top of
the previous chapter's state, and every chapter ends with something you can
break, fix, and commit. The suite underneath is three tools:
[a8s](../apps/a8s/README.md) routes the messages, [r4t](../apps/r4t/README.md)
governs the teams, [k7e](../apps/k7e/README.md) keeps what they learn.

## The name

Read the wordmark by columns: `A-8-S`, `R-4-T`, `K-7-E` — the three product
names. Read it by rows: `ARK / 847 / STE` — the suite's initials spell ARK,
and the rows give it an address: **ARK, STE 847**. The name was there all
along; the wordmark just files the paperwork.

## Chapters

| # | Chapter | What you raise |
|---|---------|----------------|
| 01 | [Hello, Agent](01-hello-agent.md) | Your first a8s agent — tell it something, get a reply. No LLM required. |
| 02 | [The Solo Agent](02-the-solo-agent.md) | A governed roster of one: Wren, on r4t, with a conversation that persists. |
| 03 | [The Long Memory](03-the-long-memory.md) | Flush, refound, and rig portability — what Wren keeps when the conversation ends. |
| 04 | [A Second Pair of Hands](04-a-second-pair-of-hands.md) | The roster grows to two: Wren delegates, Moss answers for free, budgets bite. |
| 05+ | *(coming)* | k7e, more seats, cells, missions, and the machinery of a real crew. |

## Two blessed paths

Every chapter is completable with **zero subscriptions** — that is the
default path and the one every pasted output in these guides was captured
on:

- **Free path** — [OpenCode](https://opencode.ai/) driven through
  `ollama launch`, running a local model (`qwen3.6` in these guides). Your
  hardware, your weights, no meter.
- **Subscription path** — the Cursor agent CLI (`agent`), the one blessed
  paid harness. Same chapters, same commands, different rig preset.

Choose by answering one question: do you have a machine that can hold a
capable local model (roughly 16 GB+ of RAM/VRAM for `qwen3.6`-class)? If
yes, take the free path. If not — or you already pay for Cursor — take the
subscription path. Chapters mark the fork with a "pick your path" block;
everything outside those blocks is identical.

## Conventions

Every code block in a chapter is one of four declared types, marked with a
bold label line directly above the fence:

- **Run** — paste into a shell exactly as written.
- **Create** — the complete contents of a new file; the path is given in
  the label line.
- **Replace** — replaces an existing file (or a named section of one); the
  exact file and anchor are given in the label line.
- **Patch** — a unified diff to apply.

Rules the blocks obey, so you can trust your clipboard:

- No unexplained `...` inside a copyable block.
- No `$` prompts mixed into commands.
- Anything you must substitute yourself looks `<LIKE_THIS>`.
- Expected output appears in its own fence directly after a Run block,
  introduced by "You should see:".

**Normalization, stated once:** every "You should see:" block comes from a
real run captured on a real machine. Where that output contained absolute
paths, they are normalized to `/home/you/...` (your own home and repo paths
appear instead); ULIDs are shortened to `01ABC...`; timestamps and PIDs are
from the capture runs — treat them as placeholders, yours will differ.
Everything else is verbatim.

### The 12-step contract

Every chapter walks the same twelve steps, in order:

1. **Capability** — what you can do at the end that you couldn't before.
2. **Time** — how long it takes.
3. **Starting state** — what must already be true.
4. **The change** — the files you create or edit.
5. **Run it** — the commands.
6. **Expected receipt** — the real output that proves it worked.
7. **Break it** — a deliberate failure.
8. **Diagnose** — reading the error and the logs.
9. **Fix** — the repair.
10. **Check** — independent confirmation the system is healthy again.
11. **Customize** — one bounded edit to make it yours.
12. **Commit point** — what to commit before moving on.
