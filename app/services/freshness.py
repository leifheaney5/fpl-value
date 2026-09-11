"""Generation-aware freshness caching for FPL service data."""

from __future__ import annotations

import logging
from collections.abc import Mapping
from dataclasses import dataclass, replace
from threading import Condition, RLock
from time import monotonic
from typing import Callable, Generic, TypeVar


T = TypeVar("T")


logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class FreshnessPolicy:
    """The acceptable age and error behavior for one kind of FPL data."""

    name: str
    ttl_seconds: float
    stale_if_error: bool = True


@dataclass(frozen=True)
class FreshnessRecord(Generic[T]):
    """A value together with its cache and freshness metadata."""

    value: T
    fetched_at: float
    expires_at: float
    generation: int
    cache_hit: bool
    stale: bool = False
    last_error: str | None = None


MY_TEAM_POLICY = FreshnessPolicy("my_team", ttl_seconds=60)
CURRENT_EVENT_FIXTURES_POLICY = FreshnessPolicy(
    "current_event_fixtures", ttl_seconds=5 * 60
)
PLAYER_MARKET_POLICY = FreshnessPolicy("player_market", ttl_seconds=10 * 60)
HISTORICAL_DATA_POLICY = FreshnessPolicy("historical_data", ttl_seconds=24 * 60 * 60)


def _selected_event(value: object) -> int | None:
    if not isinstance(value, Mapping):
        return None
    event = value.get("picks_event", value.get("event"))
    return event if isinstance(event, int) and not isinstance(event, bool) else None


class FreshnessCache:
    """Serialize reloads and prevent invalidated generations from being stored."""

    def __init__(self, *, clock: Callable[[], float] = monotonic) -> None:
        self._clock = clock
        self._lock = RLock()
        self._records: dict[str, FreshnessRecord[object]] = {}
        self._generations: dict[str, int] = {}
        self._loading: dict[str, int] = {}
        self._failed_loads: dict[
            str, tuple[int, FreshnessRecord[object] | None, Exception]
        ] = {}
        self._conditions: dict[str, Condition] = {}

    def get(
        self,
        key: str,
        loader: Callable[[], T],
        policy: FreshnessPolicy,
        *,
        force: bool = False,
    ) -> FreshnessRecord[T]:
        """Load a key once per generation, or return its valid cached record."""
        waited_for_generation: int | None = None
        cached_result: FreshnessRecord[T] | None = None
        cache_state: str | None = None
        with self._lock:
            while True:
                generation = self._generations.setdefault(key, 0)
                record = self._records.get(key)
                failed_load = self._failed_loads.get(key)
                if (
                    force
                    and waited_for_generation == generation
                    and failed_load is not None
                    and failed_load[0] == generation
                ):
                    stale_record = failed_load[1]
                    if stale_record is not None:
                        cached_result = stale_record  # type: ignore[assignment]
                        cache_state = "stale"
                        break
                    raise failed_load[2]
                if (
                    record is not None
                    and record.generation == generation
                    and self._clock() < record.expires_at
                    and (not force or waited_for_generation == generation)
                ):
                    cached_result = replace(
                        record, cache_hit=True, stale=False, last_error=None
                    )  # type: ignore[assignment]
                    cache_state = "hit"
                    break

                condition = self._conditions.setdefault(key, Condition(self._lock))
                if key not in self._loading:
                    self._loading[key] = generation
                    previous = record
                    break
                waited_for_generation = generation
                condition.wait()

        if cached_result is not None:
            self._log_record(key, policy, cached_result, cache_state or "hit")
            return cached_result

        try:
            value = loader()
        except Exception as error:
            with self._lock:
                stale_record = (
                    replace(
                        previous,
                        cache_hit=True,
                        stale=True,
                        last_error=str(error),
                    )
                    if previous is not None and policy.stale_if_error
                    else None
                )
                self._failed_loads[key] = (generation, stale_record, error)
                self._loading.pop(key, None)
                self._conditions[key].notify_all()
            if stale_record is not None:
                stale_result = stale_record  # type: ignore[assignment]
                self._log_record(key, policy, stale_result, "stale")
                return stale_result
            raise

        fetched_at = self._clock()
        loaded = FreshnessRecord(
            value=value,
            fetched_at=fetched_at,
            expires_at=fetched_at + policy.ttl_seconds,
            generation=generation,
            cache_hit=False,
        )
        with self._lock:
            if self._generations.get(key, 0) == generation:
                self._records[key] = loaded  # type: ignore[assignment]
            self._failed_loads.pop(key, None)
            self._loading.pop(key, None)
            self._conditions[key].notify_all()
        self._log_record(key, policy, loaded, "load")
        return loaded

    def _log_record(
        self,
        key: str,
        policy: FreshnessPolicy,
        record: FreshnessRecord[object],
        cache_state: str,
    ) -> None:
        now = self._clock()
        logger.info(
            "freshness_cache dataset=%s key=%s cache_state=%s event=%s "
            "fetched_at=%.1f expires_at=%.1f cache_hit=%s stale=%s "
            "age_ms=%.1f remaining_ttl_ms=%.1f",
            policy.name,
            key,
            cache_state,
            _selected_event(record.value),
            record.fetched_at,
            record.expires_at,
            record.cache_hit,
            record.stale,
            (now - record.fetched_at) * 1000,
            (record.expires_at - now) * 1000,
        )

    def invalidate(self, key: str | None = None) -> None:
        """Discard cached data and advance its generation."""
        with self._lock:
            keys = (
                set(self._records) | set(self._generations) | set(self._conditions)
                if key is None
                else {key}
            )
            for cache_key in keys:
                self._generations[cache_key] = self._generations.get(cache_key, 0) + 1
                self._records.pop(cache_key, None)
                self._failed_loads.pop(cache_key, None)
