import gzip
import importlib.machinery
import importlib.util
import json
import os
import shutil
import subprocess
import tempfile
import unittest
from decimal import Decimal
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parent.parent
FIXTURES = ROOT / "tests" / "fixtures"

loader = importlib.machinery.SourceFileLoader("agent_usage", str(ROOT / "agent-usage"))
spec = importlib.util.spec_from_loader("agent_usage", loader)
au = importlib.util.module_from_spec(spec)
loader.exec_module(au)


def write_jsonl(path, entries):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("".join(json.dumps(entry) + "\n" for entry in entries), encoding="utf-8")
    return path


def omp_message(time, model, input, output, cache_read, cache_write, total, provider="anthropic"):
    usage = {"input": input, "output": output, "cacheRead": cache_read, "cacheWrite": cache_write, "cost": {"total": total}}
    return {"type": "message", "timestamp": time, "message": {"role": "assistant", "provider": provider, "model": model, "usage": usage}}


def claude_line(time, message_id, model, input, output, cache_read=0, cache_write=0, cache_1h=0, sidechain=False):
    usage = {
        "input_tokens": input,
        "output_tokens": output,
        "cache_read_input_tokens": cache_read,
        "cache_creation_input_tokens": cache_write,
        "cache_creation": {"ephemeral_1h_input_tokens": cache_1h},
    }
    return {
        "type": "assistant",
        "timestamp": time,
        "sessionId": "c0ffee12-3456-7890-abcd-ef0123456789",
        "isSidechain": sidechain,
        "message": {"id": message_id, "model": model, "usage": usage},
    }


PRICES = au.PriceList(json.loads("""{"anthropic": {"models": {
    "claude-model-2": {"cost": {"input": 5, "output": 25, "cache_read": 0.5, "cache_write": 6.25}},
    "claude-model-2-20261101": {"cost": {"input": 5, "output": 25, "cache_read": 0.5, "cache_write": 6.25}},
    "claude-small-1": {"cost": {"input": 1, "output": 5}}
}}}""", parse_float=Decimal))


def no_prices():
    raise AssertionError("omp logs carry their cost; the price list is not needed")


class UsageFileTest(unittest.TestCase):

    def test_fixtures_are_valid(self):
        files = au.usage_files(FIXTURES / "agent-usage")
        self.assertEqual(len(files), 2)
        for file in files:
            self.assertEqual(au.validate(file.name, au.load(file)), [], file.name)

    def test_schema_matches_the_checked_fields(self):
        self.assertEqual(au.schema_problems("agent-usage", au.load(ROOT / "schema.json")), [])

    def test_writes_the_existing_format(self):
        fixture = FIXTURES / "agent-usage" / "2026-11-13-1d037abe.json"
        data = au.load(fixture)
        models = [(m["model"], m["provider"], m["tokens"], m["usd"]) for m in data["models"]]
        text = au.usage_json(au.date.fromisoformat(data["date"]), data["change"], data["tool"], models)
        self.assertEqual(text, fixture.read_text(encoding="utf-8"))

    def test_quotes_control_characters(self):
        self.assertEqual(au.quote('a "b" \\ c\nd\te'), '"a \\"b\\" \\\\ c\\nd\\u0009e"')

    def test_problems(self):
        good = au.load(FIXTURES / "agent-usage" / "2026-11-09-6c84c195.json")
        cases = {
            "the name is not `<yyyy-mm-dd>-<8 lowercase hex>.json`": ("2026-11-09-ABCDEF12.json", {}),
            "the name starts with 2026-11-10 but `date` is 2026-11-09": ("2026-11-10-6c84c195.json", {}),
            "`change` is missing": (None, {"change": None}),
            "`date` must be a string `yyyy-mm-dd`": (None, {"date": "2026-02-30"}),
            "unknown field `extra`": (None, {"extra": 1}),
            "`models` must be a non-empty array": (None, {"models": []}),
            "`models[0].usd` must be a number >= 0": (None, {"models": [dict(good["models"][0], usd=Decimal("-1"))]}),
            "`models[0].tokens.output` must be an integer >= 0": (
                None,
                {"models": [dict(good["models"][0], tokens=dict(good["models"][0]["tokens"], output=Decimal("1.5")))]},
            ),
        }
        for expected, (name, change) in cases.items():
            data = {key: value for key, value in dict(good, **change).items() if value is not None}
            self.assertIn(expected, au.validate(name or "2026-11-09-6c84c195.json", data))


class RecordTest(unittest.TestCase):

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.dir = Path(self.temp.name)
        self.out = self.dir / ".agent-usage"

    def tearDown(self):
        self.temp.cleanup()

    def omp_session(self):
        session = write_jsonl(self.dir / "logs" / "2026-11-13_0f3a9c2e-1111.jsonl", [
            {"type": "title", "title": "feat(cli): first version"},
            {"type": "session", "id": "0f3a9c2e-1111-2222-3333-444455556666", "timestamp": "2026-11-13T10:00:00.000Z"},
            omp_message("2026-11-13T10:00:05.000Z", "claude-model-2", 4, 400, 37000, 24000, 0.2),
            {"type": "message", "timestamp": "2026-11-13T10:00:06.000Z", "message": {"role": "user", "content": "hi"}},
            {"type": "model_usage", "timestamp": "2026-11-13T10:00:07.000Z", "provider": "anthropic",
             "model": "claude-small-1", "usage": {"input": 4, "output": 30, "cacheRead": 0, "cacheWrite": 0, "cost": {"total": 0.0002}}},
        ])
        write_jsonl(self.dir / "logs" / "2026-11-13_0f3a9c2e-1111" / "Helper.jsonl", [
            {"type": "session", "id": "99999999-aaaa", "parentSession": "0f3a9c2e"},
            omp_message("2026-11-13T11:00:00.000Z", "claude-model-2", 6, 600, 1000, 2000, 0.3),
            omp_message("2026-11-14T12:00:00.000Z", "gpt-9.1", 10, 20, 0, 0, 1.005, provider="openai"),
        ])
        return session

    def test_omp_session_with_subagent(self):
        file, logs, turns, _ = au.record([self.omp_session()], self.out, prices=no_prices)
        self.assertEqual(len(logs), 2)
        self.assertEqual(len(turns), 4)
        self.assertEqual(file.name, "2026-11-14-0f3a9c2e.json")
        data = au.load(file)
        self.assertEqual(data["change"], "feat(cli): first version")
        self.assertEqual(data["tool"], "omp")
        # claude-small-1 cost $0.0002, which rounds to nothing
        self.assertEqual([(m["model"], m["provider"], m["usd"]) for m in data["models"]], [
            ("gpt-9.1", "openai", Decimal("1.01")),
            ("claude-model-2", "anthropic", Decimal("0.50")),
        ])
        self.assertEqual(data["models"][1]["tokens"], {"input": 10, "cachedInput": 38000, "cacheWrite": 26000, "output": 1000})
        self.assertEqual(au.validate(file.name, data), [])

    def test_time_window_and_options(self):
        start = au.option_time("2026-11-13T10:30:00Z", "--from")
        end = au.option_time("2026-11-13T23:59:00+00:00", "--to")
        file, _, turns, _ = au.record(
            [self.omp_session()], self.out, start, end, "fix(cli): typo", "abcdef01", prices=no_prices
        )
        self.assertEqual(len(turns), 1)
        self.assertEqual(file.name, "2026-11-13-abcdef01.json")
        self.assertEqual(au.load(file)["change"], "fix(cli): typo")

    def test_never_overwrites(self):
        session = self.omp_session()
        au.record([session], self.out, prices=no_prices)
        with self.assertRaisesRegex(au.Failure, "already exists"):
            au.record([session], self.out, prices=no_prices)

    def test_rejects_bad_id(self):
        with self.assertRaisesRegex(au.Failure, "8 hex digits"):
            au.record([self.omp_session()], self.out, file_id="xyz", prices=no_prices)

    def test_claude_code_session(self):
        session = self.dir / "claude" / "c0ffee12.jsonl.gz"
        session.parent.mkdir()
        lines = [
            {"type": "summary", "summary": "docs: readme"},
            # One API call logged as two content blocks with the same message id
            claude_line("2026-11-13T10:00:00Z", "msg_1", "claude-model-2-20261101", 3, 200, 10000, 4000, 1000),
            claude_line("2026-11-13T10:00:01Z", "msg_1", "claude-model-2-20261101", 3, 200, 10000, 4000, 1000),
            claude_line("2026-11-13T10:00:02Z", "msg_2", "<synthetic>", 0, 0),
            claude_line("2026-11-13T10:00:03Z", "msg_3", "claude-small-1", 1000, 1000),
        ]
        with gzip.open(session, "wt", encoding="utf-8") as out:
            out.write("".join(json.dumps(line) + "\n" for line in lines))
        file, _, turns, _ = au.record([session.parent], self.out, prices=lambda: PRICES)
        self.assertEqual(len(turns), 2)
        self.assertEqual(file.name, "2026-11-13-c0ffee12.json")
        data = au.load(file)
        self.assertEqual(data["tool"], "claude-code")
        self.assertEqual(data["change"], "docs: readme")
        model = data["models"][0]
        self.assertEqual((model["model"], model["provider"]), ("claude-model-2", "anthropic"))
        # 3*5 + 10000*0.5 + 3000*6.25 + 1000*10 + 200*25 per million
        self.assertEqual(model["usd"], Decimal("0.04"))
        self.assertEqual(model["tokens"], {"input": 3, "cachedInput": 10000, "cacheWrite": 4000, "output": 200})

    def test_price_formula(self):
        counts = au.tokens(1_000_000, 1_000_000, 1_000_000, 1_000_000)
        self.assertEqual(PRICES.usd("anthropic", "claude-model-2", counts, 500_000), Decimal("38.625"))
        self.assertEqual(PRICES.usd("anthropic", "claude-small-1", counts, 0), Decimal(8))
        with self.assertRaisesRegex(au.Failure, "no price for anthropic/unknown-1"):
            PRICES.usd("anthropic", "unknown-1", counts, 0)

    def test_all_free(self):
        session = write_jsonl(self.dir / "free.jsonl", [
            {"type": "session", "id": "12345678"},
            omp_message("2026-11-13T10:00:00Z", "local-model-1", 5, 5, 0, 0, 0, provider="local"),
        ])
        with self.assertRaisesRegex(au.Failure, r"\$0.00"):
            au.record([session], self.out, change="x", prices=no_prices)


KOTLIN = """\
/**
 * A thing.
 *
 * @param x the x
 * @agent model-1
 */
class A

/** @agent other-2.5-fast */
fun b() = Unit

/**
 * ```
 * @agent not a tag in code
 * ```
 * @agent model-1
 * @since 2
 */
fun c() = Unit

/**
 * @agent Model
 */
fun d() = Unit
"""

RUST = """\
/// A thing.
///
/// @agent model-1
pub struct A;

//! @agent model-1
//! @see b
"""

PYTHON = '''\
def a():
    """A thing.

    @agent model-1
    """


class B:
    """@agent model-2"""


def c():
    r"""
    @agent model-1
    @raises ValueError
    """
'''


class AgentTagTest(unittest.TestCase):

    def test_block_comments(self):
        found, problems = au.agent_tags("A.kt", KOTLIN.splitlines(), "block")
        self.assertEqual(found, [(5, "model-1"), (9, "other-2.5-fast"), (16, "model-1"), (22, None)])
        self.assertEqual(problems, [
            "A.kt:17: `@since 2` follows an @agent tag; @agent tags go last",
            "A.kt:22: `@agent Model` is not `@agent <name>-<version>`",
        ])

    def test_rust_comments(self):
        found, problems = au.agent_tags("a.rs", RUST.splitlines(), "rust")
        self.assertEqual(found, [(3, "model-1"), (6, "model-1")])
        self.assertEqual(problems, ["a.rs:7: `@see b` follows an @agent tag; @agent tags go last"])

    def test_python_docstrings(self):
        found, problems = au.agent_tags("a.py", PYTHON.splitlines(), "python")
        self.assertEqual(found, [(4, "model-1"), (9, "model-2"), (14, "model-1")])
        self.assertEqual(problems, ["a.py:15: `@raises ValueError` follows an @agent tag; @agent tags go last"])

    def test_model_ids(self):
        for model in ("claude-opus-5-5", "deepseek-v4.1-flash", "gpt-5.6-sol", "gemini-3-pro"):
            self.assertTrue(au.MODEL_ID.fullmatch(model), model)
        for model in ("claude", "Claude-5", "anthropic/claude-5", ""):
            self.assertFalse(au.MODEL_ID.fullmatch(model), model)


class CheckTest(unittest.TestCase):

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name).resolve()
        self.usage = self.root / ".agent-usage"
        self.git("init", "-q", "-b", "main")
        self.git("config", "user.email", "test@example.com")
        self.git("config", "user.name", "Test")
        self.usage.mkdir()
        fixture = FIXTURES / "agent-usage" / "2026-11-09-6c84c195.json"
        (self.usage / fixture.name).write_bytes(fixture.read_bytes())
        (self.root / "a.kt").write_text("/** @agent claude-opus-5-5 */\nclass A\n")
        self.git("add", ".")
        self.git("commit", "-q", "-m", "first")
        self.git("branch", "base")

    def tearDown(self):
        self.temp.cleanup()

    def git(self, *args):
        subprocess.run(["git", *args], cwd=self.root, check=True)

    def check(self, **options):
        return au.check(self.root, self.usage, "base", **options)

    def test_clean(self):
        self.assertEqual(self.check(), [])

    def test_unknown_model(self):
        (self.root / "b.ts").write_text("/**\n * @agent gpt-9.1\n */\nexport function b() {}\n")
        self.git("add", ".")
        self.git("commit", "-q", "-m", "b")
        problems = self.check()
        self.assertEqual(len(problems), 2, problems)
        self.assertTrue(problems[0].startswith("Model `gpt-9.1` is named in @agent tags (b.ts:2)"))
        self.assertTrue(problems[1].startswith("This change adds or changes @agent tags (b.ts:2) but adds no .agent-usage file"))
        self.assertEqual(self.check(excludes=["*.ts"]), [])

    def test_new_tag_needs_a_new_file(self):
        (self.root / "c.kt").write_text("/** @agent claude-opus-5-5 */\nclass C\n")
        self.assertIn("(c.kt:1)", self.check()[0])
        data = au.load(FIXTURES / "agent-usage" / "2026-11-13-1d037abe.json")
        (self.usage / "2026-11-13-1d037abe.json").write_text(json.dumps(data, default=float))
        self.assertEqual(self.check(), [])

    def test_files_are_never_edited(self):
        file = self.usage / "2026-11-09-6c84c195.json"
        file.write_text(file.read_text().replace("399.41", "1"))
        self.assertTrue(self.check()[0].startswith(".agent-usage/2026-11-09-6c84c195.json was changed"))
        file.unlink()
        problems = self.check()
        self.assertTrue(problems[0].startswith("Model `claude-opus-5-5`"))
        self.assertTrue(problems[1].startswith(".agent-usage/2026-11-09-6c84c195.json was deleted"))

    def test_missing_base_skips_the_base_rules(self):
        (self.root / "c.kt").write_text("/** @agent claude-opus-5-5 */\nclass C\n")
        self.assertEqual(au.check(self.root, self.usage, "origin/nothing"), [])

    def test_style_option(self):
        (self.root / "d.txt").write_text("/** @agent Bad */\n")
        self.assertEqual(self.check(), [])
        self.assertIn("d.txt:1: `@agent Bad` is not `@agent <name>-<version>`", self.check(styles=[("*.txt", "block")]))

    def test_command_line(self):
        self.assertEqual(self.command("check", "--base", "base").returncode, 0)

    def command(self, *args):
        return subprocess.run([str(ROOT / "agent-usage"), *args], cwd=self.root, capture_output=True, text=True)

    def test_old_folder_is_used_when_alone(self):
        self.usage.rename(self.root / "agent-usage")
        self.assertIn("(1 usage files)", self.command("check", "--base", "origin/nothing").stdout)
        (self.root / ".agent-usage").mkdir()
        self.assertIn("in no .agent-usage file", self.command("check", "--base", "origin/nothing").stderr)
        self.assertIn("(1 usage files)", self.command("--dir", "agent-usage", "check", "--base", "origin/nothing").stdout)


class SummaryTest(unittest.TestCase):

    def test_table(self):
        table = au.summary_table(FIXTURES / "agent-usage")
        self.assertEqual(table, (
            "| Model | Provider | Input | Cached input | Cache write | Output | USD |\n"
            "| --- | --- | --: | --: | --: | --: | --: |\n"
            "| claude-opus-5-5 | anthropic | 210,983 | 1,444,062,783 | 12,287,392 | 2,885,128 | 445.66 |\n"
            "| claude-haiku-5-5 | anthropic | 847 | 0 | 0 | 6 | 0.00 |\n"
            "| **Total** | | **211,830** | **1,444,062,783** | **12,287,392** | **2,885,134** | **445.66** |\n"
            "\n"
            "2 changes from 2026-11-09 to 2026-11-13, in USD at the providers' list prices.\n"
        ))

    def test_update(self):
        with tempfile.TemporaryDirectory() as temp:
            readme = Path(temp) / "README.md"
            readme.write_text("# X\n\n<!-- agent-usage -->\nold\n<!-- /agent-usage -->\n\nmore\n")
            self.assertTrue(au.update_summary(readme, "table\n"))
            self.assertEqual(readme.read_text(), "# X\n\n<!-- agent-usage -->\ntable\n<!-- /agent-usage -->\n\nmore\n")
            self.assertFalse(au.update_summary(readme, "table\n"))
            readme.write_text("no markers\n")
            with self.assertRaises(au.Failure):
                au.update_summary(readme, "table\n")


SECRET_USAGE = {
    "date": "2026-11-15",
    "change": "feat(secret-addon): hidden feature",
    "tool": "omp",
    "models": [{"model": "gpt-5.6-sol", "provider": "openai", "tokens": {"input": 10, "cachedInput": 0, "cacheWrite": 0, "output": 5}, "usd": 1.5}],
}


class GitHubReply:
    def __init__(self, data):
        self.data = data

    def __enter__(self):
        return self

    def __exit__(self, *args):
        return False

    def read(self):
        return json.dumps(self.data).encode()


class RelatedTest(unittest.TestCase):

    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.secret = Path(temp.name) / "secret-addon"
        (self.secret / ".agent-usage").mkdir(parents=True)
        (self.secret / ".agent-usage" / "2026-11-15-0badc0de.json").write_text(json.dumps(SECRET_USAGE))
        self.own = Path(temp.name) / "own" / ".agent-usage"
        shutil.copytree(FIXTURES / "agent-usage", self.own)
        self.addCleanup(setattr, au.urllib.request, "urlopen", au.urllib.request.urlopen)
        self.addCleanup(setattr, au, "github_token", au.github_token)
        au.github_token = lambda: "token"
        environment = mock.patch.dict(os.environ)
        environment.start()
        self.addCleanup(environment.stop)
        os.environ.pop(au.RELATED_ENV, None)

    def reply(self, data):
        au.urllib.request.urlopen = lambda request, timeout: GitHubReply(data)

    def summary(self, *args, **env):
        result = subprocess.run(
            [str(ROOT / "agent-usage"), "--dir", str(FIXTURES / "agent-usage"), "summary", *args],
            capture_output=True,
            text=True,
            env={**os.environ, **env},
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        return result.stdout

    def assert_private(self, text):
        for secret in ("secret", "hidden", "0badc0de", "acme"):
            self.assertNotIn(secret, text)

    def test_checkout(self):
        table = self.summary("--related", str(self.secret))
        self.assertIn("| gpt-5.6-sol | openai | 10 | 0 | 0 | 5 | 1.50 |", table)
        self.assertIn("| **Total** | | **211,840** | **1,444,062,783** | **12,287,392** | **2,885,139** | **447.16** |", table)
        self.assertIn("3 changes from 2026-11-09 to 2026-11-15, 1 of them in 1 related repository,", table)
        self.assert_private(table)

    def test_json(self):
        output = self.summary("--json", **{au.RELATED_ENV: str(self.secret)})
        data = json.loads(output)
        self.assertEqual((data["usd"], data["changes"], data["relatedChanges"]), (447.16, 3, 1))
        self.assertEqual((data["first"], data["last"]), ("2026-11-09", "2026-11-15"))
        self.assertEqual(data["models"][0]["model"], "claude-opus-5-5")
        self.assert_private(output)

    def github_entries(self):
        return [
            {"name": "2026-11-15-0badc0de.json", "type": "blob", "object": {"text": json.dumps(SECRET_USAGE), "isTruncated": False}},
            {"name": "schema.json", "type": "blob", "object": {"text": "{}", "isTruncated": False}},
        ]

    def test_github(self):
        self.reply({"data": {"repository": {"current": {"entries": self.github_entries()}, "old": None}}})
        rows, changes, related, repos, _, last = au.totals(FIXTURES / "agent-usage", ["acme/secret-addon"])
        self.assertEqual((changes, related, repos, last), (3, 1, 1, "2026-11-15"))
        self.assertIn(("gpt-5.6-sol", "openai"), [row[:2] for row in rows])

    def test_environment(self):
        os.environ[au.RELATED_ENV] = f" {self.secret}  {self.secret}/ "
        self.assertEqual(au.totals(FIXTURES / "agent-usage")[1:4], (3, 1, 1))
        os.environ[au.RELATED_ENV] = " "
        with self.assertRaisesRegex(au.Failure, "AGENT_USAGE_RELATED is set but empty"):
            au.totals(FIXTURES / "agent-usage")

    def test_config(self):
        (self.own / "config.json").write_text('{"related": ["../secret-addon"]}')
        self.assertNotIn(self.own / "config.json", au.usage_files(self.own))
        self.assertEqual(au.totals(self.own)[1:4], (3, 1, 1))
        (self.own / "config.json").write_text('{"related": "acme/secret-addon"}')
        with self.assertRaisesRegex(au.Failure, "config.json must be"):
            au.totals(self.own)
        self.assertTrue(any("config.json must be" in problem for problem in au.check(self.own.parent, self.own, "none")))

    def test_duplicates_count_once(self):
        self.reply({"data": {"repository": {"current": {"entries": self.github_entries()}, "old": None}}})
        (self.own / "config.json").write_text('{"related": ["../secret-addon", "acme/secret-addon"]}')
        os.environ[au.RELATED_ENV] = f"{self.secret} ACME/Secret-Addon"
        related = [f"{self.secret}/", "acme/secret-addon", str(self.own.parent)]
        _, changes, related_changes, repos, _, _ = au.totals(self.own, related)
        self.assertEqual((changes, related_changes, repos), (4, 2, 2))
        self.assertIn("4 changes from 2026-11-09 to 2026-11-15, 2 of them in 2 related repositories,", au.summary_table(self.own, related))

    def test_own_github_repository_is_skipped(self):
        checkout = self.own.parent
        subprocess.run(["git", "init", "-q", str(checkout)], check=True)
        subprocess.run(["git", "-C", str(checkout), "remote", "add", "origin", "git@github.com:acme/Own.git"], check=True)
        au.urllib.request.urlopen = None
        self.assertEqual(au.totals(self.own, ["acme/own"])[1:4], (2, 0, 0))

    def test_missing_access(self):
        self.reply({"data": {"repository": None}, "errors": [{"message": "Could not resolve to a Repository with the name 'acme/secret-addon'."}]})
        with self.assertRaisesRegex(au.Failure, "Related repository 2 of --related was not found") as caught:
            au.summary_table(FIXTURES / "agent-usage", [str(self.secret), "acme/secret-addon"])
        self.assert_private(str(caught.exception))

        au.github_token = lambda: ""
        with self.assertRaisesRegex(au.Failure, "Related repository 1 of --related is on GitHub, but no token is set") as caught:
            au.summary_table(FIXTURES / "agent-usage", ["acme/secret-addon"])
        self.assert_private(str(caught.exception))

    def test_empty_or_unknown(self):
        (self.secret / ".agent-usage" / "2026-11-15-0badc0de.json").unlink()
        with self.assertRaisesRegex(au.Failure, "Related repository 1 of --related has no usage files"):
            au.totals(FIXTURES / "agent-usage", [str(self.secret)])
        with self.assertRaisesRegex(au.Failure, "Related repository 1 of --related is neither"):
            au.totals(FIXTURES / "agent-usage", ["no such folder"])


if __name__ == "__main__":
    unittest.main()
