"""Disk cache for extracted books and synthesized segments.

    CACHE_DIR/{item_id}/book.json                extracted text, segment table, fingerprint
    CACHE_DIR/{item_id}/{voice}/seg-000123.mp3   one HLS segment
    CACHE_DIR/{item_id}/{voice}/seg-000123.json  {"duration_sec": ...}

The .json is written after the .mp3, so its presence means "segment complete".
Segment indexes are book-global, so every session over the same (item, voice)
shares the files.
"""

import json
from dataclasses import asdict, dataclass
from pathlib import Path

from .epub import BookText, DocText
from .segments import Segment


@dataclass(frozen=True)
class StoredBook:
    book: BookText
    segments: list[Segment]
    fingerprint: str
    ebook_ino: str  # ABS file identity the text was extracted from


class Cache:
    def __init__(self, root: Path):
        self._root = root
        # Durations are immutable once written, so hits are memoized; misses never are.
        # Without this, every playlist refresh re-reads one file per cached segment.
        self._durations: dict[tuple[str, str, int], float] = {}

    def book_path(self, item_id: str) -> Path:
        return self._root / _safe(item_id) / "book.json"

    def epub_path(self, item_id: str) -> Path:
        return self._root / _safe(item_id) / "book.epub"

    def segment_path(self, item_id: str, voice: str, index: int) -> Path:
        return self._root / _safe(item_id) / _safe(voice) / f"seg-{index:06d}.mp3"

    def load_book(self, item_id: str) -> StoredBook | None:
        try:
            data = json.loads(self.book_path(item_id).read_text())
            return StoredBook(
                book=BookText(
                    docs=[DocText(**d) for d in data["docs"]], full_text=data["full_text"]
                ),
                segments=[Segment(**s) for s in data["segments"]],
                fingerprint=data["fingerprint"],
                ebook_ino=data["ebook_ino"],
            )
        except (OSError, json.JSONDecodeError, KeyError, TypeError):
            return None

    def store_book(self, item_id: str, stored: StoredBook) -> None:
        payload = {
            "fingerprint": stored.fingerprint,
            "ebook_ino": stored.ebook_ino,
            "docs": [asdict(d) for d in stored.book.docs],
            "full_text": stored.book.full_text,
            "segments": [asdict(s) for s in stored.segments],
        }
        _write_atomic(self.book_path(item_id), json.dumps(payload))

    def drop_book(self, item_id: str) -> None:
        """Remove the book and every segment under it."""
        self._durations = {k: v for k, v in self._durations.items() if k[0] != item_id}
        book_dir = self._root / _safe(item_id)
        if not book_dir.exists():
            return
        for child in sorted(book_dir.rglob("*"), reverse=True):
            child.unlink() if child.is_file() else child.rmdir()
        book_dir.rmdir()

    def segment_duration(self, item_id: str, voice: str, index: int) -> float | None:
        """Duration of a complete segment, or None if not (fully) synthesized."""
        key = (item_id, voice, index)
        if key in self._durations:
            return self._durations[key]
        meta = self.segment_path(item_id, voice, index).with_suffix(".json")
        try:
            duration = float(json.loads(meta.read_text())["duration_sec"])
        except (OSError, json.JSONDecodeError, KeyError, ValueError):
            return None
        self._durations[key] = duration
        return duration

    def store_segment(self, item_id: str, voice: str, index: int, duration_sec: float) -> None:
        """Mark a segment complete; its .mp3 must already be at `segment_path`."""
        meta = self.segment_path(item_id, voice, index).with_suffix(".json")
        _write_atomic(meta, json.dumps({"duration_sec": duration_sec}))

    def contiguous_durations(self, item_id: str, voice: str, start: int, limit: int) -> list[float]:
        """Durations of the unbroken synthesized run starting at `start`."""
        durations: list[float] = []
        for index in range(start, start + limit):
            duration = self.segment_duration(item_id, voice, index)
            if duration is None:
                break
            durations.append(duration)
        return durations


def _safe(name: str) -> str:
    """Path-traversal guard; ids and voices are validated upstream anyway."""
    return "".join(c if c.isalnum() or c in "-_." else "_" for c in name) or "_"


def _write_atomic(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(text)
    tmp.replace(path)
