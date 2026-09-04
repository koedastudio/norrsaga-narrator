"""HLS EVENT playlist rendering.

EVENT rather than a live window: entries are only appended, so the player can
seek anywhere that exists. EXT-X-START pins the start position to the playlist
head (media3 would otherwise treat a playlist without ENDLIST as live).
CAN-BLOCK-RELOAD lets the player long-poll `?_HLS_msn=N` for the next segment.
"""

import math

TARGET_DURATION_SEC = 30


def render_playlist(start_segment: int, durations_sec: list[float], ended: bool) -> str:
    target = max([TARGET_DURATION_SEC, *(math.ceil(d) for d in durations_sec)])
    lines = [
        "#EXTM3U",
        "#EXT-X-VERSION:3",
        f"#EXT-X-TARGETDURATION:{target}",
        "#EXT-X-PLAYLIST-TYPE:EVENT",
        "#EXT-X-SERVER-CONTROL:CAN-BLOCK-RELOAD=YES",
        f"#EXT-X-MEDIA-SEQUENCE:{start_segment}",
        "#EXT-X-START:TIME-OFFSET=0,PRECISE=YES",
    ]
    for i, duration in enumerate(durations_sec):
        lines += [f"#EXTINF:{duration:.3f},", f"seg/{start_segment + i}.mp3"]
    if ended:
        lines.append("#EXT-X-ENDLIST")
    return "\n".join(lines) + "\n"
