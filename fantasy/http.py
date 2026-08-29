from __future__ import annotations

import httpx


class HttpClient:
    def __init__(self) -> None:
        self.client = httpx.Client(
            timeout=httpx.Timeout(25.0, connect=10.0),
            follow_redirects=True,
            headers={"User-Agent": "fantasy-toolkit/0.1 (personal draft assistant)"},
        )

    def get_json(self, url: str, *, params: dict[str, str] | None = None, headers: dict[str, str] | None = None):
        response = self.client.get(url, params=params, headers=headers)
        response.raise_for_status()
        return response.json()

    def get_text(self, url: str) -> str:
        response = self.client.get(url)
        response.raise_for_status()
        return response.text

    def close(self) -> None:
        self.client.close()

