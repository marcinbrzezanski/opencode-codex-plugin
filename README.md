# OpenCode Codex Plugin

A Codex plugin that lets a Codex cloud task delegate independent work to multiple
[OpenCode](https://opencode.ai/) workers in parallel. Each worker gets its own Git
worktree and branch, so Codex can review the result and cherry-pick only the useful
commits into the cloud task's checkout.

The repository is also a Codex marketplace. Add it with:

```bash
codex plugin marketplace add marcinbrzezanski/opencode-codex-plugin
```

Then install `opencode-codex-plugin` from the `opencode-codex` marketplace in the
Plugins Directory and start a new Codex chat.

## What it provides

- `opencode-orchestrator`, a Codex skill for decomposing and delegating work.
- `opencode_fanout.py`, a dependency-free Python runner that creates isolated Git
  worktrees, starts concurrent `opencode run` processes, captures JSON event logs,
  and commits each worker's changes.
- Cloud setup and maintenance scripts for installing OpenCode and the MiniPLM
  Python dependencies.
- Runtime discovery of the currently installed OpenCode model catalog, with a
  configurable free-model pool.

## Quick local test

OpenCode and Python 3.11+ must be on `PATH`.

```bash
python plugins/opencode-codex-plugin/scripts/opencode_fanout.py models
python plugins/opencode-codex-plugin/scripts/opencode_fanout.py run \
  --repo /path/to/repo \
  --tasks examples/tasks.example.json \
  --dry-run
```

To execute the tasks, remove `--dry-run`. The runner prints the path to a
`summary.json`. Review each worker with `git show <commit>` and integrate selected
commits with `git cherry-pick <commit>` from the main checkout.

## Codex cloud

Copy [`examples/codex-cloud-setup.sh`](examples/codex-cloud-setup.sh) into the
environment's setup-script field and
[`examples/codex-cloud-maintenance.sh`](examples/codex-cloud-maintenance.sh) into
the maintenance-script field. The setup script expects to run from the MiniPLM
checkout. It installs the project dependencies, OpenCode, and a runtime copy of
this plugin.

Set `OPENCODE_API_KEY` as a Codex cloud **secret**. The setup script writes it to
OpenCode's credential store with mode `0600`, because Codex cloud secrets are not
available during the later agent phase. Any credential used by a delegated agent
is necessarily accessible to that agent's process.

Enable agent internet access for at least:

- `opencode.ai`
- `github.com`
- `registry.npmjs.org`
- `models.dev`

MiniPLM work that downloads datasets or models also needs the relevant Hugging
Face domains, commonly `huggingface.co` and `cdn-lfs.huggingface.co`.

The current default free pool is defined in the runner. Override it without code
changes by setting a comma-separated `OPENCODE_FREE_MODELS`, using full IDs such
as `opencode/mimo-v2.5-free`.

## Task file

```json
{
  "tasks": [
    {
      "id": "attention-tests",
      "prompt": "Add focused tests for attention mask edge cases and run them."
    },
    {
      "id": "docs-audit",
      "prompt": "Audit the training documentation for commands that no longer exist.",
      "agent": "plan"
    }
  ]
}
```

`build` is the default OpenCode agent. Use `plan` for read-only analysis. Tasks
should be independent; overlapping edits still produce valid commits, but Codex
may need to resolve cherry-pick conflicts.

## Development

```bash
python -m unittest discover -s tests -v
python /path/to/plugin-creator/scripts/validate_plugin.py \
  plugins/opencode-codex-plugin
```

## License

MIT
