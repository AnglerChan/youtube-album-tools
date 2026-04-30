# Metadata Strategy

## Discogs Matching

- Search `type=release` from album clues in title/description (artist guess, album guess, cleaned title, raw title).
- Do not depend on uploader name for release matching (reupload-safe behavior).
- Score candidates by album/artist token overlap, album-like format hints (`Album`, `LP`), and year match when available.
- Pull the top candidate's `resource_url` for full release metadata.

## Split Priority

1. YouTube chapters
2. Description timestamps
3. Discogs track durations (cumulative)
4. Full-length single-track fallback

## Compilation Rule

Mark as compilation if either condition is true:
- Release artist contains `Various`
- Discogs track artists contain multiple unique non-empty artists

## Failure Handling

- If Discogs request fails, continue with available split data.
- Always produce output folder and `manifest.json`.
- Keep failure state explicit in `release.json` (`discogs_found=false`).
