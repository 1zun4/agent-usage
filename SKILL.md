---
name: agent-usage
description: Use to add agent-usage to a project, or to record a commit's AI cost and @agent tags in a project that has it.
---

# agent-usage

A project with agent-usage keeps one JSON file per AI-made commit in `.agent-usage/` and marks code written by a model
with an `@agent <model>` doc tag. The script `agent-usage` (Python 3.11+, no dependencies) records, checks and sums
the files; README.md of this repository documents every option.

## Add it to a project

1. Fetch the script at a pinned tag, either in CI on each run or vendored into the repository:
   `curl -fsSLo agent-usage https://raw.githubusercontent.com/1zun4/agent-usage/v1.0.0/agent-usage`.
   Pin its checksum next to the download (`sha256sum -c`) so a moved tag fails the build.
2. Create `.agent-usage/` with the `schema.json` of this repository in it (optional; when present, `check` makes
   sure it matches the format).
3. Add a CI step that runs `agent-usage check --base origin/<target branch>` with the full history
   (`fetch-depth: 0`) and a `timeout-minutes`. Add `--include`, `--exclude` or `--style` when the defaults do not
   fit the project's sources.
4. Put `<!-- agent-usage -->` and `<!-- /agent-usage -->` into README.md where the totals go, then run
   `agent-usage summary --update README.md`.
5. Add the rules below to the project's AGENTS.md, with the commands as the project runs them.

## Rules for AGENTS.md

- Every commit made with an AI model adds its own file to `.agent-usage/`, recorded from the session logs of that
  commit (command below). Never edit or delete an existing file.
- Every class, interface, enum, object or top-level function a model wrote carries `@agent <model>` as the last tag
  of its doc comment, naming the model that writes it. No other doc: `/** @agent claude-opus-5-5 */` on its own.
- A model that later changes a function's logic adds its own tag; several models get one tag each, oldest first.
  Renames, imports, formatting and moves add none. Tags move with the code.
- Model ids as the provider names them, without provider prefix: `claude-opus-5-5`, `gpt-5.6-sol`.
- Code people wrote with AI only assisting gets no tag. Code taken from elsewhere keeps the tags it had there.
- After adding a usage file, refresh the README totals: `agent-usage summary --update README.md`.

## Record a commit

```sh
agent-usage record <session log or folder> --from <first call of this change> --change "<commit subject>"
```

- omp: `~/.omp/agent/sessions/<project>/<session>.jsonl`; subagent logs in the folder of the same name come along.
- Claude Code: `~/.claude/projects/<project>/<session>.jsonl`.
- Use `--from`/`--to` when a session made several commits; pass `--id <8 random hex>` for the second file of a
  session on the same day.
- Commit the new file with the change it belongs to, then run `agent-usage check` before pushing.
