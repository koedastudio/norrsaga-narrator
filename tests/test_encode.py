import io
import struct
import wave

import pytest

from narrator.encode import EncodeError, wav_duration_sec


def well_formed_wav(num_frames: int, rate: int = 24000) -> bytes:
    buf = io.BytesIO()
    with wave.open(buf, "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(rate)
        w.writeframes(struct.pack(f"<{num_frames}h", *([0] * num_frames)))
    return buf.getvalue()


def streaming_wav(pcm_bytes: bytes, rate: int = 24000, with_list_chunk: bool = True) -> bytes:
    """Mimics kokoro-fastapi: RIFF size and data-chunk size both 0xFFFFFFFF
    (the streamer doesn't know the final length up front), optionally with a
    LIST/INFO chunk between fmt and data — exactly what production returned."""
    fmt_chunk = b"fmt " + struct.pack("<IHHIIHH", 16, 1, 1, rate, rate * 2, 2, 16)
    list_chunk = (
        b"LIST" + struct.pack("<I", 26) + b"INFOISFT" + struct.pack("<I", 14) + b"kokoro-fastapi"
    )
    parts = [b"WAVE", fmt_chunk]
    if with_list_chunk:
        parts.append(list_chunk)
    parts.append(b"data" + struct.pack("<I", 0xFFFFFFFF) + pcm_bytes)
    body = b"".join(parts)
    return b"RIFF" + struct.pack("<I", 0xFFFFFFFF) + body


def test_well_formed_wav_duration():
    wav = well_formed_wav(num_frames=24000)  # exactly 1s at 24kHz
    assert wav_duration_sec(wav) == pytest.approx(1.0)


def test_streaming_placeholder_header_uses_real_byte_count():
    # 2.35s of mono 16-bit PCM at 24kHz.
    pcm = b"\x00\x00" * int(24000 * 2.35)
    wav = streaming_wav(pcm)
    # The bug this regresses: trusting the header gave ~89478s for any
    # placeholder-sized response, regardless of actual audio length.
    assert wav_duration_sec(wav) == pytest.approx(2.35, abs=0.01)


def test_streaming_header_without_list_chunk():
    pcm = b"\x00\x00" * 24000  # 1s
    wav = streaming_wav(pcm, with_list_chunk=False)
    assert wav_duration_sec(wav) == pytest.approx(1.0)


def test_trailing_metadata_after_data_is_not_counted_as_audio():
    # Legal WAV shape some tools produce: LIST/ID3 chunks AFTER the data
    # chunk. The declared data size is honest here and must win over
    # "everything to end of buffer".
    base = well_formed_wav(num_frames=24000)  # 1s, data chunk is last
    trailing = b"LIST" + struct.pack("<I", 10) + b"INFOICMT" + b"\x00\x00"
    assert wav_duration_sec(base + trailing) == pytest.approx(1.0)


def test_truncated_body_uses_available_bytes():
    # Declared size says 2s but the transfer was cut at 1s: report what we
    # actually hold, never the promise.
    full = well_formed_wav(num_frames=48000)  # 2s
    truncated = full[: len(full) - 48000]  # drop 1s of PCM, keep headers
    assert wav_duration_sec(truncated) == pytest.approx(1.0)


def test_no_data_chunk_raises():
    garbage = b"RIFF" + struct.pack("<I", 100) + b"WAVEfmt " + b"\x00" * 16
    with pytest.raises(EncodeError):
        wav_duration_sec(garbage)


def test_not_a_wav_raises():
    with pytest.raises(EncodeError):
        wav_duration_sec(b"definitely not a wav file")
