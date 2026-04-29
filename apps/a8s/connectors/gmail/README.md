# a8s Gmail connector

The first non-agent participant in a8s. A connector is just an a8s agent
whose definition's `invoke` runs a script instead of an LLM CLI. This one
bridges a8s and a human's email inbox via the [GAS Bridge][1].

[1]: https://github.com/neilco/gas

## Strict opacity

Other agents `tell <name> "..."` with no awareness that the recipient is
a Gmail-backed human (vs. another LLM agent or another script). The
connector is the only thing that knows about Gmail — there is no
email-shaped wire format on the a8s side. Outbound, the connector
decides "subject is `$SENDER`, body is `$MESSAGE`". Inbound, the cron
strips Gmail's `Re:` / `Fwd:` prefixes and routes the bare-token subject
to a registered participant via `tell`.

## What it does

- **Outbound**: when an a8s wake fires, `gmail_connector.py` POSTs
  `gmail.send` to the bridge with `subject=$SENDER` and `body=$MESSAGE`.
- **Inbound**: a cron-driven `gmail_cron.py` polls the bridge for
  `is:unread from:<configured-to>`, strips `Re:` / `Fwd:` repeats from
  the subject, resolves the result against the a8s registry, and shells
  `tell <participant> <body>` from the connector's registered root so
  a8s force-stamps `from` correctly.

The connector talks HTTP directly to the bridge — no Python deps beyond
stdlib, and no dependency on the `gas` CLI.

## Setup

1. Configure the bridge env vars (the connector and cron both read them):

   ```
   export GAS_BRIDGE_URL=https://script.google.com/.../exec
   export GAS_BRIDGE_KEY=...
   ```

2. Copy the example definition and edit `--to`:

   ```
   cp apps/a8s/connectors/gmail/example-definition.json ~/.neil-gmail.json
   $EDITOR ~/.neil-gmail.json   # set --to to your real recipient address
   ```

3. Register the connector as an a8s participant (name it after the
   recipient — if it routes to your inbox, name it after yourself):

   ```
   a8s add neil ~/bin/apps/a8s/connectors/gmail ~/.neil-gmail.json
   a8s start neil
   ```

4. Smoke-test outbound:

   ```
   tell neil "smoke test"
   ```

   An email arrives with subject = the agent enclosing your CWD and body
   = `"smoke test"`.

## Cron install

`gmail-cron` is a polyglot bash + PowerShell wrapper that calls
`gmail_cron.py` with the right definition path. Install in the user
crontab:

```
*/5 * * * * /Users/neilo/bin/apps/a8s/connectors/gmail/gmail-cron
```

Override the definition path with `A8S_GMAIL_DEF`:

```
A8S_GMAIL_DEF=~/.work-gmail.json /Users/neilo/bin/apps/a8s/connectors/gmail/gmail-cron
```

The wrapper expects `GAS_BRIDGE_URL` / `GAS_BRIDGE_KEY` in its
environment too. The cleanest way is a wrapper script in your crontab
that exports them before invoking the polyglot.

## How replies route

A reply email's subject is normally `Re: <original-subject>`. Since the
original subject was the sender's a8s name, stripping `Re:` (repeated,
case-insensitive, with optional whitespace) yields the participant the
human is replying to. Examples:

- `Re: NEIL` -> `NEIL`
- `Re: Re: NEIL` -> `NEIL`
- `RE:NEIL` -> `NEIL`
- `Fwd: NEIL` -> `NEIL`
- `re: fwd: NEIL` -> `NEIL`
- `NEIL urgent` -> left as `NEIL urgent`; `resolve_name` rejects it and
  the email stays unread with a warning to stderr

Unknown subjects are NOT marked read — operators can fix the subject in
Gmail and the next cron tick picks them up.

## Limitations

- No attachments yet. `FILE:` payloads in a8s messages are stringified
  into the email body but no MIME attachment is created. (Cross-cluster
  files / attachment passthrough is tracked in #62.)
- No HTML email — body is plain text only.
- Polling, not push. Reply latency is bounded by the cron interval.
- One Gmail account per connector instance. Multiple bridges = multiple
  registered participants pointing at separate definition files.
