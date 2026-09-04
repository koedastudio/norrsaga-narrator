"""Deterministic sentence segmentation and position mapping.

The segment table maps global character offsets in BookText.full_text to
segment indexes. It must be reproducible run to run, because the disk cache
keys MP3s by segment index; the fingerprint invalidates a cached book when the
extractor version, segment size, or text changes.
"""

import hashlib
from bisect import bisect_right
from dataclasses import dataclass

import pysbd

from .epub import EXTRACTOR_VERSION, PARAGRAPH_BREAK, BookText

# ~300 chars is 15-25 s of speech: quick to synthesize, still whole sentences for prosody.
MAX_SEGMENT_CHARS = 300

_SEGMENTER = pysbd.Segmenter(language="en", clean=False, char_span=True)


@dataclass(frozen=True)
class Segment:
    index: int
    char_start: int  # inclusive, global offset into BookText.full_text
    char_end: int  # exclusive


def segment_book(book: BookText, max_chars: int = MAX_SEGMENT_CHARS) -> list[Segment]:
    """Split the book into segments of whole sentences, never crossing a paragraph."""
    segments: list[Segment] = []
    offset = 0
    for paragraph in book.full_text.split(PARAGRAPH_BREAK):
        for start, end in _pack_paragraph(paragraph, max_chars) if paragraph else []:
            segments.append(Segment(len(segments), offset + start, offset + end))
        offset += len(paragraph) + len(PARAGRAPH_BREAK)
    return segments


def _pack_paragraph(paragraph: str, max_chars: int) -> list[tuple[int, int]]:
    """Greedily pack consecutive sentences into spans of at most max_chars."""
    sentences: list[tuple[int, int]] = []
    for span in _SEGMENTER.segment(paragraph):
        if not span.sent.strip():
            continue
        end = span.end
        while end > span.start and paragraph[end - 1].isspace():
            end -= 1  # pysbd spans include trailing whitespace
        if end - span.start > max_chars:
            sentences.extend(_split_long(paragraph, span.start, end, max_chars))
        else:
            sentences.append((span.start, end))

    packed: list[tuple[int, int]] = []
    for start, end in sentences:
        if packed and end - packed[-1][0] <= max_chars:
            packed[-1] = (packed[-1][0], end)
        else:
            packed.append((start, end))
    return packed


def _split_long(text: str, start: int, end: int, max_chars: int) -> list[tuple[int, int]]:
    """Split one overlong sentence at word boundaries, hard-splitting as a last resort."""
    pieces: list[tuple[int, int]] = []
    pos = start
    while end - pos > max_chars:
        cut = text.rfind(" ", pos + 1, pos + max_chars + 1)
        if cut <= pos:
            cut = pos + max_chars
        pieces.append((pos, cut))
        pos = cut + 1 if text[cut : cut + 1] == " " else cut
    if pos < end:
        pieces.append((pos, end))
    return pieces


def fingerprint(book: BookText, max_chars: int = MAX_SEGMENT_CHARS) -> str:
    digest = hashlib.sha256(book.full_text.encode()).hexdigest()[:16]
    return f"{_params(max_chars)}-{digest}"


def fingerprint_is_current(value: str, max_chars: int = MAX_SEGMENT_CHARS) -> bool:
    """True when a stored fingerprint was made by this extractor version and segment size."""
    return value.startswith(f"{_params(max_chars)}-")


def _params(max_chars: int) -> str:
    return f"v{EXTRACTOR_VERSION}-c{max_chars}"


def percent_to_segment(segments: list[Segment], percent: float, total_chars: int) -> int:
    """First segment whose text is not entirely before `percent` of the book."""
    if not segments:
        return 0
    char = round(min(max(percent, 0.0), 1.0) * total_chars)
    return min(bisect_right([s.char_end for s in segments], char), len(segments) - 1)


def position_to_percent(
    segments: list[Segment],
    start_index: int,
    durations_sec: list[float],
    position_sec: float,
    total_chars: int,
) -> float:
    """Map playlist media time to a whole-book percent.

    `durations_sec[i]` is the duration of segment `start_index + i`. Positions past
    the known run clamp to the end of the last known segment.
    """
    if total_chars <= 0 or not segments:
        return 0.0
    remaining = max(position_sec, 0.0)
    char = segments[start_index].char_start
    for i, duration in enumerate(durations_sec):
        seg = segments[start_index + i]
        if duration > 0 and remaining < duration:
            char = seg.char_start + (remaining / duration) * (seg.char_end - seg.char_start)
            break
        remaining -= duration
        char = seg.char_end
    return min(char / total_chars, 1.0)
