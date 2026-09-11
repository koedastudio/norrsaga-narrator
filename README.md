# norrsaga-narrator

Just-in-time text-to-speech for [Audiobookshelf](https://www.audiobookshelf.org/)
ebooks. A small sidecar that turns any epub in your library into an audio stream
that starts within seconds, resumes where you stopped reading, and writes your
listening position back so an ereader picks up where the audio left off.

Built as the ebook narrator for the [Norrsaga](https://github.com/koedastudio/norrsaga)
apps, but any HLS player works.

## How it works

1. Downloads the epub from ABS, extracts the text and splits it into ~300-character
   sentence groups.
2. Synthesizes ~20 s MP3 segments with [Kokoro](https://github.com/remsky/Kokoro-FastAPI)
   a rolling window ahead of the playhead.
3. Serves them as a growing HLS EVENT playlist. Segments are cached on disk and
   shared across sessions; finish a book once and it is fully converted.
4. Maps the playhead back to a book percentage and writes ABS `ebookProgress`.

## Running

```bash
cp .env.example .env    # fill in ABS_URL and ABS_TOKEN
docker compose up -d --build
curl -s localhost:13379/healthz
```

A prebuilt multi-arch image is published on every release as
`ghcr.io/koedastudio/norrsaga-narrator` (`:latest`, `:0.1`, `:edge` for main).
Remove the `build: .` line in `docker-compose.yml` to pull it instead of building.

Two things to get right:

- **`ABS_TOKEN` must belong to the same ABS user the app signs in as.** Progress
  is stored per user.
- **Transport security is yours.** Requests are authenticated with the caller's
  ABS Bearer token, but nothing here does TLS. Put the sidecar behind the same
  TLS or VPN layer as ABS.

## API

All `/v1` routes require `Authorization: Bearer <ABS user token>`. The token is
validated against ABS, must belong to the same user as `ABS_TOKEN`, and the
verdict is cached for five minutes. Session ids are self-describing, so
every URL survives a restart.

| Route | Purpose |
| --- | --- |
| `POST /v1/sessions` `{itemId, voice?, from?}` | Start or re-attach a session. Resumes from ABS ebook progress unless `from` (0..1) overrides. Returns `sessionId`, `playlistUrl`, `startSegment`, `startOffsetSec`, `bookPercent`, `totalSegments`. |
| `GET /v1/sessions/{id}/playlist.m3u8` | Growing HLS EVENT playlist. The first response waits for ~20 s of audio. Supports blocking reload (`?_HLS_msn=N`, up to 15 s). `#EXT-X-ENDLIST` once the book is done. |
| `GET /v1/sessions/{id}/seg/{n}.mp3` | One segment, synthesized on demand on a cache miss. |
| `POST /v1/sessions/{id}/progress` `{positionSec, flush?}` | Map playlist time to a book percent and write it to ABS. Writes are throttled to one per 10 s unless `flush` is set (pause, seek, close). Returns `{bookPercent}`. |
| `DELETE /v1/sessions/{id}` | Close a session. Idle sessions expire after 30 min anyway. |
| `GET /healthz` | `{status, version, kokoro, abs}`. No auth. |

Try it with ffplay:

```bash
TOKEN=<abs user token>
S=$(curl -s -XPOST localhost:13379/v1/sessions \
     -H "Authorization: Bearer $TOKEN" -H 'content-type: application/json' \
     -d '{"itemId":"<library item id>"}')
ffplay -headers "Authorization: Bearer $TOKEN" \
  "http://localhost:13379$(echo "$S" | jq -r .playlistUrl)"
```

## Development

```bash
uv sync
uv run pytest            # ABS and Kokoro are mocked; needs ffmpeg on PATH
uv run ruff check . && uv run ruff format --check .
docker run -p 8880:8880 ghcr.io/remsky/kokoro-fastapi-cpu   # live TTS
uv run python -m narrator.main
```

Cache layout under `CACHE_DIR`: `{itemId}/book.json` holds the extracted text
and segment table, `{itemId}/{voice}/seg-NNNNNN.mp3` + `.json` one segment each.
A change to extraction or segmentation must bump `EXTRACTOR_VERSION` in
`narrator/epub.py`; cached books are then re-extracted on next use.

## License

[GPL-3.0-or-later](LICENSE).
