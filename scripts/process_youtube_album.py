#!/usr/bin/env python3
"""Download YouTube album audio, split tracks, and enrich with Discogs metadata."""

from __future__ import annotations

import argparse
import datetime as dt
import json
import os
import re
import shutil
import subprocess
import sys
import time
import urllib.parse
import urllib.request
from pathlib import Path
from typing import Any, Dict, List, Optional


USER_AGENT = "youtube-album-splitter-discogs/1.0 (+https://discogs.com)"
YT_DLP_RETRY_ARGS = [
    "--sleep-requests",
    "1",
    "--retries",
    "15",
    "--fragment-retries",
    "15",
    "--retry-sleep",
    "http:exp=1:20",
]
YT_DLP_COMMON_ARGS = ["--extractor-args", "youtube:player_client=android,web"]


def request_headers(accept_json: bool = False) -> Dict[str, str]:
    headers = {"User-Agent": USER_AGENT}
    if accept_json:
        headers["Accept"] = "application/json"
    token = os.environ.get("DISCOGS_TOKEN", "").strip()
    if token:
        headers["Authorization"] = f"Discogs token={token}"
    return headers


def require_binary(name: str) -> None:
    if shutil.which(name) is None:
        raise RuntimeError(f"Missing required binary: {name}")


def run(cmd: List[str], cwd: Optional[Path] = None, capture: bool = False) -> str:
    if capture:
        result = subprocess.run(
            cmd,
            cwd=str(cwd) if cwd else None,
            check=True,
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
        )
        return result.stdout

    subprocess.run(cmd, cwd=str(cwd) if cwd else None, check=True)
    return ""


def slug(text: str, default: str = "untitled") -> str:
    cleaned = re.sub(r"[\\/:*?\"<>|]", "-", text)
    cleaned = re.sub(r"\s+", " ", cleaned).strip(" .")
    cleaned = cleaned[:120].strip()
    return cleaned or default


def seconds_from_timestamp(ts: str) -> int:
    parts = [int(p) for p in ts.strip().split(":")]
    if len(parts) == 2:
        mm, ss = parts
        return mm * 60 + ss
    if len(parts) == 3:
        hh, mm, ss = parts
        return hh * 3600 + mm * 60 + ss
    raise ValueError(f"Unsupported timestamp: {ts}")


def parse_duration_to_seconds(raw: str) -> Optional[int]:
    if not raw:
        return None
    raw = raw.strip()
    if not raw or raw in {"-", "--", "?"}:
        return None
    if re.fullmatch(r"\d+", raw):
        return int(raw)

    m = re.fullmatch(r"(?:(\d+):)?(\d{1,2}):(\d{2})", raw)
    if not m:
        return None
    h = int(m.group(1) or 0)
    mi = int(m.group(2))
    s = int(m.group(3))
    return h * 3600 + mi * 60 + s


def parse_description_segments(description: str, total_duration: int) -> List[Dict[str, Any]]:
    pattern = re.compile(
        r"^\s*(?P<ts>(?:\d{1,2}:)?\d{1,2}:\d{2})\s*(?:[-|\]\[)>:：.]\s*)?(?P<title>.+?)\s*$"
    )
    starts: List[Dict[str, Any]] = []

    for line in description.splitlines():
        line = line.strip()
        if not line:
            continue
        m = pattern.match(line)
        if not m:
            continue
        ts = m.group("ts")
        title = m.group("title").strip(" -|:：") or "Untitled"
        try:
            start = seconds_from_timestamp(ts)
        except ValueError:
            continue
        starts.append({"start_time": start, "title": title})

    uniq: Dict[int, Dict[str, Any]] = {}
    for item in starts:
        uniq[item["start_time"]] = item

    ordered = [uniq[k] for k in sorted(uniq.keys())]
    if len(ordered) < 2:
        return []

    segments: List[Dict[str, Any]] = []
    for idx, seg in enumerate(ordered):
        start = int(seg["start_time"])
        end = int(ordered[idx + 1]["start_time"]) if idx + 1 < len(ordered) else total_duration
        if end <= start:
            continue
        segments.append(
            {
                "title": seg["title"],
                "start_time": start,
                "end_time": min(end, total_duration),
                "source": "description",
            }
        )
    return segments


def yt_info(url: str) -> Dict[str, Any]:
    last_error: Optional[subprocess.CalledProcessError] = None
    for attempt in range(1, 4):
        try:
            out = run(
                [
                    "yt-dlp",
                    "--dump-single-json",
                    "--no-warnings",
                    "--no-playlist",
                    *YT_DLP_COMMON_ARGS,
                    *YT_DLP_RETRY_ARGS,
                    url,
                ],
                capture=True,
            )
            return json.loads(out)
        except subprocess.CalledProcessError as err:
            last_error = err
            if attempt < 3:
                time.sleep(attempt * 2)
                continue
            raise
    if last_error:
        raise last_error
    raise RuntimeError("Failed to fetch YouTube info")


def yt_download_audio(url: str, output_dir: Path) -> Path:
    template = output_dir / "source.%(ext)s"
    out = run(
        [
            "yt-dlp",
            "--no-playlist",
            *YT_DLP_COMMON_ARGS,
            *YT_DLP_RETRY_ARGS,
            "-f",
            "bestaudio/best",
            "-o",
            str(template),
            "--print",
            "after_move:filepath",
            url,
        ],
        capture=True,
    )
    lines = [line.strip() for line in out.splitlines() if line.strip()]
    if not lines:
        raise RuntimeError("yt-dlp did not return downloaded file path")
    audio_path = Path(lines[-1]).resolve()
    if not audio_path.exists():
        raise RuntimeError(f"Downloaded file not found: {audio_path}")
    return audio_path


def http_get_json(url: str, params: Dict[str, str]) -> Dict[str, Any]:
    qs = urllib.parse.urlencode(params)
    req = urllib.request.Request(
        f"{url}?{qs}",
        headers=request_headers(accept_json=True),
    )
    with urllib.request.urlopen(req, timeout=20) as resp:
        return json.loads(resp.read().decode("utf-8"))


def http_get_json_absolute(url: str) -> Dict[str, Any]:
    req = urllib.request.Request(url, headers=request_headers(accept_json=True))
    with urllib.request.urlopen(req, timeout=20) as resp:
        return json.loads(resp.read().decode("utf-8"))


def download_file(url: str, target: Path) -> Optional[Path]:
    try:
        req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
        with urllib.request.urlopen(req, timeout=20) as resp:
            target.write_bytes(resp.read())
        return target
    except Exception:
        return None


def norm_tokens(text: str) -> List[str]:
    text = re.sub(r"[^a-z0-9 ]+", " ", (text or "").lower())
    return [tok for tok in text.split() if tok]


def clean_album_text(text: str) -> str:
    s = (text or "").strip()
    s = re.sub(r"\[[^\]]*full[^\]]*\]", " ", s, flags=re.IGNORECASE)
    s = re.sub(r"\([^\)]*full[^\)]*\)", " ", s, flags=re.IGNORECASE)
    s = re.sub(r"\[[^\]]*album[^\]]*\]", " ", s, flags=re.IGNORECASE)
    s = re.sub(r"\([^\)]*album[^\)]*\)", " ", s, flags=re.IGNORECASE)
    s = re.sub(r"\b(reupload|re-upload|vinyl rip|cassette rip|tape rip|upload)\b", " ", s, flags=re.IGNORECASE)
    s = re.sub(r"\s+", " ", s).strip(" -_")
    return s


def clean_discogs_artist_name(name: str) -> str:
    """Remove Discogs disambiguation suffixes such as "Avalon (35)"."""
    return re.sub(r"\s+\(\d+\)\s*$", "", (name or "").strip())


def extract_album_hints(info: Dict[str, Any]) -> Dict[str, str]:
    raw_title = (info.get("title") or "").strip()
    description = info.get("description") or ""
    first_lines = [ln.strip() for ln in description.splitlines() if ln.strip()][:8]
    merged = " | ".join([raw_title] + first_lines)

    year_match = re.search(r"\b(19|20)\d{2}\b", merged)
    year = year_match.group(0) if year_match else ""

    cleaned_title = clean_album_text(raw_title)
    artist = ""
    album = cleaned_title

    for sep in [" - ", " – ", " — ", " / "]:
        if sep in cleaned_title:
            left, right = cleaned_title.split(sep, 1)
            left = clean_album_text(left)
            right = clean_album_text(right)
            if left and right:
                artist, album = left, right
                break

    if not artist:
        for ln in first_lines:
            m = re.search(r"^\s*(?:artist|by)\s*[:：]\s*(.+)$", ln, flags=re.IGNORECASE)
            if m:
                artist = clean_album_text(m.group(1))
                break
    artist = clean_discogs_artist_name(artist)

    return {
        "raw_title": raw_title,
        "cleaned_title": cleaned_title,
        "artist_guess": artist,
        "album_guess": album,
        "year_guess": year,
    }


def score_release(candidate: Dict[str, Any], hints: Dict[str, str]) -> float:
    c_title = candidate.get("title", "")
    album_tokens = set(norm_tokens(hints.get("album_guess", "")))
    artist_tokens = set(norm_tokens(hints.get("artist_guess", "")))
    title_tokens = set(norm_tokens(hints.get("cleaned_title", "")))
    cand_tokens = set(norm_tokens(c_title))

    score = 0.0
    if album_tokens and cand_tokens:
        overlap = len(album_tokens & cand_tokens)
        score += 1.4 * (overlap / max(len(album_tokens), 1))

    if artist_tokens and cand_tokens:
        overlap = len(artist_tokens & cand_tokens)
        score += 1.1 * (overlap / max(len(artist_tokens), 1))

    if title_tokens and cand_tokens:
        overlap = len(title_tokens & cand_tokens)
        score += 0.5 * (overlap / max(len(title_tokens), 1))

    fmt = candidate.get("format", "")
    if isinstance(fmt, list):
        fmt_text = " ".join(fmt).lower()
    else:
        fmt_text = str(fmt).lower()
    if "album" in fmt_text or "lp" in fmt_text:
        score += 0.2

    if "various" in c_title.lower():
        score += 0.05

    year = candidate.get("year")
    if isinstance(year, int) and year > 0:
        score += 0.05

    year_guess = hints.get("year_guess")
    if year_guess and str(year) == year_guess:
        score += 0.25

    if "unofficial" in c_title.lower():
        score -= 0.2

    return score


def fetch_discogs_release(info: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    hints = extract_album_hints(info)
    queries: List[str] = []

    artist = hints.get("artist_guess", "").strip()
    album = hints.get("album_guess", "").strip()
    cleaned = hints.get("cleaned_title", "").strip()
    raw = hints.get("raw_title", "").strip()

    if artist and album:
        queries.append(f"{artist} {album}")
    if album:
        queries.append(album)
    if cleaned:
        queries.append(cleaned)
    if raw and raw not in queries:
        queries.append(raw)

    seen = set()
    all_results: List[Dict[str, Any]] = []
    for q in queries[:4]:
        if not q:
            continue
        search = http_get_json(
            "https://api.discogs.com/database/search",
            {"q": q, "type": "release", "per_page": "20", "page": "1"},
        )
        for result in (search.get("results") or []):
            rid = result.get("id")
            if rid in seen:
                continue
            seen.add(rid)
            all_results.append(result)

    if not all_results:
        return None

    ranked = sorted(all_results, key=lambda r: score_release(r, hints), reverse=True)
    candidates = ranked[:5]

    chapters = info.get("chapters") or []
    total_duration = int(float(info.get("duration") or 0))
    desc = info.get("description") or ""
    desc_segs = parse_description_segments(desc, total_duration) if total_duration > 0 else []
    expected_count = len(chapters) if len(chapters) >= 2 else (len(desc_segs) if len(desc_segs) >= 2 else 0)

    best_release: Optional[Dict[str, Any]] = None
    best_score = -10_000.0

    for idx, hit in enumerate(candidates):
        resource_url = hit.get("resource_url")
        if not resource_url:
            continue
        release = http_get_json_absolute(resource_url)
        release["_search_hit"] = hit

        rank_score = score_release(hit, hints) - (idx * 0.05)
        track_entries = release_track_entries(release)
        track_count = len(track_entries)

        if expected_count > 0 and track_count > 0:
            distance = abs(track_count - expected_count) / expected_count
            rank_score += max(0.0, 1.2 - distance * 1.6)
            if track_count < max(2, int(expected_count * 0.6)):
                rank_score -= 0.6

        if rank_score > best_score:
            best_score = rank_score
            best_release = release

    return best_release


def release_track_entries(release: Dict[str, Any]) -> List[Dict[str, Any]]:
    tracks = []
    for entry in release.get("tracklist") or []:
        if entry.get("type_") and entry.get("type_") != "track":
            continue
        dur = parse_duration_to_seconds(entry.get("duration", ""))
        track_artists = ", ".join(
            clean_discogs_artist_name(a.get("name", "")) for a in (entry.get("artists") or []) if a.get("name")
        )
        tracks.append(
            {
                "position": entry.get("position", ""),
                "title": entry.get("title", "Untitled"),
                "duration": entry.get("duration", ""),
                "duration_seconds": dur,
                "artist": track_artists,
            }
        )
    return tracks


def build_segments_from_discogs(track_entries: List[Dict[str, Any]], total_duration: int) -> List[Dict[str, Any]]:
    usable = [t for t in track_entries if t.get("duration_seconds")]
    if len(usable) < 2:
        return []

    sum_dur = sum(int(t["duration_seconds"]) for t in usable)
    if sum_dur <= 0:
        return []

    if total_duration > 0 and sum_dur > int(total_duration * 1.3):
        return []

    segments: List[Dict[str, Any]] = []
    cursor = 0
    for idx, tr in enumerate(usable):
        start = cursor
        cursor += int(tr["duration_seconds"])
        end = cursor if idx + 1 < len(usable) else total_duration
        if end <= start:
            continue
        segments.append(
            {
                "title": tr["title"],
                "start_time": start,
                "end_time": min(end, total_duration),
                "source": "discogs-duration",
            }
        )
    return segments


def detect_compilation(release: Optional[Dict[str, Any]], tracks: List[Dict[str, Any]]) -> bool:
    if not release:
        return False

    release_artists = [clean_discogs_artist_name(a.get("name", "")) for a in (release.get("artists") or []) if a.get("name")]
    if any("various" in a.lower() for a in release_artists):
        return True

    unique_track_artists = {t.get("artist", "").strip() for t in tracks if t.get("artist", "").strip()}
    return len(unique_track_artists) > 1


def tag_value(value: Any) -> str:
    if value is None:
        return ""
    return str(value).strip()


def split_track(
    input_audio: Path,
    output_file: Path,
    start: int,
    end: int,
    tags: Dict[str, str],
    cover_path: Optional[Path] = None,
) -> None:
    cmd = [
        "ffmpeg",
        "-y",
        "-hide_banner",
        "-loglevel",
        "error",
        "-ss",
        str(start),
        "-to",
        str(end),
        "-i",
        str(input_audio),
    ]
    if cover_path and cover_path.exists():
        cmd.extend(["-i", str(cover_path)])

    cmd.extend(
        [
            "-map",
            "0:a:0",
            "-c:a",
            "libmp3lame",
            "-b:a",
            "320k",
            "-id3v2_version",
            "3",
        ]
    )

    if cover_path and cover_path.exists():
        cmd.extend(
            [
                "-map",
                "1:v:0",
                "-c:v:0",
                "mjpeg",
                "-disposition:v:0",
                "attached_pic",
            ]
        )

    for k, v in tags.items():
        if v:
            cmd.extend(["-metadata", f"{k}={v}"])

    cmd.append(str(output_file))
    try:
        run(cmd)
    except subprocess.CalledProcessError:
        # Some sources fail at the exact tail boundary; retry with a 1s shorter end.
        if end - start <= 1:
            raise
        retry_cmd = cmd.copy()
        to_idx = retry_cmd.index("-to") + 1
        retry_cmd[to_idx] = str(end - 1)
        run(retry_cmd)


def prepare_release_metadata(release: Optional[Dict[str, Any]], compilation: bool) -> Dict[str, Any]:
    if not release:
        return {"discogs_found": False, "compilation": compilation}

    artists = [clean_discogs_artist_name(a.get("name", "")) for a in (release.get("artists") or []) if a.get("name")]
    labels = [l.get("name") for l in (release.get("labels") or []) if l.get("name")]
    genres = release.get("genres") or []
    styles = release.get("styles") or []

    return {
        "discogs_found": True,
        "discogs_release_id": release.get("id"),
        "title": release.get("title"),
        "artists": artists,
        "year": release.get("year"),
        "country": release.get("country"),
        "released": release.get("released"),
        "labels": labels,
        "genres": genres,
        "styles": styles,
        "formats": release.get("formats") or [],
        "uri": release.get("uri"),
        "resource_url": release.get("resource_url"),
        "compilation": compilation,
    }


def make_output_dir(root: Path, title: str) -> Path:
    ts = dt.datetime.now().strftime("%Y%m%d-%H%M%S")
    out = root / f"{slug(title)}-{ts}"
    out.mkdir(parents=True, exist_ok=True)
    return out


def choose_segments(
    info: Dict[str, Any],
    discogs_tracks: List[Dict[str, Any]],
    total_duration: int,
) -> List[Dict[str, Any]]:
    chapters = info.get("chapters") or []
    if chapters:
        segs = []
        for c in chapters:
            start = int(float(c.get("start_time", 0)))
            end = int(float(c.get("end_time", total_duration)))
            title = (c.get("title") or "Untitled").strip()
            if end > start:
                segs.append(
                    {
                        "title": title,
                        "start_time": start,
                        "end_time": min(end, total_duration),
                        "source": "chapters",
                    }
                )
        if len(segs) >= 2:
            return segs

    desc = info.get("description") or ""
    desc_segs = parse_description_segments(desc, total_duration)
    if len(desc_segs) >= 2:
        return desc_segs

    by_dur = build_segments_from_discogs(discogs_tracks, total_duration)
    if len(by_dur) >= 2:
        return by_dur

    return [
        {
            "title": info.get("title") or "Track 1",
            "start_time": 0,
            "end_time": total_duration,
            "source": "full-length-fallback",
        }
    ]


def align_track_metadata(
    segments: List[Dict[str, Any]],
    discogs_tracks: List[Dict[str, Any]],
) -> List[Dict[str, Any]]:
    aligned: List[Dict[str, Any]] = []
    for idx, seg in enumerate(segments):
        discogs = discogs_tracks[idx] if idx < len(discogs_tracks) else {}
        title = discogs.get("title") or seg.get("title") or f"Track {idx + 1}"
        artist = discogs.get("artist") or ""
        aligned.append(
            {
                "index": idx + 1,
                "position": discogs.get("position", ""),
                "title": title,
                "artist": artist,
                "duration": discogs.get("duration", ""),
                "start_time": int(seg["start_time"]),
                "end_time": int(seg["end_time"]),
                "source": seg.get("source"),
            }
        )
    return aligned


def main() -> int:
    parser = argparse.ArgumentParser(description="Process YouTube album URL into tagged split tracks")
    parser.add_argument("url", help="YouTube URL")
    parser.add_argument("--output-root", default="/Users/haoxiangliu/albums", help="Base output directory")
    args = parser.parse_args()

    try:
        require_binary("yt-dlp")
        require_binary("ffmpeg")
    except RuntimeError as exc:
        print(str(exc), file=sys.stderr)
        return 2

    out_root = Path(args.output_root).expanduser().resolve()
    out_root.mkdir(parents=True, exist_ok=True)

    info = yt_info(args.url)
    title = info.get("title") or "youtube-album"
    duration = int(float(info.get("duration") or 0))

    if duration <= 0:
        print("Unable to read video duration", file=sys.stderr)
        return 2

    out_dir = make_output_dir(out_root, title)
    raw_dir = out_dir / "raw"
    album_dir = out_dir / "album"
    raw_dir.mkdir(parents=True, exist_ok=True)
    album_dir.mkdir(parents=True, exist_ok=True)

    audio_path = yt_download_audio(args.url, raw_dir)

    release: Optional[Dict[str, Any]] = None
    discogs_tracks: List[Dict[str, Any]] = []
    try:
        release = fetch_discogs_release(info=info)
        if release:
            discogs_tracks = release_track_entries(release)
            images = release.get("images") or []
            if images and images[0].get("uri"):
                download_file(images[0]["uri"], out_dir / "cover.jpg")
    except Exception:
        release = None
        discogs_tracks = []

    cover_file = out_dir / "cover.jpg"
    if not cover_file.exists():
        thumb = info.get("thumbnail")
        if thumb:
            download_file(str(thumb), cover_file)

    compilation = detect_compilation(release, discogs_tracks)
    segments = choose_segments(info, discogs_tracks, duration)
    tracks = align_track_metadata(segments, discogs_tracks)

    release_meta = prepare_release_metadata(release, compilation)
    album_title = release_meta.get("title") or title
    album_artist = "Various Artists" if compilation else ", ".join(release_meta.get("artists") or [])
    if not album_artist:
        hints = extract_album_hints(info)
        album_artist = hints.get("artist_guess") or "Unknown Artist"

    genre = ", ".join(release_meta.get("genres") or [])
    date = tag_value(release_meta.get("year") or "")

    for tr in tracks:
        fname = f"{tr['index']:02d} - {slug(tr['title'], default=f'Track {tr['index']}')}.mp3"
        out_file = album_dir / fname

        artist = tr.get("artist") or album_artist
        tags = {
            "title": tag_value(tr["title"]),
            "artist": tag_value(artist),
            "album": tag_value(album_title),
            "album_artist": tag_value(album_artist),
            "track": f"{tr['index']}/{len(tracks)}",
            "date": date,
            "genre": tag_value(genre),
            "compilation": "1" if compilation else "0",
        }

        split_track(audio_path, out_file, tr["start_time"], tr["end_time"], tags, cover_file)
        tr["file"] = str(out_file.resolve())

    manifest = {
        "input": {
            "url": args.url,
            "title": title,
            "uploader": info.get("uploader") or "",
            "duration_seconds": duration,
            "downloaded_audio": str(audio_path.resolve()),
        },
        "release": release_meta,
        "track_count": len(tracks),
        "tracks": tracks,
    }

    (out_dir / "manifest.json").write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")
    (out_dir / "release.json").write_text(json.dumps(release_meta, ensure_ascii=False, indent=2), encoding="utf-8")

    print(str(out_dir.resolve()))
    return 0


if __name__ == "__main__":
    sys.exit(main())
