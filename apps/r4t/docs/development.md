# Development: testing and layout

## Testing

- **Unit + fake sandbox (plumbing):** `r4t sandbox --fake` runs a bundled
  three-agent team (Lead/Dev/Tester building a tiny battleship game)
  against deterministic scripted agents — no LLM calls — inside a
  throwaway `A8S_HOME`/`R4T_HOME`, then emits a self-contained report on
  **stdout** (progress on **stderr**). MECHANICAL CHECKS are computed
  (program built and runs, leader answered the originator, turns within
  budget, zero orphan processes, dead-letter counts). The pytest suite
  runs it end to end.
- **Live sandbox (acceptance / eval):** `r4t sandbox` (no `--fake`) runs
  the same scenario with a real harness. Pick any named preset:
  `r4t sandbox --preset opencode` (default), or local models via Ollama:
  `r4t sandbox --preset opencode-ollama --model qwen2.5-coder:7b`.
  Other presets (`claude`, `codex`, `cursor`, `agy`, …) work the same
  way — see `r4t rig presets`. `live-agent.py` prepends explicit
  per-role steps and stages protocol tells if the model skips them.
  Save the report: `r4t sandbox --preset agy > report.md`

```bash
python3 -m pytest apps/r4t/tests/     # from anywhere in ~/bin — the repo
                                      # venv wrapper supplies pytest
```

## Layout

`r4t.py` (CLI) · `dispatch.py` (enqueue, batch turns, staging
release, quiet-thread sweep, mission-review) · `tasks.py` (thread ledger) · `state.py`
(all on-disk state under `$R4T_HOME`) · `rig.py` (rig config, presets,
model resolution) · `roster.py` · `org.py` (org dirs + settings) ·
`check.py` (verification sweep) · `verdict.py` (health verdicts +
dead-letter rollup, shared by status and chat) · `chat.py` (seat feed +
line UI) · `chat_tui.py` (Textual front end) · `notify.py` (doorbell) ·
`sandbox.py` + `sandbox/` (the end-to-end harness).
Observability rides on a8s: traffic in the a8s txlog/convo, r4t decision
lines in the node log via dispatch stdout, r4t-only state via `r4t status`.
