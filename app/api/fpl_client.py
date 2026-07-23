from __future__ import annotations

from typing import Any

import httpx
import time

from app.config import Settings


class FPLClient:
    def __init__(self, settings: Settings) -> None:
        self.settings = settings

    def _get_json(self, url: str) -> Any:
        with httpx.Client(
            timeout=30.0,
            follow_redirects=True,
            headers={
                "Accept": "application/json",
                "User-Agent": "FPL-Value-Studio/1.0",
            },
        ) as client:
            last_error: Exception | None = None
            for attempt in range(3):
                try:
                    response = client.get(url)
                    response.raise_for_status()
                    payload = response.json()
                    if payload is None:
                        raise ValueError(f"FPL endpoint returned empty JSON: {url}")
                    return payload
                except (httpx.HTTPError, ValueError) as exc:
                    last_error = exc
                    if attempt < 2:
                        time.sleep(0.5 * (2 ** attempt))
            raise RuntimeError(f"FPL request failed after retries: {url}") from last_error

    def bootstrap(self) -> dict[str, Any]:
        payload = self._get_json(self.settings.fpl_bootstrap_url)
        if not isinstance(payload, dict):
            raise ValueError("FPL bootstrap response was not an object")
        for field in ("teams", "elements", "element_types", "events"):
            if not isinstance(payload.get(field), list):
                raise ValueError(f"FPL bootstrap field {field!r} is missing or invalid")
        if not payload["elements"]:
            raise ValueError("FPL bootstrap contained no players")
        return payload

    def fixtures(self) -> list[dict[str, Any]]:
        payload = self._get_json(self.settings.fpl_fixtures_url)
        if not isinstance(payload, list):
            raise ValueError("FPL fixtures response was not a list")
        fixtures = [item for item in payload if isinstance(item, dict)]
        for fixture in fixtures:
            if not fixture.get("id") or not fixture.get("team_h") or not fixture.get("team_a"):
                raise ValueError("FPL fixture response contained an incomplete fixture")
        return fixtures

    def player_history(self, player_id: int) -> dict[str, Any]:
        """Fetch the public per-player history endpoint when explicitly requested."""
        url = f"https://fantasy.premierleague.com/api/element-summary/{player_id}/"
        payload = self._get_json(url)
        if not isinstance(payload, dict):
            raise ValueError(f"FPL player history for {player_id} was not an object")
        if not isinstance(payload.get("history", []), list):
            raise ValueError(f"FPL player history for {player_id} was malformed")
        return payload

    def entry(self, entry_id: int) -> dict[str, Any]:
        payload = self._get_json(f"https://fantasy.premierleague.com/api/entry/{entry_id}/")
        if not isinstance(payload, dict) or payload.get("id") != entry_id:
            raise ValueError(f"FPL entry response for {entry_id} was malformed")
        return payload

    def entry_picks(self, entry_id: int, event_id: int) -> dict[str, Any]:
        payload = self._get_json(
            f"https://fantasy.premierleague.com/api/entry/{entry_id}/event/{event_id}/picks/"
        )
        if not isinstance(payload, dict) or not isinstance(payload.get("picks"), list):
            raise ValueError(f"FPL picks response for {entry_id} event {event_id} was malformed")
        return payload

    def entry_history(self, entry_id: int) -> dict[str, Any]:
        payload = self._get_json(f"https://fantasy.premierleague.com/api/entry/{entry_id}/history/")
        if not isinstance(payload, dict) or not isinstance(payload.get("current", []), list):
            raise ValueError(f"FPL history response for {entry_id} was malformed")
        return payload
