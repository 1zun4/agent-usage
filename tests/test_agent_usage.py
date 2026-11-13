import gzip
import importlib.machinery
import importlib.util
import json
import subprocess
import tempfile
import unittest
from decimal import Decimal
from pathlib import Path

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
        self.git("add", ".")
        self.git("commit", "-q", "-m", "first")
        self.git("branch", "base")

    def tearDown(self):
        self.temp.cleanup()

    def git(self, *args):
        subprocess.run(["git", *args], cwd=self.root, check=True)

    def check(self):
        return au.check(self.root, self.usage, "base")

    def test_clean(self):
        self.assertEqual(self.check(), [])

    def test_files_are_never_edited(self):
        file = self.usage / "2026-11-09-6c84c195.json"
        file.write_text(file.read_text().replace("399.41", "1"))
        self.assertTrue(self.check()[0].startswith(".agent-usage/2026-11-09-6c84c195.json was changed"))
        file.unlink()
        self.assertTrue(self.check()[0].startswith(".agent-usage/2026-11-09-6c84c195.json was deleted"))

    def test_missing_base_skips_the_base_rules(self):
        (self.usage / "2026-11-09-6c84c195.json").unlink()
        self.assertEqual(au.check(self.root, self.usage, "origin/nothing"), [])

    def test_command_line(self):
        self.assertEqual(self.command("check", "--base", "base").returncode, 0)

    def command(self, *args):
        return subprocess.run([str(ROOT / "agent-usage"), *args], cwd=self.root, capture_output=True, text=True)

    def test_old_folder_is_used_when_alone(self):
        self.usage.rename(self.root / "agent-usage")
        self.assertIn("(1 usage files)", self.command("check", "--base", "origin/nothing").stdout)
        (self.root / ".agent-usage").mkdir()
        self.assertIn("(0 usage files)", self.command("check", "--base", "origin/nothing").stdout)
        self.assertIn("(1 usage files)", self.command("--dir", "agent-usage", "check", "--base", "origin/nothing").stdout)


if __name__ == "__main__":
    unittest.main()
