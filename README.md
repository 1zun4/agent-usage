# agent-usage

Records what the AI agents that wrote a project cost: one JSON file per change in `.agent-usage/`, with the tokens
and the list-price cost of each model, read from the agents' session logs. `check` validates the files and the
`@agent` tags that name the model behind a class or function; `summary` sums everything up for a README.

One Python 3.11+ script, no dependencies. Agents adding it to a project can follow [SKILL.md](SKILL.md).

## Install

```sh
curl -fsSLo agent-usage https://raw.githubusercontent.com/1zun4/agent-usage/v1.1.0/agent-usage
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
./agent-usage summary --json             # the totals as JSON, for sites and cards
```

`--json` prints a stable format, described by [summary.schema.json](summary.schema.json); new fields may be added,
existing ones keep their meaning:

```json
{
  "usd": 4279.11,
  "changes": 183,
  "relatedChanges": 81,
  "first": "2026-10-27",
  "last": "2026-11-17",
  "models": [
    {
      "model": "claude-opus-5-5",
      "provider": "anthropic",
      "tokens": { "input": 551748, "cachedInput": 11669343182, "cacheWrite": 111731510, "output": 26818051 },
      "usd": 3766.36
    }
  ]
}
```

`usd` and `models` are sums over every change counted, related repositories included; `changes` counts them all and
`relatedChanges` those from related repositories. `models` holds one entry per model and provider, the most expensive
first, models that cost $0.00 included.

### Related repositories

The totals can include other repositories, private ones too: local checkouts, or `OWNER/REPO` read from the default
branch on GitHub. Three inputs name them, and all of them are used:

- `related` in `.agent-usage/config.json`, for repositories whose names may be public. Paths are relative to the
  repository root: `{ "related": ["my-org/public-addon", "../sibling-checkout"] }`.
- `AGENT_USAGE_RELATED`: names separated by spaces. This is the way to keep private names out of a public
  repository: set it from a CI secret. Set but empty fails, as an unset secret would otherwise go unnoticed.
- `--related NAME ...` on the command line.

A repository listed more than once, or the repository itself, counts once. The table footer and `--json` only add
how many changes came from related repositories; names, files and change subjects never appear in the output, and
errors name a repository only by its input and position (`Related repository 2 of AGENT_USAGE_RELATED`). A related
repository that is missing, unreadable or has no usage files fails the command, so totals are never short.

GitHub is read with `GH_TOKEN`, else `GITHUB_TOKEN`, else the login of the `gh` CLI. For private repositories use a
fine-grained token with access to just those repositories and the permission Contents: read-only.

A GitHub Actions step that writes the totals with private related repositories, with two repository secrets:
`AGENT_USAGE_TOKEN` (the token above) and `AGENT_USAGE_RELATED` (`my-org/private-a my-org/private-b`). Secrets are
masked in the public logs; repository variables are not.

```yaml
      - name: AI usage totals
        env:
          GH_TOKEN: ${{ secrets.AGENT_USAGE_TOKEN }}
          AGENT_USAGE_RELATED: ${{ secrets.AGENT_USAGE_RELATED }}
        run: |
          curl -fsSLo "$RUNNER_TEMP/agent-usage" https://raw.githubusercontent.com/1zun4/agent-usage/v1.1.0/agent-usage
          echo "c5c7a8335d239abcfea7ba5b61ff889b0903bb6f3543a903274aa8c33441f57d  $RUNNER_TEMP/agent-usage" | sha256sum -c -
          python3 "$RUNNER_TEMP/agent-usage" summary --json > "$RUNNER_TEMP/ai-usage.json"
```

Secrets are not passed to workflows of pull requests from forks, so run it on pushes or on a schedule, not on
`pull_request`.

## License

[MIT](LICENSE)
