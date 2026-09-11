"""Generation-aware freshness caching for FPL service data."""

from __future__ import annotations

from dataclasses import dataclass, replace
from threading import Condition, RLock
from time import monotonic
from typing import Callable, Generic, TypeVar


T = TypeVar("T")


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


class FreshnessCache:
    """Serialize reloads and prevent invalidated generations from being stored."""

    def __init__(self, *, clock: Callable[[], float] = monotonic) -> None:
        self._clock = clock
        self._lock = RLock()
        self._records: dict[str, FreshnessRecord[object]] = {}
        self._generations: dict[str, int] = {}
        self._loading: dict[str, int] = {}
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
        with self._lock:
            while True:
                generation = self._generations.setdefault(key, 0)
                record = self._records.get(key)
                if (
                    record is not None
                    and record.generation == generation
                    and self._clock() < record.expires_at
                    and (not force or waited_for_generation == generation)
                ):
                    return replace(record, cache_hit=True, stale=False, last_error=None)  # type: ignore[return-value]

                condition = self._conditions.setdefault(key, Condition(self._lock))
                if key not in self._loading:
                    self._loading[key] = generation
                    previous = record
                    break
                waited_for_generation = generation
                condition.wait()

        try:
            value = loader()
        except Exception as error:
            with self._lock:
                self._loading.pop(key, None)
                self._conditions[key].notify_all()
            if previous is not None and policy.stale_if_error:
                return replace(
                    previous,
                    cache_hit=True,
                    stale=True,
                    last_error=str(error),
                )  # type: ignore[return-value]
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
            self._loading.pop(key, None)
            self._conditions[key].notify_all()
        return loaded

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
