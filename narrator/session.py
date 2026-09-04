"""Narration sessions: just-in-time synthesis a rolling window ahead of the playhead.

A session is one listen-through of one book from one start segment. Its id is
self-describing (item id, voice, start segment), so after a restart any playlist
or segment URL rebuilds the session from the disk cache.
"""

import asyncio
import base64
import binascii
import logging
import re
import time
from dataclasses import dataclass, field
from pathlib import Path

from .abs import AbsClient, AbsNotFound
from .cache import Cache, StoredBook
from .config import Settings
from .encode import wav_duration_sec, wav_to_mp3
from .epub import extract_book_text
from .kokoro import KokoroClient
from .segments import (
    fingerprint,
    fingerprint_is_current,
    percent_to_segment,
    position_to_percent,
    segment_book,
)
from .version import __version__

log = logging.getLogger("narrator.session")

ITEM_ID_PATTERN = r"^[A-Za-z0-9_-]{1,64}$"
VOICE_PATTERN = r"^[A-Za-z0-9_+().-]{1,64}$"  # kokoro voice names and "a+b" mixes

PREROLL_SEGMENTS = 1  # one segment of recap before the resume point
LOOKAHEAD_SEGMENTS = 8  # ~2-3 min synthesized ahead of the last-served segment
# The first playlist response waits for this much audio, so playback starts with runway.
# Seconds rather than a segment count: front-matter segments are often only 2-3 s.
MIN_PLAYLIST_RUNWAY_SEC = 20.0
PLAYLIST_WAIT_SEC = 15.0  # blocking-reload cap; must stay under the client's read timeout
SESSION_IDLE_TTL_SEC = 30 * 60
PROGRESS_PATCH_MIN_INTERVAL_SEC = 10.0  # ABS write throttle


class UnknownSessionError(Exception):
    pass


def encode_session_id(item_id: str, voice: str, start_segment: int) -> str:
    raw = f"{item_id}:{voice}:{start_segment}".encode()
    return base64.urlsafe_b64encode(raw).decode().rstrip("=")


def decode_session_id(session_id: str) -> tuple[str, str, int]:
    try:
        padded = session_id + "=" * (-len(session_id) % 4)
        item_id, voice, start = base64.urlsafe_b64decode(padded).decode().rsplit(":", 2)
        start_segment = int(start)
    except (binascii.Error, UnicodeDecodeError, ValueError) as e:
        raise UnknownSessionError(session_id) from e
    if not (re.fullmatch(ITEM_ID_PATTERN, item_id) and re.fullmatch(VOICE_PATTERN, voice)):
        raise UnknownSessionError(session_id)
    return item_id, voice, start_segment


@dataclass
class SessionState:
    session_id: str
    item_id: str
    voice: str
    start_segment: int
    stored: StoredBook
    next_index: int  # synthesis cursor, owned by the worker between jumps
    target: int  # synthesis horizon
    wake: asyncio.Event = field(default_factory=asyncio.Event)
    worker: asyncio.Task | None = None
    last_touch: float = field(default_factory=time.monotonic)
    last_patch: float = 0.0


class SessionManager:
    def __init__(
        self, settings: Settings, abs_client: AbsClient, kokoro: KokoroClient, cache: Cache
    ):
        self._settings = settings
        self._abs = abs_client
        self._kokoro = kokoro
        self._cache = cache
        self._sessions: dict[str, SessionState] = {}
        self._book_locks: dict[str, asyncio.Lock] = {}
        # Kokoro and ffmpeg both eat CPU: serialize synthesis so a jump never
        # starves the segment the player needs next.
        self._synth_sem = asyncio.Semaphore(1)
        self._inflight: dict[tuple[str, str, int], asyncio.Task[float]] = {}

    async def create_session(
        self, item_id: str, voice: str | None = None, from_percent: float | None = None
    ) -> dict:
        self._purge_idle()
        voice = voice or self._kokoro.default_voice
        stored = await self._load_book(item_id)
        percent = (
            from_percent
            if from_percent is not None
            else await self._abs.get_ebook_progress(item_id)
        )
        resume_segment = percent_to_segment(stored.segments, percent, stored.book.total_chars)
        start_segment = max(0, resume_segment - PREROLL_SEGMENTS)
        session_id = encode_session_id(item_id, voice, start_segment)
        state = self._sessions.get(session_id) or self._new_state(
            session_id, item_id, voice, start_segment, stored
        )
        state.last_touch = time.monotonic()

        preroll = resume_segment - start_segment
        preroll_durations = self._cache.contiguous_durations(item_id, voice, start_segment, preroll)
        return {
            "sessionId": session_id,
            "playlistUrl": f"/v1/sessions/{session_id}/playlist.m3u8",
            "startSegment": start_segment,
            "startOffsetSec": sum(preroll_durations) if len(preroll_durations) == preroll else None,
            "bookPercent": percent,
            "totalSegments": len(stored.segments),
        }

    async def playlist(
        self, session_id: str, block_msn: int | None = None
    ) -> tuple[int, list[float], bool]:
        """(start_segment, durations of the contiguous synthesized run, ended).

        `block_msn` is the HLS blocking-reload directive: wait until that segment
        exists. Any growth at all is answered immediately, since players re-request
        a changed playlist at once but back off on an unchanged one.
        """
        state = await self._resolve(session_id)
        total = len(state.stored.segments)
        if block_msn is not None and block_msn < total:
            # Make sure the worker is heading there; a paused player only polls the playlist.
            state.target = max(state.target, block_msn + LOOKAHEAD_SEGMENTS)
            state.wake.set()
        deadline = time.monotonic() + PLAYLIST_WAIT_SEC
        initial_end: int | None = None
        while True:
            durations = self._known_durations(state)
            end = state.start_segment + len(durations)
            if initial_end is None:
                initial_end = end
            ended = end >= total
            if block_msn is not None:
                satisfied = ended or end > block_msn or end > initial_end
            else:
                satisfied = ended or sum(durations) >= MIN_PLAYLIST_RUNWAY_SEC
            if satisfied or time.monotonic() > deadline:
                state.last_touch = time.monotonic()
                return state.start_segment, durations, ended
            await asyncio.sleep(0.5)

    async def segment(self, session_id: str, index: int) -> Path:
        """Path to segment `index`'s MP3, synthesizing it first if needed."""
        state = await self._resolve(session_id)
        if not state.start_segment <= index < len(state.stored.segments):
            raise UnknownSessionError(f"segment {index} out of range")
        state.last_touch = time.monotonic()
        state.target = max(state.target, index + LOOKAHEAD_SEGMENTS)
        if self._cache.segment_duration(state.item_id, state.voice, index) is None:
            # Cache miss: jump the worker here instead of grinding through the gap.
            if not state.next_index <= index <= state.next_index + 1:
                state.next_index = index
            await self._ensure_segment(state.stored, state.item_id, state.voice, index)
        state.wake.set()
        return self._cache.segment_path(state.item_id, state.voice, index)

    async def progress(self, session_id: str, position_sec: float, flush: bool = False) -> float:
        """Map playlist time to a book percent and write it to ABS (throttled unless flush)."""
        state = await self._resolve(session_id)
        state.last_touch = time.monotonic()
        percent = position_to_percent(
            state.stored.segments,
            state.start_segment,
            self._known_durations(state),
            position_sec,
            state.stored.book.total_chars,
        )
        now = time.monotonic()
        if flush or now - state.last_patch >= PROGRESS_PATCH_MIN_INTERVAL_SEC:
            state.last_patch = now
            await self._abs.patch_ebook_progress(state.item_id, percent)
        return percent

    async def close(self, session_id: str) -> None:
        state = self._sessions.pop(session_id, None)
        if state and state.worker:
            state.worker.cancel()

    async def health(self) -> dict:
        kokoro_up, abs_up = await asyncio.gather(self._kokoro.is_up(), self._abs.is_up())
        return {
            "status": "ok",
            "version": __version__,
            "kokoro": "ok" if kokoro_up else "down",
            "abs": "ok" if abs_up else "down",
        }

    def _known_durations(self, state: SessionState) -> list[float]:
        total = len(state.stored.segments)
        return self._cache.contiguous_durations(
            state.item_id, state.voice, state.start_segment, total - state.start_segment
        )

    def _new_state(
        self, session_id: str, item_id: str, voice: str, start_segment: int, stored: StoredBook
    ) -> SessionState:
        state = SessionState(
            session_id=session_id,
            item_id=item_id,
            voice=voice,
            start_segment=start_segment,
            stored=stored,
            next_index=start_segment,
            target=start_segment + LOOKAHEAD_SEGMENTS,
        )
        state.worker = asyncio.create_task(self._worker(state))
        self._sessions[session_id] = state
        return state

    async def _resolve(self, session_id: str) -> SessionState:
        """Existing session, or one rebuilt from its self-describing id."""
        state = self._sessions.get(session_id)
        if state is not None:
            return state
        item_id, voice, start_segment = decode_session_id(session_id)
        stored = await self._load_book(item_id)
        if not 0 <= start_segment < len(stored.segments):
            raise UnknownSessionError(session_id)
        log.info("rebuilt session %s (item=%s start=%d)", session_id, item_id, start_segment)
        return self._new_state(session_id, item_id, voice, start_segment, stored)

    async def _load_book(self, item_id: str) -> StoredBook:
        lock = self._book_locks.setdefault(item_id, asyncio.Lock())
        async with lock:
            ebook = AbsClient.ebook_file(await self._abs.get_item(item_id))
            if ebook is None:
                raise AbsNotFound(f"item {item_id} has no ebook file")
            ino = str(ebook.get("ino", ""))

            stored = self._cache.load_book(item_id)
            if stored and stored.ebook_ino == ino and fingerprint_is_current(stored.fingerprint):
                return stored
            if stored:
                log.info("cache for %s is stale, re-extracting", item_id)
            self._cache.drop_book(item_id)

            epub_path = self._cache.epub_path(item_id)
            await self._abs.download_ebook(item_id, ino, epub_path)
            book = await asyncio.to_thread(extract_book_text, str(epub_path))
            segments = await asyncio.to_thread(segment_book, book)
            fresh = StoredBook(book, segments, fingerprint(book), ino)
            self._cache.store_book(item_id, fresh)
            return fresh

    async def _worker(self, state: SessionState) -> None:
        """Keeps synthesis LOOKAHEAD_SEGMENTS ahead of the last-served segment."""
        total = len(state.stored.segments)
        while state.next_index < total:
            if state.next_index >= state.target:
                state.wake.clear()
                try:
                    await asyncio.wait_for(state.wake.wait(), timeout=60.0)
                except TimeoutError:
                    if time.monotonic() - state.last_touch > SESSION_IDLE_TTL_SEC:
                        return
                continue
            index = state.next_index
            try:
                await self._ensure_segment(state.stored, state.item_id, state.voice, index)
            except Exception:
                log.exception("synthesis failed for %s seg %d; backing off", state.item_id, index)
                await asyncio.sleep(5.0)
                continue
            if state.next_index == index:  # unless a serving-path jump moved the cursor
                state.next_index = index + 1

    async def _ensure_segment(
        self, stored: StoredBook, item_id: str, voice: str, index: int
    ) -> float:
        """Duration of segment `index`, synthesizing it once no matter how many waiters."""
        cached = self._cache.segment_duration(item_id, voice, index)
        if cached is not None:
            return cached
        key = (item_id, voice, index)
        task = self._inflight.get(key)
        if task is None:
            task = asyncio.create_task(self._synthesize(stored, item_id, voice, index))
            task.add_done_callback(lambda t: self._inflight.pop(key, None))
            self._inflight[key] = task
        # Shielded: a client hanging up must not cancel synthesis others wait on.
        return await asyncio.shield(task)

    async def _synthesize(self, stored: StoredBook, item_id: str, voice: str, index: int) -> float:
        async with self._synth_sem:
            duration = self._cache.segment_duration(item_id, voice, index)
            if duration is not None:
                return duration
            seg = stored.segments[index]
            text = stored.book.full_text[seg.char_start : seg.char_end].strip() or "…"
            wav = await self._kokoro.synthesize_wav(text, voice)
            duration = wav_duration_sec(wav)
            path = self._cache.segment_path(item_id, voice, index)
            await asyncio.to_thread(wav_to_mp3, wav, path)
            self._cache.store_segment(item_id, voice, index, duration)
            return duration

    def _purge_idle(self) -> None:
        now = time.monotonic()
        for session_id, state in list(self._sessions.items()):
            if now - state.last_touch > SESSION_IDLE_TTL_SEC:
                if state.worker:
                    state.worker.cancel()
                del self._sessions[session_id]
