# YouTube Album Tools

Split YouTube albums into tagged MP3 tracks with Discogs metadata and cover art.

## Features

- Download YouTube audio (best quality via yt-dlp)
- Split by chapters, description timestamps, or Discogs track durations
- Fetch release metadata and cover art from Discogs
- Export tagged 320kbps MP3 files with embedded cover art
- Batch processing from URL lists
- Output validation and repair

## Scripts

### `SKILL.md`

Codex skill definition for the end-to-end YouTube album splitter workflow.

### `process_youtube_album.py`

Process a single YouTube album URL:

```bash
python3 scripts/process_youtube_album.py "<youtube-url>"
```

Output is written to `/Users/haoxiangliu/albums` by default. Use `--output-root` to change.

### `batch_process_albums.py`

Process multiple albums from a TXT or JSON file:

```bash
python3 scripts/batch_process_albums.py urls.txt
```

### `validate_album_outputs.py`

Validate generated album outputs and optionally repair broken ones:

```bash
python3 scripts/validate_album_outputs.py --output-root ./output
python3 scripts/validate_album_outputs.py --fix ./output/some-album
```

## Requirements

- Python 3.9+
- yt-dlp
- ffmpeg / ffprobe
- Discogs API token

## Discogs Setup

Set the `DISCOGS_TOKEN` environment variable or configure a Discogs API access token before running.

## Included Skill Resources

- `SKILL.md`: agent workflow and output contract
- `scripts/process_youtube_album.py`: single-album downloader, splitter, and Discogs enricher
- `scripts/batch_process_albums.py`: batch runner for URL lists
- `scripts/validate_album_outputs.py`: output validator and repair helper
- `references/metadata-strategy.md`: matching and fallback strategy
- `agents/openai.yaml`: agent interface metadata
