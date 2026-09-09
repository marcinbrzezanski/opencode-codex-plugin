---
name: opencode-orchestrator
description: Delegate independent repository research, implementation, review, or testing tasks from Codex to concurrent OpenCode workers, then review and integrate their Git commits. Use when the user asks to use OpenCode, free models, parallel external workers, higher task throughput, or this plugin.
---

# OpenCode Orchestrator

Use Codex as the parent: decompose the request, dispatch bounded independent work,
inspect every result, integrate selected commits, resolve conflicts, and run the
repository's required verification. OpenCode worker output is evidence and proposed
code, not the final answer.

## Preconditions

1. Locate this plugin's root from the loaded `SKILL.md`; the runner is at
   `<plugin-root>/scripts/opencode_fanout.py`.
2. Run `opencode --version` and `python3 --version` (use `python` if needed).
3. Run the runner's `models` command. If OpenCode authentication is missing, report
   the exact failure. Do not solicit or print credentials.
4. Read the repository's applicable `AGENTS.md` files before writing task prompts.
5. Require a clean main checkout before fan-out. Worker worktrees start at `HEAD`,
   so uncommitted parent changes are not visible to them.

## Choose work that benefits from fan-out

Delegate two or more independent units such as separate modules, alternative bug
hypotheses, independent reviews, or disjoint tests. Keep tightly coupled edits with
the Codex parent. Avoid sending secrets, private data, or unrelated repository
content in prompts.

For uncertain designs, assign the same question to two different models and compare
their answers. For implementation, split by disjoint files or behavior boundaries.

## Dispatch

Write a temporary JSON task file outside the repository:

```json
{
  "tasks": [
    {"id": "focused-id", "prompt": "Concrete deliverable and verification."},
    {"id": "second-id", "prompt": "Another independent deliverable."}
  ]
}
```

Use `"agent": "plan"` for read-only investigation; the default is `build`.
Then run:

```bash
python3 <plugin-root>/scripts/opencode_fanout.py run \
  --repo "$PWD" \
  --tasks /tmp/opencode-tasks.json \
  --max-workers 4
```

Use `--models provider/model-a,provider/model-b` or set
`OPENCODE_FREE_MODELS` to override the discovered/default pool. Do not raise
concurrency after provider rate-limit failures; reduce it and retry failed tasks.

## Review and integrate

The command prints a `summary.json` path. Inspect every result:

1. Read its log and return code.
2. Inspect `git show --stat <commit>` and `git show <commit>`.
3. Reject unrelated, duplicated, unsafe, or unverified changes.
4. Cherry-pick selected commits into the parent checkout one at a time.
5. Resolve overlap deliberately, preserving the user's existing work.
6. Run the repository's relevant tests in the parent checkout.

If a worker produced useful analysis without a commit, synthesize its log. If a
worker failed after editing files, inspect the recorded commit before deciding
whether any part is useful. Never claim success based only on worker exit status.

The runner keeps worktrees so results remain reviewable. Remove them only after
integration with `git worktree remove <recorded-worktree>` and then prune with
`git worktree prune`.

## Reporting

Report which tasks ran, which models handled them, which commits were integrated,
what Codex changed during synthesis, and the final tests. State failed or discarded
worker results briefly.
