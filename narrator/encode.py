"""WAV duration and WAV→MP3 encoding through ffmpeg."""

import io
import subprocess
import wave
from pathlib import Path


class EncodeError(Exception):
    pass


def wav_duration_sec(wav_bytes: bytes) -> float:
    """Duration of an in-memory WAV, tolerant of streaming headers.

    kokoro-fastapi streams WAV without knowing the final length and writes the data
    size as 0xFFFFFFFF, which `wave` takes literally. The declared size is trusted
    only when it fits inside the bytes held; otherwise the real byte count is used.
    """
    try:
        with wave.open(io.BytesIO(wav_bytes)) as w:
            rate, channels, sample_width = w.getframerate(), w.getnchannels(), w.getsampwidth()
    except (wave.Error, EOFError) as e:
        raise EncodeError(f"bad wav: {e}") from e
    if rate <= 0 or channels <= 0 or sample_width <= 0:
        raise EncodeError("wav reports zero rate/channels/sample width")
    data_offset, declared_size = _data_chunk(wav_bytes)
    available = len(wav_bytes) - data_offset
    frame_bytes = declared_size if 0 < declared_size <= available else available
    return (frame_bytes // (channels * sample_width)) / rate


def _data_chunk(buf: bytes) -> tuple[int, int]:
    """(offset just past the 'data' chunk header, declared data size)."""
    pos = 12  # past RIFF + size + WAVE
    while pos + 8 <= len(buf):
        chunk_id = buf[pos : pos + 4]
        chunk_size = int.from_bytes(buf[pos + 4 : pos + 8], "little")
        if chunk_id == b"data":
            return pos + 8, chunk_size
        pos += 8 + chunk_size + (chunk_size & 1)  # chunks pad to even
    raise EncodeError("wav has no data chunk")


def wav_to_mp3(wav_bytes: bytes, dest: Path) -> None:
    """Encode to 64 kbps mono MP3 via tmp file + rename; a crash never leaves a partial segment."""
    dest.parent.mkdir(parents=True, exist_ok=True)
    tmp = dest.with_suffix(".tmp")
    result = subprocess.run(
        [
            "ffmpeg", "-hide_banner", "-loglevel", "error", "-y",
            "-i", "pipe:0",
            "-ac", "1", "-ar", "24000", "-b:a", "64k",
            "-f", "mp3", str(tmp),
        ],
        input=wav_bytes,
        capture_output=True,
    )  # fmt: skip
    if result.returncode != 0 or not tmp.exists():
        tmp.unlink(missing_ok=True)
        raise EncodeError(f"ffmpeg failed: {result.stderr.decode(errors='replace')[:300]}")
    tmp.replace(dest)
