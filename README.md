# move-ical

Small utility script that finds `*.ical.zip` files in a directory, extracts every `.ics` calendar inside each archive into a destination folder (overwriting any existing file with the same name), and deletes the archive afterward so the calendars behave like a **move** out of the inbox.

Requires **Python 3.11+** (stdlib `tomllib`).

## Install

### pipx (recommended)

[pipx](https://pipx.pypa.io/) installs the tool in an isolated environment and puts `move-ical` on your `PATH`. This works well on distributions with an externally managed system Python (for example openSUSE).

Install pipx if needed (openSUSE example):

```bash
sudo zypper install python313-pipx
pipx ensurepath
```

From a clone of this repository:

```bash
cd /path/to/move-ical
pipx install .
```

To pick up local changes after editing the code:

```bash
pipx install -e .
```

Upgrade or reinstall later:

```bash
pipx upgrade move-ical
# or: pipx reinstall move-ical
```

### pip (venv or development)

Use a virtual environment if you prefer `pip` directly:

```bash
cd /path/to/move-ical
python3 -m venv .venv
source .venv/bin/activate
pip install -e .    # editable; good while developing
# or: pip install .
```

After install, run `move-ical` from any directory (see configuration below for `source_dir` / `dest_dir`).

## Usage

```bash
move-ical --source-dir /path/to/inbox --dest-dir /path/to/out
```

Without installing, you can still run the module directly:

```bash
python3 move_ical.py --source-dir /path/to/inbox --dest-dir /path/to/out
```

See what would happen without writing or deleting:

```bash
move-ical --source-dir /path/to/inbox --dest-dir /path/to/out --dry-run
```

Keep the zip files after extraction:

```bash
move-ical --source-dir /path/to/inbox --dest-dir /path/to/out --keep-zip
```

Scan subdirectories recursively for `*.ical.zip`:

```bash
move-ical --source-dir /path/to/inbox --dest-dir /path/to/out --recursive
```

### Configuration file

Copy [`move-ical.example.toml`](move-ical.example.toml) to `move-ical.toml` (or `move-ical.local.toml`) in the current working directory, or pass an explicit path:

```bash
move-ical --config /path/to/move-ical.toml
```

You can also set the config path with the `MOVE_ICAL_CONFIG` environment variable.

**Precedence** (later wins): built-in defaults → TOML file → environment variables → CLI flags.

### Environment variables

| Variable | Meaning |
|----------|---------|
| `MOVE_ICAL_CONFIG` | Path to a TOML config file |
| `MOVE_ICAL_SOURCE_DIR` | Source directory (must exist) |
| `MOVE_ICAL_DEST_DIR` | Destination directory (must exist) |
| `MOVE_ICAL_RECURSIVE` | `true` / `false` (or `1` / `0`, `yes` / `no`) |
| `MOVE_ICAL_DRY_RUN` | Same boolean strings |
| `MOVE_ICAL_KEEP_ZIP` | Same boolean strings |
| `MOVE_ICAL_USE_BASENAME_ONLY` | `false` to preserve relative paths under `dest_dir` (still validated) |

### TOML keys

| Key | Type | Default | Description |
|-----|------|---------|-------------|
| `source_dir` | string | _(required)_ | Directory to scan |
| `dest_dir` | string | _(required)_ | Where `.ics` files are written |
| `recursive` | bool | `false` | Use `rglob` for `*.ical.zip` |
| `dry_run` | bool | `false` | No writes or zip deletes |
| `keep_zip` | bool | `false` | Do not delete `.ical.zip` after extraction |
| `use_basename_only` | bool | `true` | Write `dest_dir / basename(member)` only |

### Behavior notes

- Only archive members whose names end with `.ics` (case-insensitive) are extracted. Directory entries are ignored.
- Entries under `__MACOSX/` are skipped.
- With `use_basename_only = true` (default), every `.ics` is written to `dest_dir` using **only its file name**. If two members share the same basename (e.g. `a/x.ics` and `b/x.ics`), the last one processed wins.
- With `use_basename_only = false`, relative paths inside the zip are recreated under `dest_dir`, but any path containing `..` or resolving outside `dest_dir` is skipped.

## Tests

```bash
cd /path/to/move-ical
python3 -m unittest discover -s tests -p 'test_*.py' -v
```
