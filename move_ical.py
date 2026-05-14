#!/usr/bin/env python3
"""
Extract .ics files from *.ical.zip archives into a destination directory.

Configuration is merged in this order (later wins): defaults, TOML config file,
environment variables, then CLI flags.

Environment variables (optional):
  MOVE_ICAL_SOURCE_DIR   — directory to scan for *.ical.zip
  MOVE_ICAL_DEST_DIR     — directory to write .ics files into
  MOVE_ICAL_RECURSIVE    — "1", "true", "yes" (case-insensitive) to scan recursively
  MOVE_ICAL_DRY_RUN      — same truthy strings for dry-run mode
  MOVE_ICAL_KEEP_ZIP     — same truthy strings to keep the zip after extraction
  MOVE_ICAL_USE_BASENAME_ONLY — "0", "false", "no" to preserve relative paths under dest
"""

from __future__ import annotations

import argparse
import os
import sys
import zipfile
from pathlib import Path
from typing import Any, Iterator

import tomllib


CONFIG_ENV_VAR = "MOVE_ICAL_CONFIG"
DEFAULT_CONFIG_FILENAMES = ("move-ical.toml", "move-ical.local.toml")

# Members under this path prefix are skipped (common macOS metadata junk).
_MACOSX_PREFIX = "__MACOSX/"


def _parse_bool(value: str | None) -> bool | None:
    """Parse a string as bool; return None if value is None or empty."""
    if value is None or value.strip() == "":
        return None
    normalized = value.strip().lower()
    if normalized in {"1", "true", "yes", "on"}:
        return True
    if normalized in {"0", "false", "no", "off"}:
        return False
    raise ValueError(f"Not a boolean string: {value!r}")


def _load_toml_config(path: Path) -> dict[str, Any]:
    """Load a TOML file into a flat dict of supported keys."""
    with path.open("rb") as handle:
        data = tomllib.load(handle)
    if not isinstance(data, dict):
        return {}
    return data


def _find_default_config_path(cwd: Path) -> Path | None:
    """Return the first existing default config filename in cwd."""
    for name in DEFAULT_CONFIG_FILENAMES:
        candidate = cwd / name
        if candidate.is_file():
            return candidate
    return None


def _env_config() -> dict[str, Any]:
    """Build a config overlay from environment variables."""
    out: dict[str, Any] = {}
    if v := os.environ.get("MOVE_ICAL_SOURCE_DIR"):
        out["source_dir"] = v
    if v := os.environ.get("MOVE_ICAL_DEST_DIR"):
        out["dest_dir"] = v
    if (parsed := _parse_bool(os.environ.get("MOVE_ICAL_RECURSIVE"))) is not None:
        out["recursive"] = parsed
    if (parsed := _parse_bool(os.environ.get("MOVE_ICAL_DRY_RUN"))) is not None:
        out["dry_run"] = parsed
    if (parsed := _parse_bool(os.environ.get("MOVE_ICAL_KEEP_ZIP"))) is not None:
        out["keep_zip"] = parsed
    if (parsed := _parse_bool(os.environ.get("MOVE_ICAL_USE_BASENAME_ONLY"))) is not None:
        out["use_basename_only"] = parsed
    return out


def _merge_dict(base: dict[str, Any], overlay: dict[str, Any]) -> dict[str, Any]:
    """Shallow merge: overlay keys replace base."""
    merged = dict(base)
    for key, value in overlay.items():
        if value is not None:
            merged[key] = value
    return merged


def _iter_ical_zip_paths(source_dir: Path, *, recursive: bool) -> Iterator[Path]:
    """Yield paths to files ending with .ical.zip under source_dir."""
    if recursive:
        for path in source_dir.rglob("*"):
            if path.is_file() and path.name.endswith(".ical.zip"):
                yield path
    else:
        for path in source_dir.iterdir():
            if path.is_file() and path.name.endswith(".ical.zip"):
                yield path


def _is_skippable_zip_member(name: str) -> bool:
    """Return True if this archive member should be ignored."""
    normalized = name.replace("\\", "/").lstrip("/")
    if not normalized or normalized.endswith("/"):
        return True
    if normalized.startswith(_MACOSX_PREFIX) or "/__MACOSX/" in f"/{normalized}/":
        return True
    return False


def _safe_basename(filename: str) -> str:
    """Return the basename or raise if it is unsafe."""
    base = Path(filename).name
    if not base or base in {".", ".."}:
        raise ValueError(f"Unsafe or empty zip member basename: {filename!r}")
    return base


def _destination_path_for_member(
    member_name: str,
    dest_dir: Path,
    *,
    use_basename_only: bool,
) -> Path:
    """
    Resolve the output path for a zip member under dest_dir.

    When use_basename_only is True, only the basename is used (zip-slip safe).
    Otherwise, relative POSIX subpaths are preserved if the resolved path stays
    under dest_dir.
    """
    if use_basename_only:
        return dest_dir / _safe_basename(member_name)

    relative_posix = member_name.replace("\\", "/").lstrip("/")
    parts = Path(relative_posix).parts
    if not parts:
        raise ValueError(f"Empty path after normalizing member: {member_name!r}")
    if ".." in parts:
        raise ValueError(f"Zip member contains '..': {member_name!r}")
    candidate = (dest_dir / Path(*parts)).resolve()
    dest_resolved = dest_dir.resolve()
    try:
        candidate.relative_to(dest_resolved)
    except ValueError as exc:
        raise ValueError(f"Zip member escapes destination directory: {member_name!r}") from exc
    return candidate


def _extract_member(
    archive: zipfile.ZipFile,
    info: zipfile.ZipInfo,
    dest_path: Path,
    *,
    dry_run: bool,
) -> None:
    """Write one member to dest_path, creating parent dirs as needed."""
    if dry_run:
        return
    dest_path.parent.mkdir(parents=True, exist_ok=True)
    with archive.open(info, "r") as source, dest_path.open("wb") as target:
        while True:
            chunk = source.read(1024 * 1024)
            if not chunk:
                break
            target.write(chunk)


def process_zip(
    zip_path: Path,
    dest_dir: Path,
    *,
    dry_run: bool,
    keep_zip: bool,
    use_basename_only: bool,
) -> tuple[int, list[str]]:
    """
    Extract every ``.ics`` member from a single ``.ical.zip`` archive.

    Skips directory entries, ``__MACOSX`` metadata paths, and unsafe member
    paths. When at least one calendar is extracted, the source archive is
    removed unless ``keep_zip`` is true or ``dry_run`` is true.

    Args:
        zip_path: Path to the ``*.ical.zip`` file on disk.
        dest_dir: Existing directory where ``.ics`` files are written.
        dry_run: If true, log intended writes and zip deletion without touching
            the filesystem.
        keep_zip: If false (default), delete ``zip_path`` after a successful
            extraction of one or more ``.ics`` files.
        use_basename_only: If true, each member is written using only its
            basename under ``dest_dir``. If false, relative paths are preserved
            when they resolve inside ``dest_dir``.

    Returns:
        A tuple of ``(extracted_count, log_lines)`` where ``extracted_count`` is
        the number of members written (or that would be written in dry-run).
        Archives with no extractable ``.ics`` files are left on disk.
    """
    lines: list[str] = []
    extracted = 0

    with zipfile.ZipFile(zip_path, "r") as archive:
        for info in archive.infolist():
            if info.is_dir():
                continue
            name = info.filename
            if _is_skippable_zip_member(name):
                continue
            if not name.lower().endswith(".ics"):
                continue

            try:
                dest_path = _destination_path_for_member(
                    name,
                    dest_dir,
                    use_basename_only=use_basename_only,
                )
            except ValueError as exc:
                lines.append(f"  SKIP (unsafe path) {name!r}: {exc}")
                continue

            action = "would write" if dry_run else "wrote"
            lines.append(f"  {action} {dest_path}")
            _extract_member(archive, info, dest_path, dry_run=dry_run)
            extracted += 1

    if extracted == 0:
        lines.append("  (no .ics members extracted)")
        return 0, lines

    if not keep_zip and not dry_run:
        zip_path.unlink()
        lines.append(f"  deleted archive {zip_path}")
    elif not keep_zip and dry_run:
        lines.append(f"  would delete archive {zip_path}")
    else:
        lines.append(f"  kept archive {zip_path}")

    return extracted, lines


def build_arg_parser() -> argparse.ArgumentParser:
    """
    Construct the CLI parser for ``move_ical``.

    Boolean flags use ``argparse.BooleanOptionalAction`` so callers may pass
    explicit ``--no-*`` forms; omitted flags defer to configuration files or
    environment variables.
    """
    parser = argparse.ArgumentParser(
        description="Extract .ics files from *.ical.zip archives into a directory.",
    )
    parser.add_argument(
        "--config",
        metavar="PATH",
        help=(
            f"TOML config file (default: read {CONFIG_ENV_VAR} env var, else "
            f"{', '.join(DEFAULT_CONFIG_FILENAMES)} in the current working directory)"
        ),
    )
    parser.add_argument("--source-dir", metavar="PATH", help="Directory containing *.ical.zip files")
    parser.add_argument("--dest-dir", metavar="PATH", help="Directory to write extracted .ics files")
    parser.add_argument(
        "--recursive",
        action=argparse.BooleanOptionalAction,
        default=None,
        help="Scan source directory recursively (default: false)",
    )
    parser.add_argument(
        "--dry-run",
        action=argparse.BooleanOptionalAction,
        default=None,
        help="Print actions without writing files or deleting zips",
    )
    parser.add_argument(
        "--keep-zip",
        action=argparse.BooleanOptionalAction,
        default=None,
        help="Do not delete the zip after successful extraction",
    )
    parser.add_argument(
        "--use-basename-only",
        action=argparse.BooleanOptionalAction,
        default=None,
        help="Write each .ics using only its basename under dest (default: true)",
    )
    return parser


def resolve_settings(args: argparse.Namespace, cwd: Path) -> dict[str, Any]:
    """
    Merge settings from defaults, TOML, environment variables, and CLI.

    Resolution order (each layer overrides the previous): built-in defaults,
    TOML file (from ``--config``, ``MOVE_ICAL_CONFIG``, or default filenames in
    ``cwd``), process environment, then CLI arguments.

    Args:
        args: Parsed namespace from :func:`build_arg_parser`.
        cwd: Directory used when searching for default config filenames.

    Returns:
        A dictionary containing at least ``source_dir``, ``dest_dir``,
        ``recursive``, ``dry_run``, ``keep_zip``, and ``use_basename_only`` as
        :class:`pathlib.Path` or ``bool`` values ready for :func:`main`.

    Raises:
        ValueError: If a configured file path is missing, or ``source_dir`` /
            ``dest_dir`` are absent after merging all sources.
    """
    defaults: dict[str, Any] = {
        "recursive": False,
        "dry_run": False,
        "keep_zip": False,
        "use_basename_only": True,
    }

    config_path_str = args.config or os.environ.get(CONFIG_ENV_VAR)
    if config_path_str:
        config_path = Path(config_path_str).expanduser()
    else:
        config_path = _find_default_config_path(cwd)

    file_data: dict[str, Any] = {}
    if config_path is not None:
        if not config_path.is_file():
            raise ValueError(f"Config file not found: {config_path}")
        file_data = _load_toml_config(config_path)

    merged = _merge_dict(defaults, file_data)
    merged = _merge_dict(merged, _env_config())

    cli_overlay: dict[str, Any] = {}
    if args.source_dir is not None:
        cli_overlay["source_dir"] = args.source_dir
    if args.dest_dir is not None:
        cli_overlay["dest_dir"] = args.dest_dir
    if args.recursive is not None:
        cli_overlay["recursive"] = args.recursive
    if args.dry_run is not None:
        cli_overlay["dry_run"] = args.dry_run
    if args.keep_zip is not None:
        cli_overlay["keep_zip"] = args.keep_zip
    if args.use_basename_only is not None:
        cli_overlay["use_basename_only"] = args.use_basename_only

    merged = _merge_dict(merged, cli_overlay)

    source = merged.get("source_dir")
    dest = merged.get("dest_dir")
    if not source or not dest:
        raise ValueError(
            "source_dir and dest_dir must be set (config file, environment, or --source-dir / --dest-dir).",
        )

    merged["source_dir"] = Path(str(source)).expanduser()
    merged["dest_dir"] = Path(str(dest)).expanduser()
    return merged


def main(argv: list[str] | None = None) -> int:
    """
    CLI entry point: scan ``source_dir`` for ``*.ical.zip`` files and delegate
    each archive to :func:`process_zip`.

    Args:
        argv: Optional argument vector (defaults to ``sys.argv[1:]`` via
            :meth:`argparse.ArgumentParser.parse_args`).

    Returns:
        Process exit code: ``0`` on success, ``1`` on user or filesystem errors.
    """
    parser = build_arg_parser()
    args = parser.parse_args(argv)
    cwd = Path.cwd()

    try:
        settings = resolve_settings(args, cwd)
    except ValueError as exc:
        print(str(exc), file=sys.stderr)
        return 1

    source_dir: Path = settings["source_dir"]
    dest_dir: Path = settings["dest_dir"]
    recursive: bool = bool(settings["recursive"])
    dry_run: bool = bool(settings["dry_run"])
    keep_zip: bool = bool(settings["keep_zip"])
    use_basename_only: bool = bool(settings["use_basename_only"])

    if not source_dir.is_dir():
        print(f"Source directory does not exist or is not a directory: {source_dir}", file=sys.stderr)
        return 1
    if not dest_dir.is_dir():
        print(f"Destination directory does not exist or is not a directory: {dest_dir}", file=sys.stderr)
        return 1

    zips = sorted(_iter_ical_zip_paths(source_dir, recursive=recursive), key=lambda p: str(p))
    if not zips:
        mode = "recursively " if recursive else ""
        print(f"No *.ical.zip files found {mode}under {source_dir}")
        return 0

    print(f"Scanning: {source_dir} ({'recursive' if recursive else 'non-recursive'})")
    print(f"Destination: {dest_dir}")
    if dry_run:
        print("Dry run: no files will be written or deleted.")

    total_extracted = 0
    for zip_path in zips:
        print(f"Processing: {zip_path}")
        count, lines = process_zip(
            zip_path,
            dest_dir,
            dry_run=dry_run,
            keep_zip=keep_zip,
            use_basename_only=use_basename_only,
        )
        for line in lines:
            print(line)
        total_extracted += count

    print(f"Done. Extracted {total_extracted} .ics file(s) from {len(zips)} archive(s).")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
