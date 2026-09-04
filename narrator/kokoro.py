"""Client for kokoro-fastapi's OpenAI-compatible speech endpoint."""

import contextlib

import httpx


class KokoroError(Exception):
    pass


class KokoroClient:
    def __init__(self, base_url: str, default_voice: str, client: httpx.AsyncClient):
        self._base_url = base_url.rstrip("/")
        self.default_voice = default_voice
        self._client = client

    async def synthesize_wav(self, text: str, voice: str | None = None) -> bytes:
        try:
            response = await self._client.post(
                f"{self._base_url}/v1/audio/speech",
                json={
                    "model": "kokoro",
                    "voice": voice or self.default_voice,
                    "input": text,
                    "response_format": "wav",
                },
                timeout=httpx.Timeout(120.0, connect=10.0),
            )
        except httpx.HTTPError as e:
            raise KokoroError(f"kokoro unreachable: {e}") from e
        if response.status_code != 200:
            raise KokoroError(f"kokoro returned {response.status_code}: {response.text[:200]}")
        return response.content

    async def is_up(self) -> bool:
        try:
            response = await self._client.get(f"{self._base_url}/health", timeout=5.0)
        except httpx.HTTPError:
            return False
        return response.status_code == 200

    async def warm_up(self) -> None:
        """The first synthesis pays the model load; do it at startup, not on a listener."""
        with contextlib.suppress(KokoroError):
            await self.synthesize_wav("Ready.")
