"""End-to-end tests: real app, real ffmpeg, real epub; ABS and Kokoro mocked at the transport."""

import io
import json
import struct
import wave
from pathlib import Path

import httpx
import pytest
from fastapi.testclient import TestClient

from narrator import segments
from narrator.config import Settings
from narrator.main import create_app
from narrator.version import __version__
from tests.test_epub import build_epub

ABS_URL = "http://abs.test"
KOKORO_URL = "http://kokoro.test"
USER_TOKEN = "user-token"
ITEM_ID = "li_test1"

CHAPTER = "<p>" + " ".join(f"Sentence number {i} of the story." for i in range(40)) + "</p>"


def silence_wav(duration_sec: float = 0.5) -> bytes:
    buf = io.BytesIO()
    with wave.open(buf, "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(24000)
        frames = int(24000 * duration_sec)
        w.writeframes(struct.pack(f"<{frames}h", *([0] * frames)))
    return buf.getvalue()


class FakeServers:
    """One httpx.MockTransport playing both ABS and Kokoro."""

    def __init__(self, epub_bytes: bytes):
        self.epub_bytes = epub_bytes
        self.ebook_progress = 0.0
        self.patched: list[float] = []
        self.synth_count = 0
        self.epub_downloads = 0

    def handler(self, request: httpx.Request) -> httpx.Response:
        path = request.url.path
        if str(request.url).startswith(KOKORO_URL):
            if path == "/health":
                return httpx.Response(200)
            if json.loads(request.content)["input"] != "Ready.":  # ignore startup warm-up
                self.synth_count += 1
            return httpx.Response(200, content=silence_wav())
        if path == "/api/me":
            users = {
                f"Bearer {USER_TOKEN}": "u1",
                "Bearer server-token": "u1",
                "Bearer other": "u2",
            }
            user = users.get(request.headers.get("authorization", ""))
            return httpx.Response(200, json={"id": user}) if user else httpx.Response(401)
        if path == "/status":
            return httpx.Response(200)
        if path == f"/api/items/{ITEM_ID}":
            return httpx.Response(
                200, json={"media": {"ebookFile": {"ino": "ino-42", "ebookFormat": "epub"}}}
            )
        if path == f"/api/items/{ITEM_ID}/ebook":
            self.epub_downloads += 1
            return httpx.Response(200, content=self.epub_bytes)
        if path == f"/api/me/progress/{ITEM_ID}":
            if request.method == "PATCH":
                self.patched.append(json.loads(request.content)["ebookProgress"])
                return httpx.Response(200, json={})
            return httpx.Response(200, json={"ebookProgress": self.ebook_progress})
        return httpx.Response(404)


def settings_for(tmp_path: Path) -> Settings:
    return Settings(
        abs_url=ABS_URL,
        abs_token="server-token",
        kokoro_url=KOKORO_URL,
        cache_dir=tmp_path / "cache",
    )


@pytest.fixture
def servers(tmp_path: Path) -> FakeServers:
    fixture_dir = tmp_path / "fixture"
    fixture_dir.mkdir()
    return FakeServers(Path(build_epub(fixture_dir, [("one", CHAPTER)])).read_bytes())


@pytest.fixture
def client(tmp_path: Path, servers: FakeServers):
    app = create_app(settings_for(tmp_path), http_transport=httpx.MockTransport(servers.handler))
    with TestClient(app) as test_client:
        test_client.headers["Authorization"] = f"Bearer {USER_TOKEN}"
        yield test_client


def test_rejects_missing_or_bad_token(client: TestClient):
    body = {"itemId": ITEM_ID}
    assert client.post("/v1/sessions", json=body, headers={"Authorization": ""}).status_code == 401
    bad = {"Authorization": "Bearer wrong"}
    assert client.post("/v1/sessions", json=body, headers=bad).status_code == 401
    other_user = {"Authorization": "Bearer other"}  # valid token, but not the narrator's user
    assert client.post("/v1/sessions", json=body, headers=other_user).status_code == 401


def test_rejects_malformed_ids(client: TestClient):
    assert client.post("/v1/sessions", json={"itemId": "../me"}).status_code == 422
    assert client.post("/v1/sessions", json={"itemId": ITEM_ID, "voice": "a/b"}).status_code == 422
    assert client.get("/v1/sessions/not-a-session/playlist.m3u8").status_code == 404


def test_healthz_needs_no_auth(client: TestClient):
    response = client.get("/healthz", headers={"Authorization": ""})
    assert response.status_code == 200
    assert response.json() == {"status": "ok", "version": __version__, "kokoro": "ok", "abs": "ok"}


def test_full_session_flow(client: TestClient, servers: FakeServers):
    created = client.post("/v1/sessions", json={"itemId": ITEM_ID})
    assert created.status_code == 201
    body = created.json()
    assert body["startSegment"] == 0  # never-opened book starts at the top
    assert body["totalSegments"] > 3
    sid = body["sessionId"]

    playlist = client.get(body["playlistUrl"])
    assert playlist.status_code == 200
    assert playlist.headers["content-type"].startswith("application/vnd.apple.mpegurl")
    assert "#EXT-X-PLAYLIST-TYPE:EVENT" in playlist.text
    assert "seg/0.mp3" in playlist.text

    segment = client.get(f"/v1/sessions/{sid}/seg/0.mp3")
    assert segment.status_code == 200
    assert segment.headers["content-type"] == "audio/mpeg"
    assert len(segment.content) > 100

    progress = client.post(f"/v1/sessions/{sid}/progress", json={"positionSec": 0.4})
    assert progress.status_code == 200
    assert 0.0 < progress.json()["bookPercent"] < 1.0
    assert servers.patched

    assert client.delete(f"/v1/sessions/{sid}").status_code == 204


def test_resumes_from_saved_ebook_progress(client: TestClient, servers: FakeServers):
    servers.ebook_progress = 0.5
    body = client.post("/v1/sessions", json={"itemId": ITEM_ID}).json()
    assert body["bookPercent"] == 0.5
    assert 0 < body["startSegment"] < body["totalSegments"] - 1


def test_from_override_beats_saved_progress(client: TestClient, servers: FakeServers):
    servers.ebook_progress = 0.9
    body = client.post("/v1/sessions", json={"itemId": ITEM_ID, "from": 0.0}).json()
    assert body["bookPercent"] == 0.0
    assert body["startSegment"] == 0


def test_blocking_reload_waits_for_requested_segment(client: TestClient):
    sid = client.post("/v1/sessions", json={"itemId": ITEM_ID}).json()["sessionId"]
    reload = client.get(f"/v1/sessions/{sid}/playlist.m3u8", params={"_HLS_msn": 1})
    assert reload.status_code == 200
    assert "seg/1.mp3" in reload.text


def test_blocking_reload_past_end_returns_ended_playlist(client: TestClient):
    body = client.post("/v1/sessions", json={"itemId": ITEM_ID}).json()
    total = body["totalSegments"]
    url = f"/v1/sessions/{body['sessionId']}/playlist.m3u8"
    reload = client.get(url, params={"_HLS_msn": total + 5})
    assert reload.status_code == 200
    assert "#EXT-X-ENDLIST" in reload.text
    assert f"seg/{total - 1}.mp3" in reload.text


def test_segment_out_of_range_is_404(client: TestClient):
    body = client.post("/v1/sessions", json={"itemId": ITEM_ID}).json()
    url = f"/v1/sessions/{body['sessionId']}/seg"
    assert client.get(f"{url}/{body['totalSegments']}.mp3").status_code == 404
    assert client.get(f"{url}/-1.mp3").status_code == 404


def test_progress_flush_bypasses_patch_throttle(client: TestClient, servers: FakeServers):
    sid = client.post("/v1/sessions", json={"itemId": ITEM_ID}).json()["sessionId"]
    client.get(f"/v1/sessions/{sid}/seg/0.mp3")
    url = f"/v1/sessions/{sid}/progress"

    assert client.post(url, json={"positionSec": 0.1}).status_code == 200
    assert len(servers.patched) == 1
    assert client.post(url, json={"positionSec": 0.2}).status_code == 200
    assert len(servers.patched) == 1  # throttled
    assert client.post(url, json={"positionSec": 0.3, "flush": True}).status_code == 200
    assert len(servers.patched) == 2


def test_missing_ebook_is_404(client: TestClient):
    assert client.post("/v1/sessions", json={"itemId": "li_other"}).status_code == 404


def test_session_survives_restart(tmp_path: Path, servers: FakeServers):
    settings = settings_for(tmp_path)
    transport = httpx.MockTransport(servers.handler)
    auth = {"Authorization": f"Bearer {USER_TOKEN}"}

    with TestClient(create_app(settings, http_transport=transport)) as first:
        body = first.post("/v1/sessions", json={"itemId": ITEM_ID}, headers=auth).json()
        assert first.get(body["playlistUrl"], headers=auth).status_code == 200
    synthesized_before = servers.synth_count

    with TestClient(create_app(settings, http_transport=transport)) as second:
        playlist = second.get(body["playlistUrl"], headers=auth)
        assert playlist.status_code == 200
        assert "seg/0.mp3" in playlist.text
        segment = second.get(f"/v1/sessions/{body['sessionId']}/seg/0.mp3", headers=auth)
        assert segment.status_code == 200
    assert servers.synth_count == synthesized_before  # cached segments reused
    assert servers.epub_downloads == 1


def test_extractor_version_bump_re_extracts(
    tmp_path: Path, servers: FakeServers, monkeypatch: pytest.MonkeyPatch
):
    settings = settings_for(tmp_path)
    transport = httpx.MockTransport(servers.handler)
    auth = {"Authorization": f"Bearer {USER_TOKEN}"}
    book_json = tmp_path / "cache" / ITEM_ID / "book.json"

    with TestClient(create_app(settings, http_transport=transport)) as first:
        body = first.post("/v1/sessions", json={"itemId": ITEM_ID}, headers=auth).json()
        first.get(f"/v1/sessions/{body['sessionId']}/seg/0.mp3", headers=auth)
    assert servers.epub_downloads == 1
    assert json.loads(book_json.read_text())["fingerprint"].startswith("v1-")
    synthesized_before = servers.synth_count

    monkeypatch.setattr(segments, "EXTRACTOR_VERSION", 2)
    with TestClient(create_app(settings, http_transport=transport)) as second:
        second.get(f"/v1/sessions/{body['sessionId']}/seg/0.mp3", headers=auth)
    assert servers.epub_downloads == 2
    assert json.loads(book_json.read_text())["fingerprint"].startswith("v2-")
    assert servers.synth_count > synthesized_before  # stale segments dropped and redone
