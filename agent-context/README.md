# agent-context

A reusable composite GitHub Action that generates two small, agent-friendly
files for the calling repository:

- `REPO_MAP.md` - a directory-grouped list of the tracked files, one line per
  file, with a short description.
- `CODE_INDEX.md` - a per-file index of code structure (signatures, function
  names, service names, job ids, and so on) without any file bodies.

These files let agents load a small index instead of whole codebases. Both
files are written with a hard size limit and the action fails the step if a
file is too large.

The action only generates files. It does not commit or push.

## Inputs

| Input | Required | Default | Description |
| --- | --- | --- | --- |
| `max-bytes` | no | `"20480"` | Maximum allowed size in bytes for each generated file. The action exits non-zero if a file is larger. |
| `output-dir` | no | `"."` | Directory (relative to the workspace) where `REPO_MAP.md` and `CODE_INDEX.md` are written. |

## Usage

Pin the action to a tag or a commit SHA. From a caller repository in the same
organisation:

```yaml
name: Agent context

on:
  workflow_dispatch:
  schedule:
    - cron: "0 6 * * *"

permissions:
  contents: read

jobs:
  context:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4
      - uses: jimbobsyouruncle/shared-actions/agent-context@v1
        with:
          max-bytes: "20480"
          output-dir: "."
```

After the step runs, `REPO_MAP.md` and `CODE_INDEX.md` are in the workspace.
A later step can upload them as artifacts or open a pull request. The action
itself stays read-only and never commits.

## What gets listed

The file list comes from `git ls-files` only. Only tracked files are
considered, and the list is sorted, so the output is deterministic.

### Excluded entirely (neither listed nor read)

- `CONSOLIDATED_*.md`, `REPO_MAP.md`, `CODE_INDEX.md`
- `.env` and `.env.*`, except `.env.example`
- `*.pem`, `*.key`, `id_*`, `*.log`
- Symbolic links: any tracked path that is a symlink, or whose real path
  resolves outside the repository root. This check runs before any file is
  opened, so a symlink can never leak content from outside the repository.
- Binary files, detected by a NUL byte in the first 8 KB

### Descriptions in `REPO_MAP.md`

Each included file gets at most one line. The description comes from the file
type:

| File type | Description source |
| --- | --- |
| `.md` | first `# ` heading |
| `.py` | first line of the module docstring |
| `.sh` | first comment line after any shebang |
| `.yml` / `.yaml` | `name:` value, or the first comment |
| anything else | empty |

Descriptions are a single line, at most 100 characters, with backticks
removed. Paths under `.github/` are listed as paths only, with no description
(their job ids still appear in `CODE_INDEX.md`).

Files are grouped by directory. Each directory heading appears exactly once,
and every file sits under its own directory's heading. Root-level files are
listed first, under the `## .` heading, before any directory heading.

### Entries in `CODE_INDEX.md`

| File type | Index content |
| --- | --- |
| `.py` | top-level and class-level `def`/`class` signatures via `ast`, parameter names only, no default values |
| `.sh` | function names |
| Compose files | service names |
| Workflow files | job ids |
| Other YAML | top-level keys |
| `Dockerfile*` | `FROM` lines and stage names |

A file that fails to parse is listed with `(unparsed)` and does not stop the
run. YAML is parsed without third-party libraries by a minimal
indentation-based key scan.

## Determinism

The output contains no timestamps and no commit SHAs. The same input tree
produces byte-identical output, so you can verify it with `sha256sum`.

## Size limit

If either generated file exceeds `max-bytes`, the action exits non-zero and
prints a message that names the file and its size. It never truncates
silently. To fix an overflow, either reduce the file list or raise
`max-bytes`. Raise the default only when you can justify it.

## Development

Run the tests from the repository root:

```sh
python3 -m unittest discover agent-context/tests
```

Run the generator directly against any repository:

```sh
python3 agent-context/generate.py \
  --root /path/to/repo \
  --output-dir /tmp/agent-context \
  --max-bytes 20480
```

## Requirements

Python 3 standard library only. No third-party packages.
