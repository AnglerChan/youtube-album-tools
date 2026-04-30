#!/usr/bin/env python3
"""Run the local YouTube album splitter for a batch of links."""

from __future__ import annotations

import argparse
import datetime as dt
import json
import os
import shutil
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any


TOOLS_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_DOWNLOADS_ROOT = TOOLS_ROOT / "downloads"
DEFAULT_OUTPUT_ROOT = TOOLS_ROOT / "output"
DEFAULT_PROCESSOR = Path(
    os.environ.get(
        "YOUTUBE_ALBUM_PROCESSOR",
        "/Users/haoxiangliu/.codex/skills/youtube-album-splitter-discogs/scripts/process_youtube_album.py",
    )
)


@dataclass(frozen=True)
class Task:
    index: int
    url: str
    title: str = ""


def load_tasks(path: Path) -> list[Task]:
    if not path.exists():
        raise FileNotFoundError(f"Input list not found: {path}")

    suffix = path.suffix.lower()
    if suffix == ".json":
        return tasks_from_json(json.loads(path.read_text(encoding="utf-8")))
    return tasks_from_text(path.read_text(encoding="utf-8"))


def tasks_from_text(raw: str) -> list[Task]:
    urls: list[str] = []
    for line in raw.splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith("#"):
            continue
        urls.append(stripped)
    return [Task(index=i + 1, url=url) for i, url in enumerate(urls)]


def tasks_from_json(data: Any) -> list[Task]:
    if isinstance(data, dict):
        if isinstance(data.get("urls"), list):
            data = data["urls"]
        elif isinstance(data.get("tasks"), list):
            data = data["tasks"]
        else:
            raise ValueError("JSON must be an array, or an object with urls/tasks array")

    if not isinstance(data, list):
        raise ValueError("JSON input must resolve to a list")

    tasks: list[Task] = []
    for item in data:
        title = ""
        if isinstance(item, str):
            url = item.strip()
        elif isinstance(item, dict):
            url = str(item.get("url") or item.get("link") or "").strip()
            title = str(item.get("title") or item.get("name") or "").strip()
        else:
            raise ValueError(f"Unsupported JSON task item: {item!r}")

        if not url:
            continue
        tasks.append(Task(index=len(tasks) + 1, url=url, title=title))
    return tasks


def make_run_dirs(downloads_root: Path, output_root: Path) -> tuple[str, Path, Path]:
    run_id = dt.datetime.now().strftime("%Y%m%d-%H%M%S")
    download_run_dir = downloads_root / "batch-runs" / run_id
    output_run_dir = output_root / "batch-runs" / run_id
    download_run_dir.mkdir(parents=True, exist_ok=True)
    output_run_dir.mkdir(parents=True, exist_ok=True)
    return run_id, download_run_dir, output_run_dir


def read_manifest(output_dir: Path) -> dict[str, Any]:
    manifest_path = output_dir / "manifest.json"
    if not manifest_path.exists():
        return {}
    return json.loads(manifest_path.read_text(encoding="utf-8"))


def find_output_dir(lines: list[str]) -> Path | None:
    for line in reversed(lines):
        candidate = line.strip()
        if not candidate.startswith("/"):
            continue
        path = Path(candidate)
        if (path / "manifest.json").exists():
            return path
    return None


def relocate_downloaded_audio(manifest: dict[str, Any], output_dir: Path, downloads_root: Path) -> str:
    input_meta = manifest.get("input") or {}
    raw_value = str(input_meta.get("downloaded_audio") or "").strip()
    if not raw_value:
        return ""

    raw_path = Path(raw_value)
    if not raw_path.is_file():
        return ""

    target_dir = downloads_root / "album-sources" / output_dir.name
    target_dir.mkdir(parents=True, exist_ok=True)
    target_path = target_dir / raw_path.name

    if raw_path.resolve() != target_path.resolve():
        if target_path.exists():
            target_path.unlink()
        shutil.move(str(raw_path), str(target_path))
        try:
            raw_path.symlink_to(target_path)
        except OSError:
            pass

    input_meta["downloaded_audio"] = str(target_path.resolve())
    manifest["input"] = input_meta
    (output_dir / "manifest.json").write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")
    return str(target_path.resolve())


def run_one(
    task: Task,
    processor: Path,
    downloads_root: Path,
    output_root: Path,
    log_path: Path,
) -> dict[str, Any]:
    cmd = [
        sys.executable,
        str(processor),
        task.url,
        "--output-root",
        str(output_root),
    ]
    lines: list[str] = []
    env = os.environ.copy()
    env.setdefault("PYTHONDONTWRITEBYTECODE", "1")
    with log_path.open("w", encoding="utf-8") as log:
        log.write(f"$ {' '.join(cmd)}\n\n")
        process = subprocess.Popen(
            cmd,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            bufsize=1,
            env=env,
        )
        assert process.stdout is not None
        for line in process.stdout:
            lines.append(line.rstrip("\n"))
            log.write(line)
        return_code = process.wait()

    output_dir = find_output_dir(lines)
    if return_code != 0 or output_dir is None:
        return {
            "index": task.index,
            "title": task.title,
            "url": task.url,
            "status": "failed",
            "return_code": return_code,
            "log": str(log_path),
            "error": last_error_line(lines) or f"processor exited with code {return_code}",
        }

    manifest = read_manifest(output_dir)
    downloaded_audio = relocate_downloaded_audio(manifest, output_dir, downloads_root)
    release = manifest.get("release") or {}
    input_meta = manifest.get("input") or {}
    return {
        "index": task.index,
        "title": task.title or input_meta.get("title") or "",
        "url": task.url,
        "status": "success",
        "output_dir": str(output_dir),
        "track_count": manifest.get("track_count"),
        "discogs_found": release.get("discogs_found"),
        "release_title": release.get("title") or "",
        "release_artists": release.get("artists") or [],
        "downloaded_audio": downloaded_audio,
        "log": str(log_path),
    }


def last_error_line(lines: list[str]) -> str:
    for line in reversed(lines):
        stripped = line.strip()
        if stripped:
            return stripped
    return ""


def write_json(path: Path, data: Any) -> None:
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")


def write_summary_markdown(path: Path, summary: dict[str, Any]) -> None:
    lines = [
        f"# 专辑分轨批量摘要",
        "",
        f"- 批次: `{summary['run_id']}`",
        f"- 总数: {summary['total']}",
        f"- 成功: {summary['success_count']}",
        f"- 失败: {summary['failed_count']}",
        f"- 重试 TXT: `{summary['retry_txt']}`",
        f"- 重试 JSON: `{summary['retry_json']}`",
        "",
        "## 任务",
        "",
    ]
    for item in summary["tasks"]:
        label = item.get("title") or item["url"]
        if item["status"] == "success":
            lines.append(
                f"- [OK] #{item['index']} {label} | tracks: {item.get('track_count')} | `{item.get('output_dir')}`"
            )
        elif item["status"] == "failed":
            lines.append(f"- [FAIL] #{item['index']} {label} | {item.get('error')} | log: `{item.get('log')}`")
        else:
            lines.append(f"- [PENDING] #{item['index']} {label}")
    lines.append("")
    path.write_text("\n".join(lines), encoding="utf-8")


def write_retry_lists(download_run_dir: Path, failed: list[dict[str, Any]]) -> tuple[Path, Path]:
    retry_txt = download_run_dir / "retry.txt"
    retry_json = download_run_dir / "retry.json"
    retry_txt.write_text("\n".join(item["url"] for item in failed) + ("\n" if failed else ""), encoding="utf-8")
    write_json(
        retry_json,
        [
            {
                "url": item["url"],
                "title": item.get("title") or "",
                "previous_error": item.get("error") or "",
            }
            for item in failed
        ],
    )
    return retry_txt, retry_json


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Batch-run YouTube album splitting links in order.",
    )
    parser.add_argument("input", help="TXT or JSON file containing YouTube links")
    parser.add_argument("--downloads-root", default=str(DEFAULT_DOWNLOADS_ROOT), help="Batch logs/retry root")
    parser.add_argument("--output-root", default=str(DEFAULT_OUTPUT_ROOT), help="Album output root")
    parser.add_argument("--processor", default=str(DEFAULT_PROCESSOR), help="Single-album processor script")
    parser.add_argument("--dry-run", action="store_true", help="Parse input and write a dry-run summary only")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    input_path = Path(args.input).expanduser().resolve()
    downloads_root = Path(args.downloads_root).expanduser().resolve()
    output_root = Path(args.output_root).expanduser().resolve()
    processor = Path(args.processor).expanduser().resolve()

    tasks = load_tasks(input_path)
    if not tasks:
        print("No tasks found in input list.", file=sys.stderr)
        return 2

    if not args.dry_run and not processor.exists():
        print(f"Processor not found: {processor}", file=sys.stderr)
        return 2

    downloads_root.mkdir(parents=True, exist_ok=True)
    output_root.mkdir(parents=True, exist_ok=True)
    run_id, download_run_dir, output_run_dir = make_run_dirs(downloads_root, output_root)
    (download_run_dir / input_path.name).write_text(input_path.read_text(encoding="utf-8"), encoding="utf-8")

    print(f"Batch run: {run_id}")
    print(f"Tasks: {len(tasks)}")
    print(f"Downloads/logs: {download_run_dir}")
    print(f"Output: {output_root}")

    results: list[dict[str, Any]] = []
    if args.dry_run:
        results = [
            {
                "index": task.index,
                "title": task.title,
                "url": task.url,
                "status": "pending",
            }
            for task in tasks
        ]
    else:
        for task in tasks:
            label = f" ({task.title})" if task.title else ""
            print(f"[{task.index}/{len(tasks)}] Start{label}: {task.url}")
            log_path = download_run_dir / f"{task.index:03d}.log"
            result = run_one(task, processor, downloads_root, output_root, log_path)
            results.append(result)
            if result["status"] == "success":
                print(f"[{task.index}/{len(tasks)}] OK: {result.get('output_dir')}")
            else:
                print(f"[{task.index}/{len(tasks)}] FAIL: {result.get('error')}")

    failed = [item for item in results if item["status"] == "failed"]
    retry_txt, retry_json = write_retry_lists(download_run_dir, failed)
    summary = {
        "run_id": run_id,
        "input": str(input_path),
        "downloads_run_dir": str(download_run_dir),
        "output_run_dir": str(output_run_dir),
        "album_output_root": str(output_root),
        "processor": str(processor),
        "dry_run": bool(args.dry_run),
        "total": len(results),
        "success_count": sum(1 for item in results if item["status"] == "success"),
        "failed_count": len(failed),
        "retry_txt": str(retry_txt),
        "retry_json": str(retry_json),
        "tasks": results,
    }

    write_json(output_run_dir / "summary.json", summary)
    write_summary_markdown(output_run_dir / "summary.md", summary)
    write_json(download_run_dir / "summary.json", summary)

    print(f"Summary: {output_run_dir / 'summary.md'}")
    if failed:
        print(f"Retry list: {retry_txt}")
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
