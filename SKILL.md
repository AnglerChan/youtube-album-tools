---
name: youtube-album-splitter-discogs
description: Download a YouTube album or mix in best available audio quality, split it into tracks from chapters/description timestamps (or Discogs track durations when timestamps are missing), and enrich per-track metadata from Discogs (cover, artist, release, track position, year, label, genre). Use when a user provides a YouTube URL and asks for album ripping, track splitting, metadata completion, or when the video is a reupload and Discogs matching must rely on album info instead of uploader info.
---

# YouTube Album Splitter Discogs

## Overview

Turn one YouTube album link into a tagged, split track folder with Discogs metadata and cover art.
Prefer deterministic script execution over manual ad-hoc commands.

## Workflow

1. Run dependency check.
2. Run the processing script with the YouTube URL.
3. After success, read `manifest.json` and `release.json` from the output folder.
4. In assistant reply, include:
   - absolute output folder path
   - track list (track no. + title + output filename)
   - key release metadata (artist, release title, year, label, genre/style, format)
   - Discogs release link (or a clear fallback note if unavailable)

```bash
python3 scripts/process_youtube_album.py "<youtube-url>"
```

Default output root: `/Users/haoxiangliu/albums`.

Optional output location:

```bash
python3 scripts/process_youtube_album.py "<youtube-url>" --output-root /absolute/path
```

## Behavior Rules

- Download with `yt-dlp` best-audio selector (`bestaudio/best`).
- Export split tracks as `mp3` (`320k`).
- For reuploaded videos, match Discogs release by album information extracted from title/description, not by uploader.
- Split order priority:
1. YouTube chapters
2. Description timestamps
3. Discogs track durations (cumulative)
- Fetch Discogs release metadata and cover image.
- Strip Discogs artist disambiguation suffixes such as `(35)` from artist names before writing metadata or tags.
- Write release metadata to `release.json` and per-track metadata to `manifest.json`.
- Export one audio file per track in `album/` as `.mp3` with embedded tags and embedded cover art.
- Do not write comment annotations into mp3 tags.
- Mark compilation when Discogs release artist is `Various` or track artists are mixed.
- If Discogs lookup fails, keep splitting output with local metadata and record fallback status in `manifest.json`.

## Output Contract

- The script prints the absolute output folder path on success.
- In normal assistant replies, always include:
  - absolute output folder path
  - track list
  - metadata summary
  - Discogs release link
- If Discogs lookup fails, still include path + track list and explicitly mark metadata/link as unavailable.

## Resources

### scripts/

- `scripts/process_youtube_album.py`: end-to-end downloader/splitter/Discogs enricher.

### references/

- `references/metadata-strategy.md`: matching and fallback strategy details.
