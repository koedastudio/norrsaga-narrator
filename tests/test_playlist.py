from narrator.playlist import render_playlist


def test_event_playlist_shape():
    text = render_playlist(start_segment=41, durations_sec=[20.5, 19.0, 21.25], ended=False)
    lines = text.strip().split("\n")
    assert lines[0] == "#EXTM3U"
    assert "#EXT-X-PLAYLIST-TYPE:EVENT" in lines
    assert "#EXT-X-SERVER-CONTROL:CAN-BLOCK-RELOAD=YES" in lines
    assert "#EXT-X-MEDIA-SEQUENCE:41" in lines
    assert "#EXT-X-START:TIME-OFFSET=0,PRECISE=YES" in lines
    assert "#EXTINF:20.500," in lines
    assert "seg/41.mp3" in lines
    assert "seg/43.mp3" in lines
    assert "#EXT-X-ENDLIST" not in text


def test_ended_playlist_has_endlist():
    text = render_playlist(0, [10.0], ended=True)
    assert text.strip().endswith("#EXT-X-ENDLIST")


def test_target_duration_covers_longest_segment():
    text = render_playlist(0, [10.0, 45.7], ended=False)
    assert "#EXT-X-TARGETDURATION:46" in text


def test_empty_playlist_renders_headers_only():
    text = render_playlist(0, [], ended=False)
    assert "#EXTINF" not in text
    assert "#EXT-X-TARGETDURATION:30" in text
