# agent-usage

Records what the AI agents that wrote a project cost: one JSON file per change in `.agent-usage/`, with the tokens
and the list-price cost of each model, read from the agents' session logs. `check` validates the files and the
`@agent` tags that name the model behind a class or function; `summary` sums everything up for a README.

One Python 3.11+ script, no dependencies. Agents adding it to a project can follow [SKILL.md](SKILL.md).

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

It also checks `@agent` tags in doc comments:

- `@agent <model>` is the last tag of the comment, one tag per model (`@agent claude-opus-5-5`).
- The model id is lowercase `name-version` without provider prefix: `claude-opus-5-5`, `gpt-5.6-sol`.
- Every tagged model appears in some usage file.
- A change that adds or changes `@agent` tags adds a usage file.

Files come from `git ls-files` (tracked and unignored). The comment style follows the extension:

| Style | Comments | Extensions |
| --- | --- | --- |
| `block` | `/** ... */` (KDoc, Javadoc, JSDoc, TSDoc, PHPDoc, Doxygen) | kt, kts, java, scala, groovy, js, jsx, mjs, cjs, ts, tsx, mts, cts, php, c, h, cc, cpp, hpp, swift |
| `rust` | `///`, `//!` and `/** ... */` | rs |
| `python` | triple-quoted strings that start a line | py, pyi |

`--include GLOB` and `--exclude GLOB` narrow the files, `--style GLOB=STYLE` sets the style of other files. Globs
match the path from the repository root, and `*` also matches `/`. All three can be repeated.

## Summary

```sh
./agent-usage summary                    # prints a markdown table of the totals per model
./agent-usage summary --update README.md # puts it between <!-- agent-usage --> and <!-- /agent-usage -->
```

## License

[MIT](LICENSE)
