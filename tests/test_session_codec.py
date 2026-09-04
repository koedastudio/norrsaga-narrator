import pytest

from narrator.session import UnknownSessionError, decode_session_id, encode_session_id


def test_round_trip():
    sid = encode_session_id("li_abc123", "af_heart", 42)
    assert decode_session_id(sid) == ("li_abc123", "af_heart", 42)


def test_mixed_voice_round_trips():
    sid = encode_session_id("li_abc123", "af_bella(2)+af_sky(1)", 0)
    assert decode_session_id(sid) == ("li_abc123", "af_bella(2)+af_sky(1)", 0)


def test_url_safe():
    sid = encode_session_id("li_abc123", "af_heart", 999999)
    assert not set("=/+") & set(sid)


@pytest.mark.parametrize(
    "bad",
    [
        "not-base64!!",
        "aGVsbG8",  # "hello": no fields
        encode_session_id("../me", "af_heart", 0),  # item id must not reach ABS URLs
        encode_session_id("li_abc", "a/b", 0),
        encode_session_id("li_abc", "af_heart", -1) + "x",
    ],
)
def test_garbage_raises(bad: str):
    with pytest.raises(UnknownSessionError):
        decode_session_id(bad)
