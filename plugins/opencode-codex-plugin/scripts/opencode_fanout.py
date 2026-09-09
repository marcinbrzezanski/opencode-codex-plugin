#!/usr/bin/env python3
"""Run independent OpenCode tasks concurrently in isolated Git worktrees."""

from __future__ import annotations

import argparse
import concurrent.futures
import datetime as dt
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys
import tempfile
from typing import Any


DEFAULT_FREE_MODELS = (
    "opencode/big-pickle",
    "opencode/mimo-v2.5-free",
    "opencode/ling-3.0-flash-fin-free",
    "opencode/nemotron-3-ultra-free",
    "opencode/nemotron-3.5-lightning-free",
    "opencode/muse-spark-1.3-contributor-free",
)
FREE_MARKERS = ("-free", "big-pickle")


class RunnerError(RuntimeError):
    """A user-actionable orchestration error."""


def run_command(
    command: list[str],
    *,
    cwd: Path | None = None,
    timeout: int | None = None,
    check: bool = True,
) -> subprocess.CompletedProcess[str]:
    """Run a command and capture text output."""
    try:
        result = subprocess.run(
            command,
            cwd=cwd,
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            timeout=timeout,
            check=False,
        )
    except FileNotFoundError as exc:
        raise RunnerError(f"Command not found: {command[0]}") from exc
    except subprocess.TimeoutExpired as exc:
        raise RunnerError(f"Command timed out after {timeout}s: {command[0]}") from exc
    if check and result.returncode != 0:
        output = result.stdout.strip()
        raise RunnerError(
            f"Command failed ({result.returncode}): {' '.join(command)}"
            + (f"\n{output}" if output else "")
        )
    return result


def git(repo: Path, *args: str, check: bool = True) -> subprocess.CompletedProcess[str]:
    """Run Git in *repo*."""
    resolved = repo.resolve()
    return run_command(
        ["git", "-c", f"safe.directory={resolved.as_posix()}", "-C", str(resolved), *args],
        check=check,
    )


def normalize_id(value: str) -> str:
    """Return a branch- and path-safe task identifier."""
    normalized = re.sub(r"[^a-zA-Z0-9._-]+", "-", value).strip("-.").lower()
    if not normalized:
        raise RunnerError(f"Task id has no usable characters: {value!r}")
    return normalized[:48]


def parse_model_list(raw: str | None) -> list[str]:
    """Parse and deduplicate a comma-separated model list."""
    if not raw:
        return []
    result: list[str] = []
    for item in raw.split(","):
        model = item.strip()
        if model and model not in result:
            result.append(model)
    return result


def discover_models(executable: str) -> list[str]:
    """Return full OpenCode model IDs reported by the installed CLI."""
    result = run_command([executable, "models", "opencode"], check=False)
    if result.returncode != 0:
        raise RunnerError(result.stdout.strip() or "OpenCode model discovery failed")
    models = []
    for line in result.stdout.splitlines():
        candidate = line.strip().split()[0] if line.strip() else ""
        if candidate.startswith("opencode/") and candidate not in models:
            models.append(candidate)
    return models


def select_models(executable: str, explicit: str | None) -> tuple[list[str], str]:
    """Select explicit, environment, discovered-free, or fallback models."""
    requested = parse_model_list(explicit)
    if requested:
        return requested, "--models"
    requested = parse_model_list(os.environ.get("OPENCODE_FREE_MODELS"))
    if requested:
        return requested, "OPENCODE_FREE_MODELS"
    try:
        available = discover_models(executable)
    except RunnerError as exc:
        print(f"warning: {exc}; using the built-in free-model pool", file=sys.stderr)
        return list(DEFAULT_FREE_MODELS), "built-in fallback"
    free = [model for model in available if any(mark in model for mark in FREE_MARKERS)]
    if free:
        return free, "OpenCode model discovery"
    print("warning: no free models identified; using the built-in pool", file=sys.stderr)
    return list(DEFAULT_FREE_MODELS), "built-in fallback"


def load_tasks(path: Path) -> list[dict[str, str]]:
    """Load and validate a task-list JSON document."""
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise RunnerError(f"Cannot read task file {path}: {exc}") from exc
    raw_tasks = data.get("tasks") if isinstance(data, dict) else data
    if not isinstance(raw_tasks, list) or not raw_tasks:
        raise RunnerError("Task file must contain a non-empty list or a {'tasks': [...]} object")
    tasks: list[dict[str, str]] = []
    seen: set[str] = set()
    for index, raw in enumerate(raw_tasks, 1):
        if not isinstance(raw, dict):
            raise RunnerError(f"Task {index} must be an object")
        task_id = normalize_id(str(raw.get("id", f"task-{index}")))
        prompt = raw.get("prompt")
        agent = raw.get("agent", "build")
        if task_id in seen:
            raise RunnerError(f"Duplicate normalized task id: {task_id}")
        if not isinstance(prompt, str) or not prompt.strip():
            raise RunnerError(f"Task {task_id} requires a non-empty prompt")
        if agent not in {"build", "plan"}:
            raise RunnerError(f"Task {task_id} agent must be 'build' or 'plan'")
        seen.add(task_id)
        tasks.append({"id": task_id, "prompt": prompt.strip(), "agent": agent})
    return tasks


def ensure_repository(repo: Path, allow_dirty: bool) -> str:
    """Validate repository state and return the base commit."""
    root = Path(git(repo, "rev-parse", "--show-toplevel").stdout.strip()).resolve()
    if root != repo.resolve():
        raise RunnerError(f"--repo must be the Git root: {root}")
    status = git(repo, "status", "--porcelain").stdout.strip()
    if status and not allow_dirty:
        raise RunnerError(
            "Main checkout is dirty. Commit/stash parent changes or pass --allow-dirty "
            "after accepting that workers only see HEAD."
        )
    return git(repo, "rev-parse", "HEAD").stdout.strip()


def worker_prompt(task: dict[str, str]) -> str:
    """Append a stable worker contract to a task prompt."""
    if task["agent"] == "plan":
        contract = (
            "\n\nWorker contract: read all applicable AGENTS.md files. Investigate only this "
            "task. Do not modify files. Give concrete file and line references, commands run, "
            "uncertainties, and a concise recommendation."
        )
    else:
        contract = (
            "\n\nWorker contract: read all applicable AGENTS.md files. Work only on this task "
            "and avoid unrelated edits. Implement a complete solution, run focused validation, "
            "and summarize changed files, tests, and remaining risks. The harness will commit "
            "your work after you finish."
        )
    return task["prompt"] + contract


def create_worktree(repo: Path, worktree: Path, branch: str, base: str) -> None:
    """Create one isolated worker checkout."""
    worktree.parent.mkdir(parents=True, exist_ok=True)
    git(repo, "worktree", "add", "-b", branch, str(worktree), base)


def commit_worker_changes(worktree: Path, task_id: str) -> tuple[str | None, bool]:
    """Commit all worker changes and return (commit, changed)."""
    changed = bool(git(worktree, "status", "--porcelain").stdout.strip())
    if not changed:
        return None, False
    git(worktree, "add", "-A")
    result = run_command(
        [
            "git", "-c", f"safe.directory={worktree.resolve().as_posix()}",
            "-C", str(worktree),
            "-c", "user.name=OpenCode Worker",
            "-c", "user.email=opencode-worker@localhost",
            "commit", "-m", f"opencode worker: {task_id}",
        ],
        check=False,
    )
    if result.returncode != 0:
        raise RunnerError(f"Could not commit worker {task_id}:\n{result.stdout.strip()}")
    return git(worktree, "rev-parse", "HEAD").stdout.strip(), True


def execute_worker(
    task: dict[str, str],
    model: str,
    worktree: Path,
    branch: str,
    log_path: Path,
    executable: str,
    timeout: int,
) -> dict[str, Any]:
    """Run one OpenCode worker and capture its result."""
    command = [
        executable, "run", "--format", "json", "--model", model,
        "--agent", task["agent"], "--dir", str(worktree), "--auto",
        worker_prompt(task),
    ]
    started = dt.datetime.now(dt.timezone.utc)
    try:
        result = run_command(command, cwd=worktree, timeout=timeout, check=False)
        output = result.stdout
        returncode = result.returncode
        error = None
    except RunnerError as exc:
        output = str(exc) + "\n"
        returncode = 124 if "timed out" in str(exc) else 1
        error = str(exc)
    log_path.write_text(output, encoding="utf-8")
    try:
        commit, changed = commit_worker_changes(worktree, task["id"])
    except RunnerError as exc:
        commit, changed = None, True
        error = f"{error}; {exc}" if error else str(exc)
        returncode = returncode or 1
    finished = dt.datetime.now(dt.timezone.utc)
    return {
        "id": task["id"], "agent": task["agent"], "model": model,
        "branch": branch, "worktree": str(worktree), "log": str(log_path),
        "returncode": returncode, "commit": commit, "changed": changed,
        "error": error,
        "duration_seconds": round((finished - started).total_seconds(), 3),
    }


def command_models(args: argparse.Namespace) -> int:
    """Implement the models subcommand."""
    available = discover_models(args.opencode)
    free = [model for model in available if any(mark in model for mark in FREE_MARKERS)]
    print(json.dumps({"available": available, "free_candidates": free}, indent=2))
    return 0


def command_run(args: argparse.Namespace) -> int:
    """Implement the run subcommand."""
    repo = Path(args.repo).resolve()
    tasks = load_tasks(Path(args.tasks).resolve())
    base = ensure_repository(repo, args.allow_dirty)
    models, model_source = select_models(args.opencode, args.models)
    if not models:
        raise RunnerError("No models selected")
    run_id = normalize_id(
        args.run_id or dt.datetime.now(dt.timezone.utc).strftime("%Y%m%d-%H%M%S")
    )
    output_dir = (
        Path(args.output_dir).resolve()
        if args.output_dir
        else Path(tempfile.gettempdir()) / "opencode-codex" / repo.name / run_id
    )
    output_dir.mkdir(parents=True, exist_ok=True)
    plans: list[dict[str, Any]] = []
    for index, task in enumerate(tasks):
        branch = f"opencode/{run_id}/{task['id']}"
        plans.append(
            {
                "task": task,
                "model": models[index % len(models)],
                "branch": branch,
                "worktree": output_dir / "worktrees" / task["id"],
                "log": output_dir / f"{task['id']}.jsonl",
            }
        )

    if args.dry_run:
        print(json.dumps({
            "run_id": run_id,
            "base_commit": base,
            "model_source": model_source,
            "plans": [
                {
                    "id": plan["task"]["id"], "agent": plan["task"]["agent"],
                    "model": plan["model"], "branch": plan["branch"],
                    "worktree": str(plan["worktree"]),
                }
                for plan in plans
            ],
        }, indent=2))
        return 0

    if shutil.which(args.opencode) is None:
        raise RunnerError(f"OpenCode executable not found: {args.opencode}")
    for plan in plans:
        create_worktree(repo, plan["worktree"], plan["branch"], base)

    max_workers = min(args.max_workers, len(plans))
    results: list[dict[str, Any]] = []
    with concurrent.futures.ThreadPoolExecutor(max_workers=max_workers) as pool:
        futures = [
            pool.submit(
                execute_worker, plan["task"], plan["model"], plan["worktree"],
                plan["branch"], plan["log"], args.opencode, args.timeout,
            )
            for plan in plans
        ]
        for future in concurrent.futures.as_completed(futures):
            results.append(future.result())
    task_order = [task["id"] for task in tasks]
    results.sort(key=lambda item: task_order.index(item["id"]))
    summary = {
        "run_id": run_id, "repo": str(repo), "base_commit": base,
        "model_source": model_source,
        "all_succeeded": all(result["returncode"] == 0 for result in results),
        "results": results,
    }
    summary_path = output_dir / "summary.json"
    summary_path.write_text(json.dumps(summary, indent=2) + "\n", encoding="utf-8")
    for result in results:
        state = "ok" if result["returncode"] == 0 else f"failed({result['returncode']})"
        print(f"{result['id']}: {state} model={result['model']} commit={result['commit'] or '-'}")
    print(f"summary: {summary_path}")
    return 0 if summary["all_succeeded"] else 1


def build_parser() -> argparse.ArgumentParser:
    """Build the command-line parser."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--opencode", default=os.environ.get("OPENCODE_BIN", "opencode"),
        help="OpenCode executable (default: OPENCODE_BIN or opencode)",
    )
    subparsers = parser.add_subparsers(dest="command", required=True)
    models = subparsers.add_parser("models", help="list available and free candidate models")
    models.set_defaults(handler=command_models)
    run = subparsers.add_parser("run", help="run tasks in parallel worktrees")
    run.add_argument("--repo", default=".", help="clean Git repository root")
    run.add_argument("--tasks", required=True, help="JSON task file")
    run.add_argument("--models", help="comma-separated provider/model IDs")
    run.add_argument("--max-workers", type=int, default=4)
    run.add_argument("--timeout", type=int, default=3600, help="seconds per worker")
    run.add_argument("--run-id", help="stable run identifier; defaults to UTC timestamp")
    run.add_argument("--output-dir", help="logs and worktrees directory")
    run.add_argument("--allow-dirty", action="store_true")
    run.add_argument("--dry-run", action="store_true")
    run.set_defaults(handler=command_run)
    return parser


def main() -> int:
    """CLI entry point."""
    parser = build_parser()
    args = parser.parse_args()
    if getattr(args, "max_workers", 1) < 1:
        parser.error("--max-workers must be at least 1")
    if getattr(args, "timeout", 1) < 1:
        parser.error("--timeout must be at least 1")
    try:
        return args.handler(args)
    except RunnerError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
