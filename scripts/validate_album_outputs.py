#!/usr/bin/env python3
"""Validate and optionally repair generated album output folders."""

from __future__ import annotations

import argparse
import json
import shutil
import subprocess
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any


TOOLS_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_PROCESSOR = TOOLS_ROOT / "scripts" / "process_youtube_album.py"
DEFAULT_OUTPUT_ROOT = TOOLS_ROOT / "output"
AUDIO_EXTS = {".mp3", ".flac", ".m4a", ".aac", ".wav", ".ogg", ".opus"}
GOOD_OUTPUT_EXTS = {".mp3"}
REQUIRED_TAGS = ("title", "artist", "album", "track")
COVER_NAMES = ("cover.jpg", "cover.jpeg", "cover.png", "folder.jpg", "folder.png")


@dataclass
class FixAction:
    kind: str
    command: str = ""
    reason: str = ""


@dataclass
class AlbumReport:
    path: Path
    status: str = "ok"
    issues: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    actions: list[FixAction] = field(default_factory=list)
    repaired: list[str] = field(default_factory=list)
    rerun_output: str = ""

    def add_issue(self, text: str) -> None:
        if text not in self.issues:
            self.issues.append(text)
        self.status = "broken"

    def add_warning(self, text: str) -> None:
        if text not in self.warnings:
            self.warnings.append(text)
        if self.status == "ok":
            self.status = "warning"

    def add_action(self, kind: str, reason: str, command: str = "") -> None:
        if not any(a.kind == kind and a.reason == reason and a.command == command for a in self.actions):
            self.actions.append(FixAction(kind=kind, reason=reason, command=command))


def read_json(path: Path) -> tuple[dict[str, Any] | None, str | None]:
    try:
        return json.loads(path.read_text(encoding="utf-8")), None
    except FileNotFoundError:
        return None, "missing"
    except json.JSONDecodeError as exc:
        return None, f"invalid json: {exc}"


def write_json(path: Path, data: dict[str, Any]) -> None:
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def looks_like_album_dir(path: Path) -> bool:
    markers = ("manifest.json", "release.json", "album", "tracks", "raw")
    if any((path / marker).exists() for marker in markers):
        return True
    if cover_files_under(path):
        return True
    return bool(audio_files_under(path))


def album_dirs(output_root: Path, explicit: list[str]) -> list[Path]:
    if explicit:
        return [Path(item).expanduser().resolve() for item in explicit]
    if not output_root.exists():
        return []
    return sorted(p.resolve() for p in output_root.iterdir() if p.is_dir() and looks_like_album_dir(p))


def audio_files_under(album_dir: Path) -> list[Path]:
    files: list[Path] = []
    for base in (album_dir / "album", album_dir / "tracks", album_dir):
        if not base.exists():
            continue
        files.extend(p.resolve() for p in base.rglob("*") if p.is_file() and p.suffix.lower() in AUDIO_EXTS)
    return sorted(set(files))


def cover_files_under(album_dir: Path) -> list[Path]:
    covers = [album_dir / name for name in COVER_NAMES]
    covers.extend((album_dir / "album" / name) for name in COVER_NAMES)
    return [p.resolve() for p in covers if p.exists() and p.is_file()]


def as_path(value: Any, album_dir: Path) -> Path | None:
    if not isinstance(value, str) or not value.strip():
        return None
    p = Path(value).expanduser()
    if not p.is_absolute():
        p = album_dir / p
    return p.resolve()


def manifest_tracks(manifest: dict[str, Any] | None) -> list[dict[str, Any]]:
    tracks = manifest.get("tracks") if manifest else None
    return tracks if isinstance(tracks, list) else []


def discogs_found(manifest: dict[str, Any] | None, release: dict[str, Any] | None) -> bool:
    candidates = []
    if isinstance(release, dict):
        candidates.append(release)
    if isinstance(manifest, dict) and isinstance(manifest.get("release"), dict):
        candidates.append(manifest["release"])
    return any(bool(item.get("discogs_found") and item.get("discogs_release_id")) for item in candidates)


def track_file_candidates(album_dir: Path, index: int, title: str = "") -> list[Path]:
    prefixes = {f"{index:02d}", str(index)}
    candidates: list[Path] = []
    for item in audio_files_under(album_dir):
        stem = item.stem.strip()
        first = stem.split(" ", 1)[0].strip(".-_")
        if first in prefixes:
            candidates.append(item)
            continue
        if title and title.lower() in stem.lower():
            candidates.append(item)
    return sorted(candidates, key=lambda p: (p.suffix.lower() not in GOOD_OUTPUT_EXTS, len(str(p))))


def repair_manifest_paths(album_dir: Path, manifest: dict[str, Any]) -> list[str]:
    repairs: list[str] = []
    changed = False
    for track in manifest_tracks(manifest):
        if not isinstance(track, dict):
            continue
        index = int(track.get("index") or 0)
        title = str(track.get("title") or "")
        current = as_path(track.get("file"), album_dir)
        current_exists = bool(current and current.exists())
        needs_mp3_swap = bool(current and current.suffix.lower() == ".flac")
        if current_exists and not needs_mp3_swap:
            continue

        candidates = track_file_candidates(album_dir, index, title)
        chosen = next((p for p in candidates if p.suffix.lower() in GOOD_OUTPUT_EXTS), None)
        if not chosen and not current_exists:
            chosen = candidates[0] if candidates else None
        if not chosen:
            continue

        old = str(track.get("file") or "")
        track["file"] = str(chosen)
        repairs.append(f"track {index}: {old or '<empty>'} -> {chosen}")
        changed = True

    if changed:
        write_json(album_dir / "manifest.json", manifest)
    return repairs


def ffprobe_tags(path: Path) -> tuple[dict[str, str], bool, str | None]:
    try:
        result = subprocess.run(
            [
                "ffprobe",
                "-v",
                "quiet",
                "-print_format",
                "json",
                "-show_format",
                "-show_streams",
                str(path),
            ],
            check=True,
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
        )
    except FileNotFoundError:
        return {}, False, "ffprobe not found"
    except subprocess.CalledProcessError as exc:
        detail = (exc.stderr or "").strip()
        return {}, False, detail or "ffprobe failed"

    try:
        payload = json.loads(result.stdout)
    except json.JSONDecodeError as exc:
        return {}, False, f"invalid ffprobe output: {exc}"

    tags: dict[str, str] = {}
    format_tags = (payload.get("format") or {}).get("tags") or {}
    if isinstance(format_tags, dict):
        tags.update({str(k).lower(): str(v).strip() for k, v in format_tags.items()})

    has_cover = False
    for stream in payload.get("streams") or []:
        if not isinstance(stream, dict):
            continue
        if stream.get("disposition", {}).get("attached_pic") == 1:
            has_cover = True
        stream_tags = stream.get("tags") or {}
        if isinstance(stream_tags, dict):
            for key, value in stream_tags.items():
                tags.setdefault(str(key).lower(), str(value).strip())
    return tags, has_cover, None


def missing_tags_for(path: Path) -> tuple[list[str], bool, str | None]:
    tags, has_cover, error = ffprobe_tags(path)
    if error:
        return [], False, error
    missing = [tag for tag in REQUIRED_TAGS if not tags.get(tag)]
    return missing, has_cover, None


def rerun_command(processor: Path, url: str, output_root: Path) -> list[str]:
    return [sys.executable, str(processor), url, "--output-root", str(output_root)]


def validator_fix_command(album_dir: Path, output_root: Path) -> list[str]:
    return [
        sys.executable,
        str(Path(__file__).resolve()),
        "--output-root",
        str(output_root),
        "--fix",
        str(album_dir),
    ]


def shell_join(parts: list[str]) -> str:
    return " ".join(sh_quote(part) for part in parts)


def sh_quote(value: str) -> str:
    if not value:
        return "''"
    safe = "abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789_+-=.,/:@%"
    if all(ch in safe for ch in value):
        return value
    return "'" + value.replace("'", "'\"'\"'") + "'"


def validate_album(
    album_dir: Path,
    output_root: Path,
    processor: Path,
    fix: bool,
    check_tags: bool,
) -> AlbumReport:
    report = AlbumReport(path=album_dir)
    manifest_path = album_dir / "manifest.json"
    release_path = album_dir / "release.json"
    manifest, manifest_error = read_json(manifest_path)
    release, release_error = read_json(release_path)

    if manifest_error:
        report.add_issue(f"manifest.json {manifest_error}")
    if release_error:
        report.add_issue(f"release.json {release_error}")

    if fix and manifest:
        repairs = repair_manifest_paths(album_dir, manifest)
        report.repaired.extend(repairs)
        if repairs:
            manifest, _ = read_json(manifest_path)

    tracks = manifest_tracks(manifest)
    if manifest and not tracks:
        report.add_issue("manifest has no tracks")

    declared_count = manifest.get("track_count") if manifest else None
    if isinstance(declared_count, int) and tracks and declared_count != len(tracks):
        report.add_issue(f"track_count is {declared_count}, but manifest has {len(tracks)} tracks")

    covers = cover_files_under(album_dir)
    if not covers:
        report.add_issue("cover image is missing")

    scanned_audio = audio_files_under(album_dir)
    if not scanned_audio:
        report.add_issue("no audio files found")

    flac_files = [p for p in scanned_audio if p.suffix.lower() == ".flac"]
    if flac_files:
        report.add_issue(f"{len(flac_files)} flac file(s) still present")

    if manifest:
        for idx, track in enumerate(tracks, start=1):
            if not isinstance(track, dict):
                report.add_issue(f"track #{idx} is not an object")
                continue
            track_path = as_path(track.get("file"), album_dir)
            if not track_path:
                report.add_issue(f"track #{idx} has no file path")
                continue
            if track_path.suffix.lower() not in GOOD_OUTPUT_EXTS:
                report.add_issue(f"track #{idx} is {track_path.suffix.lower() or 'extensionless'}, expected .mp3")
            if not track_path.exists():
                report.add_issue(f"track #{idx} file missing: {track_path}")
                continue

    if not discogs_found(manifest, release):
        report.add_issue("Discogs metadata is missing")

    files_to_check: list[Path] = []
    if manifest:
        for track in tracks:
            if isinstance(track, dict):
                p = as_path(track.get("file"), album_dir)
                if p and p.exists():
                    files_to_check.append(p)
    else:
        files_to_check = scanned_audio

    if check_tags and files_to_check:
        for path in sorted(set(files_to_check)):
            missing, has_embedded_cover, error = missing_tags_for(path)
            if error:
                report.add_warning(f"could not inspect tags for {path.name}: {error}")
                continue
            if missing:
                report.add_issue(f"{path.name} missing tag(s): {', '.join(missing)}")
            if covers and path.suffix.lower() == ".mp3" and not has_embedded_cover:
                report.add_issue(f"{path.name} has no embedded cover art")

    url = ""
    if manifest and isinstance(manifest.get("input"), dict):
        url = str(manifest["input"].get("url") or "")

    needs_rerun = any(
        token in issue
        for issue in report.issues
        for token in (
            "flac",
            "Discogs metadata",
            "file missing",
            "no audio files",
            "cover image",
            "missing tag",
            "embedded cover",
            "manifest has no tracks",
        )
    )
    if needs_rerun:
        if url:
            report.add_action(
                "fix",
                "one-click repair/rerun through this validator",
                shell_join(validator_fix_command(album_dir, output_root)),
            )
            command = rerun_command(processor, url, output_root)
            report.add_action("rerun", "rebuild album from manifest.input.url", shell_join(command))
        else:
            report.add_action("manual", "no manifest.input.url available; rerun needs the original YouTube URL")

    has_validator_fix = any(action.kind == "fix" for action in report.actions)
    if any("file missing" in issue for issue in report.issues) and not fix and not has_validator_fix:
        report.add_action("local", "try --fix to rewrite manifest track paths when matching audio exists")

    if fix and needs_rerun and url:
        if not processor.exists():
            report.add_issue(f"processor not found: {processor}")
        else:
            command = rerun_command(processor, url, output_root)
            try:
                result = subprocess.run(command, check=True, text=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
                lines = [line.strip() for line in result.stdout.splitlines() if line.strip()]
                report.rerun_output = lines[-1] if lines else ""
                report.repaired.append(f"reran processor: {report.rerun_output or shell_join(command)}")
            except subprocess.CalledProcessError as exc:
                detail = (exc.stderr or exc.stdout or "").strip()
                report.add_issue(f"rerun failed: {detail or exc}")

    if not report.issues and report.status == "ok":
        report.status = "ok"
    return report


def report_to_json(report: AlbumReport) -> dict[str, Any]:
    return {
        "path": str(report.path),
        "status": report.status,
        "issues": report.issues,
        "warnings": report.warnings,
        "actions": [action.__dict__ for action in report.actions],
        "repaired": report.repaired,
        "rerun_output": report.rerun_output,
    }


def print_human(reports: list[AlbumReport], only_broken: bool) -> None:
    shown = [r for r in reports if not only_broken or r.status != "ok"]
    if not shown:
        print("All album outputs look OK.")
        return

    for report in shown:
        marker = {"ok": "OK", "warning": "WARN", "broken": "BROKEN"}.get(report.status, report.status.upper())
        print(f"[{marker}] {report.path}")
        for item in report.issues:
            print(f"  - issue: {item}")
        for item in report.warnings:
            print(f"  - warning: {item}")
        for item in report.repaired:
            print(f"  - repaired: {item}")
        for action in report.actions:
            if action.command:
                print(f"  - action: {action.reason}")
                print(f"    {action.command}")
            else:
                print(f"  - action: {action.reason}")
        print()


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Scan album output folders and report missing manifests, cover art, audio files, tags, and Discogs metadata."
    )
    parser.add_argument("albums", nargs="*", help="Specific album directories. Defaults to every child of --output-root.")
    parser.add_argument("--output-root", default=str(DEFAULT_OUTPUT_ROOT), help="Album output root to scan and rerun into.")
    parser.add_argument("--processor", default=str(DEFAULT_PROCESSOR), help="process_youtube_album.py path for reruns.")
    parser.add_argument("--fix", action="store_true", help="Repair manifest paths when possible and rerun broken albums with URLs.")
    parser.add_argument("--no-tags", action="store_true", help="Skip ffprobe tag checks.")
    parser.add_argument("--only-broken", action="store_true", help="Hide OK albums in human output.")
    parser.add_argument("--json", action="store_true", help="Emit JSON instead of human output.")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    output_root = Path(args.output_root).expanduser().resolve()
    processor = Path(args.processor).expanduser().resolve()
    dirs = album_dirs(output_root, args.albums)

    if not dirs:
        print(f"No album directories found under {output_root}", file=sys.stderr)
        return 2

    if not args.no_tags and shutil.which("ffprobe") is None:
        print("warning: ffprobe not found; tag checks will be reported as warnings", file=sys.stderr)

    reports = [
        validate_album(
            album_dir=album,
            output_root=output_root,
            processor=processor,
            fix=args.fix,
            check_tags=not args.no_tags,
        )
        for album in dirs
    ]

    if args.json:
        print(json.dumps([report_to_json(report) for report in reports], ensure_ascii=False, indent=2))
    else:
        print_human(reports, args.only_broken)

    return 1 if any(report.status == "broken" for report in reports) else 0


if __name__ == "__main__":
    sys.exit(main())
