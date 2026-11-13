# agent-usage

Records what the AI agents that wrote a project cost: one JSON file per change in `.agent-usage/`, with the tokens
and the list-price cost of each model, read from the agents' session logs. `check` validates the files.

One Python 3.11+ script, no dependencies.

## Install

```sh
curl -fsSLo agent-usage https://raw.githubusercontent.com/1zun4/agent-usage/v1.0.0/agent-usage
chmod +x agent-usage
```

Run it from the repository root. Usage files go to `.agent-usage/`; a project that only has `agent-usage/` keeps
using that. `--dir` (before the command) picks another folder.

## Record a change

```sh
./agent-usage record ~/.omp/agent/sessions/<project>/<session>.jsonl --change "feat(cli): first version"
./agent-usage record ~/.claude/projects/<project>/ --from 2026-11-13T14:00 --to 2026-11-13T18:30
```

Paths are session logs (`.jsonl`, `.jsonl.gz`) or folders of them; a log brings the subagent logs in the folder
of the same name. Supported: omp (oh-my-pi) and Claude Code.

- `--from`, `--to`: count only model calls in this window (ISO time; no offset means local time).
- `--change`: the commit subject; defaults to the session title.
- `--id`: 8 lowercase hex digits for the file name; defaults to the start of the session id.

omp logs carry the cost of each call. Claude Code logs only tokens, so they are priced from the list prices on
[models.dev](https://models.dev). Models that cost $0.00 are left out. The file is never overwritten.

```json
{
  "date": "2026-11-13",
  "change": "feat(cli): first version",
  "tool": "omp",
  "models": [
    {
      "model": "claude-opus-5-5",
      "provider": "anthropic",
      "tokens": { "input": 1058, "cachedInput": 149200068, "cacheWrite": 1209872, "output": 336290 },
      "usd": 46.25
    }
  ]
}
```

`cachedInput` counts cache reads, `cacheWrite` cache writes, `usd` the cost at the provider's list price.
[schema.json](schema.json) describes the format. Files can also be written by hand, named
`<yyyy-mm-dd>-<8 random hex>.json`.

## Check

```sh
./agent-usage check --base origin/main
```

Fails when a usage file is malformed, or when a file that exists at the merge base with `--base` was edited or
deleted. Without that ref the base rules are skipped.

## License

[MIT](LICENSE)
