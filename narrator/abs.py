"""Audiobookshelf client.

Two tokens, kept separate: the server-side ABS_TOKEN does all the work (metadata,
epub download, progress); callers' Bearer tokens are only validated against
GET /api/me, and must belong to the same ABS user as ABS_TOKEN, since progress is
read and written on that user's behalf.
"""

import hashlib
import time
from pathlib import Path

import httpx

TOKEN_CACHE_TTL_SEC = 300.0
TOKEN_CACHE_MAX = 100


class AbsError(Exception):
    pass


class AbsNotFound(AbsError):
    pass


class AbsClient:
    def __init__(self, base_url: str, token: str, client: httpx.AsyncClient):
        self._base_url = base_url.rstrip("/")
        self._auth = {"Authorization": f"Bearer {token}"}
        self._client = client
        self._valid_tokens: dict[str, float] = {}  # sha256(token) -> verified at
        self._owner_id: str | None = None

    async def get_item(self, item_id: str) -> dict:
        return await self._get_json(f"/api/items/{item_id}?expanded=1&include=progress")

    @staticmethod
    def ebook_file(item: dict) -> dict | None:
        return (item.get("media") or {}).get("ebookFile")

    async def download_ebook(self, item_id: str, ino: str, dest: Path) -> None:
        """Try the ebook route first, then the raw-file route used by older ABS versions."""
        for path in (f"/api/items/{item_id}/ebook", f"/api/items/{item_id}/file/{ino}"):
            try:
                response = await self._client.get(
                    f"{self._base_url}{path}",
                    headers=self._auth,
                    timeout=httpx.Timeout(120.0, connect=10.0),
                    follow_redirects=True,
                )
            except httpx.HTTPError as e:
                raise AbsError(f"ABS unreachable: {e}") from e
            if response.status_code == 200 and response.content:
                dest.parent.mkdir(parents=True, exist_ok=True)
                tmp = dest.with_suffix(".tmp")
                tmp.write_bytes(response.content)
                tmp.replace(dest)
                return
        raise AbsNotFound(f"no downloadable ebook for item {item_id}")

    async def get_ebook_progress(self, item_id: str) -> float:
        """Saved reading position as 0..1; 0.0 for a never-opened book."""
        try:
            data = await self._get_json(f"/api/me/progress/{item_id}")
        except AbsNotFound:
            return 0.0
        return float(data.get("ebookProgress") or 0.0)

    async def patch_ebook_progress(self, item_id: str, percent: float) -> None:
        try:
            response = await self._client.patch(
                f"{self._base_url}/api/me/progress/{item_id}",
                headers=self._auth,
                json={"ebookProgress": round(min(max(percent, 0.0), 1.0), 6)},
                timeout=15.0,
            )
        except httpx.HTTPError as e:
            raise AbsError(f"ABS unreachable: {e}") from e
        if response.status_code >= 400:
            raise AbsError(f"progress PATCH failed: {response.status_code}")

    async def validate_user_token(self, bearer: str) -> bool:
        key = hashlib.sha256(bearer.encode()).hexdigest()
        now = time.monotonic()
        verified_at = self._valid_tokens.get(key)
        if verified_at is not None and now - verified_at < TOKEN_CACHE_TTL_SEC:
            return True
        try:
            owner_id = self._owner_id or (await self._get_json("/api/me"))["id"]
            response = await self._client.get(
                f"{self._base_url}/api/me",
                headers={"Authorization": f"Bearer {bearer}"},
                timeout=10.0,
            )
        except (AbsError, KeyError, httpx.HTTPError):
            # ABS down: keep trusting a token that was valid before, never a new one.
            return verified_at is not None
        self._owner_id = owner_id
        if response.status_code != 200 or response.json().get("id") != owner_id:
            self._valid_tokens.pop(key, None)
            return False
        self._valid_tokens[key] = now
        while len(self._valid_tokens) > TOKEN_CACHE_MAX:
            del self._valid_tokens[next(iter(self._valid_tokens))]
        return True

    async def is_up(self) -> bool:
        try:
            response = await self._client.get(f"{self._base_url}/status", timeout=5.0)
        except httpx.HTTPError:
            return False
        return response.status_code == 200

    async def _get_json(self, path: str) -> dict:
        try:
            response = await self._client.get(
                f"{self._base_url}{path}", headers=self._auth, timeout=30.0
            )
        except httpx.HTTPError as e:
            raise AbsError(f"ABS unreachable: {e}") from e
        if response.status_code == 404:
            raise AbsNotFound(path)
        if response.status_code >= 400:
            raise AbsError(f"ABS returned {response.status_code} for {path}")
        return response.json()
