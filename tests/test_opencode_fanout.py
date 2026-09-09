from __future__ import annotations

import importlib.util
import json
from pathlib import Path
import subprocess
import tempfile
import unittest


SCRIPT = (
    Path(__file__).parents[1]
    / "plugins"
    / "opencode-codex-plugin"
    / "scripts"
    / "opencode_fanout.py"
)
SPEC = importlib.util.spec_from_file_location("opencode_fanout", SCRIPT)
assert SPEC and SPEC.loader
MODULE = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(MODULE)


class FanoutTests(unittest.TestCase):
    def test_load_tasks_normalizes_and_validates(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "tasks.json"
            path.write_text(
                json.dumps({"tasks": [{"id": "Docs Audit!", "prompt": "Inspect docs"}]}),
                encoding="utf-8",
            )
            self.assertEqual(
                MODULE.load_tasks(path),
                [{"id": "docs-audit", "prompt": "Inspect docs", "agent": "build"}],
            )

    def test_dry_run_creates_no_branch(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            repo = Path(directory) / "repo"
            repo.mkdir()
            subprocess.run(["git", "init", "-q", str(repo)], check=True)
            (repo / "tracked.txt").write_text("base\n", encoding="utf-8")
            subprocess.run(["git", "-C", str(repo), "add", "tracked.txt"], check=True)
            subprocess.run(
                [
                    "git", "-C", str(repo),
                    "-c", "user.name=Test",
                    "-c", "user.email=test@example.com",
                    "commit", "-qm", "base",
                ],
                check=True,
            )
            tasks = Path(directory) / "tasks.json"
            tasks.write_text(
                json.dumps([{"id": "one", "prompt": "Do one thing"}]),
                encoding="utf-8",
            )
            result = subprocess.run(
                [
                    "python", str(SCRIPT), "run", "--repo", str(repo),
                    "--tasks", str(tasks), "--models", "opencode/fake-free",
                    "--run-id", "test-run", "--dry-run",
                ],
                text=True,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                check=False,
            )
            self.assertEqual(result.returncode, 0, result.stderr)
            payload = json.loads(result.stdout)
            self.assertEqual(payload["plans"][0]["model"], "opencode/fake-free")
            branches = subprocess.run(
                ["git", "-C", str(repo), "branch", "--list", "opencode/*"],
                text=True,
                stdout=subprocess.PIPE,
                check=True,
            ).stdout
            self.assertEqual(branches.strip(), "")


if __name__ == "__main__":
    unittest.main()
