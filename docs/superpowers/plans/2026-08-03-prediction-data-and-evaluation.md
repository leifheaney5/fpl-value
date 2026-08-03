# Prediction Data and Evaluation Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build the labelled dataset and the evaluation harness that a prediction model needs, so that when the model arrives there is already a way to prove whether it is better than what it replaces.

**Architecture:** Ingest the MIT-licensed `vaastav/Fantasy-Premier-League` archive into the existing `gameweek_history` table, which already carries `season` and a per-season unique key. A pure feature builder converts history into feature vectors that provably cannot see the future. A walk-forward harness trains on seasons up to N, evaluates on N+1, and scores six baselines so any future model has a bar to clear.

**Tech Stack:** Python 3.12, SQLAlchemy 2, Alembic, numpy, pytest. Training-only dependencies (scikit-learn) live in an optional extra and never enter the production image.

## Global Constraints

- Run tests with `PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 python -m pytest -q`. An installed `dash` pytest plugin crashes collection on Windows without it.
- Alembic revision for this increment is `0005`, `down_revision = "0004"`.
- Migrations must be idempotent and inspect existing columns before adding, matching `alembic/versions/0004_trust_foundation.py`.
- Use `batch_alter_table` for any nullability change so SQLite works.
- No new table for archive rows. Extend `gameweek_history`.
- Season labels use the FPL format `YYYY/YY`, e.g. `2024/25`. Archive directories use `YYYY-YY`, e.g. `2024-25`. Convert explicitly; never infer.
- A metric that cannot be computed is `None`, never `0.0`. See `docs/METRICS.md`.
- Do not add `torch`, `pandas` or `onnxruntime` in this increment. Layers A and B need neither.
- Every ingested row records whether its `started` value was observed or derived.

## Archive schema eras (verified 2026-08-03)

The ingestion layer must handle all four. Column sets were read from the live repository.

| Era | Seasons | Columns | Notes |
| --- | --- | --- | --- |
| Legacy | 2016-17, 2017-18, 2018-19 | 56 | Quoted headers in 2016-17. Extra detail stats (`attempted_passes`, `dribbles`, `ea_index`). Has `id`. No `position`, `team`, `starts`, xG. |
| Minimal | 2019-20, 2020-21 | 33 | No `position`, `team`, `starts`, xG. |
| Transitional | 2021-22 | 36 | Adds `position`, `team`, `xP`. Still no `starts` or xG. |
| Modern | 2022-23 to 2025-26 | 49 | Adds `starts`, `expected_goals`, `expected_assists`, `expected_goal_involvements`, `expected_goals_conceded`. |

Common core present in every era — this is the guaranteed feature surface:

```
name, assists, bonus, bps, clean_sheets, creativity, element, fixture,
goals_conceded, goals_scored, ict_index, influence, kickoff_time, minutes,
opponent_team, own_goals, penalties_missed, penalties_saved, red_cards, round,
saves, selected, team_a_score, team_h_score, threat, total_points,
transfers_balance, transfers_in, transfers_out, value, was_home, yellow_cards, GW
```

## File structure

| File | Responsibility |
| --- | --- |
| `alembic/versions/0005_gameweek_history_detail.py` | Add archive columns to `gameweek_history` |
| `app/db/models.py` | `GameweekHistory` gains the new columns |
| `app/services/archive_schema.py` | Pure per-era column mapping and row normalisation |
| `app/services/archive_import.py` | Fetch, parse and store archive seasons idempotently |
| `app/models/__init__.py` | New package for modelling code |
| `app/models/features.py` | Point-in-time feature builder, with availability mask |
| `app/models/baselines.py` | The six baselines the model must beat |
| `app/models/metrics.py` | MAE, RMSE, Spearman, calibration, log-likelihood |
| `app/models/evaluation.py` | Walk-forward harness and report |
| `app/cli.py` | `import-archive` and `evaluate` commands |

---

### Task 1: Extend `gameweek_history` for archive detail

**Files:**
- Create: `alembic/versions/0005_gameweek_history_detail.py`
- Modify: `app/db/models.py` (`GameweekHistory`)
- Test: `tests/test_migration.py`

**Interfaces:**
- Produces: `GameweekHistory` with `starts`, `started_is_derived`, `source`, `fixture_id`, `kickoff_time`, `opponent_team_id`, `saves`, `bps`, `yellow_cards`, `red_cards`, `own_goals`, `penalties_missed`, `penalties_saved`, `goals_conceded`, `expected_goal_involvements`, `expected_goals_conceded`, `influence`, `creativity`, `threat`, `transfers_in`, `transfers_out`, `transfers_balance`, `selected`, `position`, `team_name`.

- [ ] **Step 1: Write the failing test**

```python
# append to tests/test_migration.py
ARCHIVE_COLUMNS = {
    "starts", "started_is_derived", "source", "fixture_id", "kickoff_time",
    "opponent_team_id", "saves", "bps", "yellow_cards", "red_cards", "own_goals",
    "penalties_missed", "penalties_saved", "goals_conceded",
    "expected_goal_involvements", "expected_goals_conceded", "influence",
    "creativity", "threat", "transfers_in", "transfers_out", "transfers_balance",
    "selected", "position", "team_name",
}


def test_gameweek_history_carries_archive_detail(tmp_path):
    url = f"sqlite:///{tmp_path / 'archive.db'}"
    command.upgrade(_config(url), "head")
    inspector = sa.inspect(sa.create_engine(url))
    columns = {c["name"] for c in inspector.get_columns("gameweek_history")}
    missing = ARCHIVE_COLUMNS - columns
    assert not missing, f"missing archive columns: {sorted(missing)}"


def test_derived_start_flag_is_not_nullable(tmp_path):
    url = f"sqlite:///{tmp_path / 'derived.db'}"
    command.upgrade(_config(url), "head")
    inspector = sa.inspect(sa.create_engine(url))
    columns = {c["name"]: c for c in inspector.get_columns("gameweek_history")}
    assert columns["started_is_derived"]["nullable"] is False
    # starts itself IS nullable: six of ten archive seasons never recorded it.
    assert columns["starts"]["nullable"] is True
```

- [ ] **Step 2: Run to verify it fails**

Run: `PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 python -m pytest tests/test_migration.py -q -p no:warnings`
Expected: FAIL listing every missing column.

- [ ] **Step 3: Write the migration**

```python
"""Archive detail columns on gameweek_history."""

from alembic import op
import sqlalchemy as sa

revision = "0005"
down_revision = "0004"
branch_labels = None
depends_on = None

INT_COLUMNS = [
    "starts", "saves", "bps", "yellow_cards", "red_cards", "own_goals",
    "penalties_missed", "penalties_saved", "goals_conceded", "fixture_id",
    "opponent_team_id", "transfers_in", "transfers_out", "transfers_balance",
    "selected",
]
FLOAT_COLUMNS = [
    "expected_goal_involvements", "expected_goals_conceded",
    "influence", "creativity", "threat",
]


def upgrade() -> None:
    inspector = sa.inspect(op.get_bind())
    existing = {c["name"] for c in inspector.get_columns("gameweek_history")}

    for name in INT_COLUMNS:
        if name not in existing:
            op.add_column("gameweek_history", sa.Column(name, sa.Integer(), nullable=True))
    for name in FLOAT_COLUMNS:
        if name not in existing:
            op.add_column("gameweek_history", sa.Column(name, sa.Float(), nullable=True))
    if "kickoff_time" not in existing:
        op.add_column(
            "gameweek_history",
            sa.Column("kickoff_time", sa.DateTime(timezone=True), nullable=True),
        )
    if "position" not in existing:
        op.add_column("gameweek_history", sa.Column("position", sa.String(5), nullable=True))
    if "team_name" not in existing:
        op.add_column("gameweek_history", sa.Column("team_name", sa.String(60), nullable=True))
    if "source" not in existing:
        op.add_column(
            "gameweek_history",
            sa.Column("source", sa.String(20), nullable=False, server_default="api"),
        )
    if "started_is_derived" not in existing:
        # NOT NULL on purpose: whether a start was observed or inferred is never
        # unknown, and the evaluation harness must be able to segment on it.
        op.add_column(
            "gameweek_history",
            sa.Column(
                "started_is_derived", sa.Boolean(), nullable=False,
                server_default=sa.false(),
            ),
        )
    op.create_index(
        "ix_gameweek_history_season_gw", "gameweek_history", ["season", "gameweek"]
    )


def downgrade() -> None:
    op.drop_index("ix_gameweek_history_season_gw", table_name="gameweek_history")
    with op.batch_alter_table("gameweek_history") as batch:
        for name in (
            INT_COLUMNS + FLOAT_COLUMNS
            + ["kickoff_time", "position", "team_name", "source", "started_is_derived"]
        ):
            batch.drop_column(name)
```

- [ ] **Step 4: Add the columns to `GameweekHistory` in `app/db/models.py`**

Add inside the class, after `gameweek`:

```python
    source: Mapped[str] = mapped_column(String(20), nullable=False, default="api")
    fixture_id: Mapped[int | None] = mapped_column(Integer, nullable=True)
    kickoff_time: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    opponent_team_id: Mapped[int | None] = mapped_column(Integer, nullable=True)
    position: Mapped[str | None] = mapped_column(String(5), nullable=True)
    team_name: Mapped[str | None] = mapped_column(String(60), nullable=True)
    # Null for the six archive seasons that never recorded starts.
    starts: Mapped[int | None] = mapped_column(Integer, nullable=True)
    started_is_derived: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    saves: Mapped[int | None] = mapped_column(Integer, nullable=True)
    bps: Mapped[int | None] = mapped_column(Integer, nullable=True)
    yellow_cards: Mapped[int | None] = mapped_column(Integer, nullable=True)
    red_cards: Mapped[int | None] = mapped_column(Integer, nullable=True)
    own_goals: Mapped[int | None] = mapped_column(Integer, nullable=True)
    penalties_missed: Mapped[int | None] = mapped_column(Integer, nullable=True)
    penalties_saved: Mapped[int | None] = mapped_column(Integer, nullable=True)
    goals_conceded: Mapped[int | None] = mapped_column(Integer, nullable=True)
    expected_goal_involvements: Mapped[float | None] = mapped_column(Float, nullable=True)
    expected_goals_conceded: Mapped[float | None] = mapped_column(Float, nullable=True)
    influence: Mapped[float | None] = mapped_column(Float, nullable=True)
    creativity: Mapped[float | None] = mapped_column(Float, nullable=True)
    threat: Mapped[float | None] = mapped_column(Float, nullable=True)
    transfers_in: Mapped[int | None] = mapped_column(Integer, nullable=True)
    transfers_out: Mapped[int | None] = mapped_column(Integer, nullable=True)
    transfers_balance: Mapped[int | None] = mapped_column(Integer, nullable=True)
    selected: Mapped[int | None] = mapped_column(Integer, nullable=True)
```

Add `Index("ix_gameweek_history_season_gw", "season", "gameweek")` to `__table_args__`.

- [ ] **Step 5: Run the migration tests and the drift test**

Run: `PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 python -m pytest tests/test_migration.py -q -p no:warnings`
Expected: PASS. `test_migrated_schema_matches_the_models` must also pass — it compares the migrated schema against the models, so a mismatch between Step 3 and Step 4 fails here.

- [ ] **Step 6: Commit**

```bash
git add alembic/versions/0005_gameweek_history_detail.py app/db/models.py tests/test_migration.py
git commit -m "Add archive detail columns to gameweek history"
```

---

### Task 2: Per-era archive column mapping

**Files:**
- Create: `app/services/archive_schema.py`
- Test: `tests/test_archive_schema.py`

**Interfaces:**
- Produces: `ArchiveRow` frozen dataclass; `normalise_row(raw: dict[str, str], season: str) -> ArchiveRow | None`; `season_label(directory: str) -> str`; `CORE_COLUMNS: frozenset[str]`; `detect_era(columns: set[str]) -> str` returning `"legacy" | "minimal" | "transitional" | "modern"`.
- Consumes: nothing.

- [ ] **Step 1: Write the failing test**

```python
# tests/test_archive_schema.py
import pytest

from app.services.archive_schema import (
    CORE_COLUMNS, ArchiveRow, detect_era, normalise_row, season_label,
)

MODERN = {
    "name": "Mohamed Salah", "position": "MID", "team": "Liverpool",
    "element": "328", "fixture": "12", "round": "3", "GW": "3",
    "minutes": "90", "starts": "1", "total_points": "13", "goals_scored": "2",
    "assists": "1", "clean_sheets": "0", "goals_conceded": "1", "saves": "0",
    "bonus": "3", "bps": "52", "yellow_cards": "0", "red_cards": "0",
    "own_goals": "0", "penalties_missed": "0", "penalties_saved": "0",
    "expected_goals": "0.87", "expected_assists": "0.31",
    "expected_goal_involvements": "1.18", "expected_goals_conceded": "1.02",
    "influence": "78.4", "creativity": "45.1", "threat": "62.0",
    "ict_index": "18.5", "value": "128", "selected": "4210000",
    "transfers_in": "120000", "transfers_out": "8000",
    "transfers_balance": "112000", "opponent_team": "7", "was_home": "True",
    "kickoff_time": "2026-08-30T14:00:00Z",
}

MINIMAL = {
    k: v for k, v in MODERN.items()
    if k not in {"position", "team", "starts", "expected_goals",
                 "expected_assists", "expected_goal_involvements",
                 "expected_goals_conceded"}
}


def test_season_label_converts_archive_directory_format():
    assert season_label("2024-25") == "2024/25"
    assert season_label("2016-17") == "2016/17"
    with pytest.raises(ValueError):
        season_label("2024")


def test_era_detection():
    assert detect_era(set(MODERN)) == "modern"
    assert detect_era(set(MINIMAL)) == "minimal"
    assert detect_era(set(MINIMAL) | {"position", "team", "xP"}) == "transitional"
    assert detect_era(set(MINIMAL) | {"attempted_passes", "ea_index"}) == "legacy"


def test_core_columns_are_present_in_every_era():
    for payload in (MODERN, MINIMAL):
        assert CORE_COLUMNS <= set(payload)


def test_modern_row_records_observed_starts():
    row = normalise_row(MODERN, "2024/25")
    assert row.player_element == 328
    assert row.gameweek == 3
    assert row.minutes == 90
    assert row.starts == 1
    assert row.started is True
    assert row.started_is_derived is False
    assert row.expected_goals == 0.87
    assert row.price == 12.8
    assert row.is_home is True


def test_minimal_row_derives_started_and_flags_it():
    row = normalise_row(MINIMAL, "2019/20")
    assert row.starts is None
    assert row.started is True          # 90 minutes
    assert row.started_is_derived is True
    assert row.expected_goals is None   # never recorded that season
    assert row.position is None


def test_a_derived_start_uses_the_sixty_minute_rule():
    short = dict(MINIMAL, minutes="45")
    assert normalise_row(short, "2019/20").started is False
    long = dict(MINIMAL, minutes="60")
    assert normalise_row(long, "2019/20").started is True


def test_a_row_without_a_player_element_is_rejected():
    assert normalise_row(dict(MODERN, element=""), "2024/25") is None
    assert normalise_row(dict(MODERN, GW=""), "2024/25") is None


def test_quoted_legacy_headers_are_tolerated():
    quoted = {f'"{k}"': v for k, v in MINIMAL.items()}
    stripped = {k.strip('"'): v for k, v in quoted.items()}
    assert normalise_row(stripped, "2016/17") is not None
```

- [ ] **Step 2: Run to verify it fails**

Run: `PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 python -m pytest tests/test_archive_schema.py -q -p no:warnings`
Expected: FAIL, module not found.

- [ ] **Step 3: Implement `app/services/archive_schema.py`**

```python
"""Column mapping for the FPL archive, which has four schema eras.

The archive spans ten seasons and its CSV layout changed three times. Rather
than assume a shape, each row is normalised against the columns that are
actually present, and anything a season never recorded stays None. A None here
means "this season did not record it", which is different from zero.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import datetime, timezone

# Present in every era. This is the guaranteed feature surface.
CORE_COLUMNS = frozenset({
    "assists", "bonus", "bps", "clean_sheets", "creativity", "element",
    "fixture", "goals_conceded", "goals_scored", "ict_index", "influence",
    "kickoff_time", "minutes", "opponent_team", "own_goals",
    "penalties_missed", "penalties_saved", "red_cards", "round", "saves",
    "selected", "threat", "total_points", "transfers_balance", "transfers_in",
    "transfers_out", "value", "was_home", "yellow_cards", "GW",
})

_LEGACY_MARKERS = {"attempted_passes", "ea_index", "big_chances_created"}
_SEASON_PATTERN = re.compile(r"^(\d{4})-(\d{2})$")

# A start was not recorded before 2022-23. Sixty minutes is the conventional
# proxy; it is wrong for a 45-minute start and for a 60-minute substitute
# appearance, so every derived value is flagged.
DERIVED_START_MINUTES = 60


def season_label(directory: str) -> str:
    """Convert an archive directory name (2024-25) to a season label (2024/25)."""
    match = _SEASON_PATTERN.match(directory)
    if not match:
        raise ValueError(
            f"Archive directory {directory!r} is not in YYYY-YY form; "
            "a season must never be guessed."
        )
    return f"{match.group(1)}/{match.group(2)}"


def detect_era(columns: set[str]) -> str:
    if columns & _LEGACY_MARKERS:
        return "legacy"
    if "starts" in columns:
        return "modern"
    if "position" in columns or "xP" in columns:
        return "transitional"
    return "minimal"


def _int(value, default=None):
    try:
        text = str(value).strip()
        return int(float(text)) if text else default
    except (TypeError, ValueError):
        return default


def _float(value, default=None):
    try:
        text = str(value).strip()
        return float(text) if text else default
    except (TypeError, ValueError):
        return default


def _bool(value) -> bool:
    return str(value).strip().casefold() in {"true", "1", "yes"}


def _time(value):
    text = str(value or "").strip()
    if not text:
        return None
    try:
        parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError:
        return None
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)


@dataclass(frozen=True)
class ArchiveRow:
    season: str
    player_element: int
    player_name: str
    gameweek: int
    minutes: int
    total_points: int
    started: bool
    started_is_derived: bool
    starts: int | None
    position: str | None
    team_name: str | None
    opponent_team_id: int | None
    fixture_id: int | None
    kickoff_time: datetime | None
    is_home: bool
    goals: int
    assists: int
    clean_sheets: int
    goals_conceded: int | None
    saves: int | None
    bonus: int | None
    bps: int | None
    yellow_cards: int | None
    red_cards: int | None
    own_goals: int | None
    penalties_missed: int | None
    penalties_saved: int | None
    expected_goals: float | None
    expected_assists: float | None
    expected_goal_involvements: float | None
    expected_goals_conceded: float | None
    influence: float | None
    creativity: float | None
    threat: float | None
    ict_index: float | None
    price: float | None
    selected: int | None
    transfers_in: int | None
    transfers_out: int | None
    transfers_balance: int | None


def normalise_row(raw: dict[str, str], season: str) -> ArchiveRow | None:
    """Convert one archive CSV row. Returns None if it cannot be identified."""
    element = _int(raw.get("element"))
    gameweek = _int(raw.get("GW", raw.get("round")))
    if element is None or gameweek is None or gameweek <= 0:
        return None

    minutes = _int(raw.get("minutes"), 0) or 0
    recorded_starts = _int(raw.get("starts"))
    if recorded_starts is None:
        started = minutes >= DERIVED_START_MINUTES
        started_is_derived = True
    else:
        started = recorded_starts > 0
        started_is_derived = False

    price_units = _int(raw.get("value"))

    return ArchiveRow(
        season=season,
        player_element=element,
        player_name=str(raw.get("name") or "").strip(),
        gameweek=gameweek,
        minutes=minutes,
        total_points=_int(raw.get("total_points"), 0) or 0,
        started=started,
        started_is_derived=started_is_derived,
        starts=recorded_starts,
        position=(str(raw["position"]).strip() if raw.get("position") else None),
        team_name=(str(raw["team"]).strip() if raw.get("team") else None),
        opponent_team_id=_int(raw.get("opponent_team")),
        fixture_id=_int(raw.get("fixture")),
        kickoff_time=_time(raw.get("kickoff_time")),
        is_home=_bool(raw.get("was_home")),
        goals=_int(raw.get("goals_scored"), 0) or 0,
        assists=_int(raw.get("assists"), 0) or 0,
        clean_sheets=_int(raw.get("clean_sheets"), 0) or 0,
        goals_conceded=_int(raw.get("goals_conceded")),
        saves=_int(raw.get("saves")),
        bonus=_int(raw.get("bonus")),
        bps=_int(raw.get("bps")),
        yellow_cards=_int(raw.get("yellow_cards")),
        red_cards=_int(raw.get("red_cards")),
        own_goals=_int(raw.get("own_goals")),
        penalties_missed=_int(raw.get("penalties_missed")),
        penalties_saved=_int(raw.get("penalties_saved")),
        expected_goals=_float(raw.get("expected_goals")),
        expected_assists=_float(raw.get("expected_assists")),
        expected_goal_involvements=_float(raw.get("expected_goal_involvements")),
        expected_goals_conceded=_float(raw.get("expected_goals_conceded")),
        influence=_float(raw.get("influence")),
        creativity=_float(raw.get("creativity")),
        threat=_float(raw.get("threat")),
        ict_index=_float(raw.get("ict_index")),
        price=None if price_units is None else price_units / 10.0,
        selected=_int(raw.get("selected")),
        transfers_in=_int(raw.get("transfers_in")),
        transfers_out=_int(raw.get("transfers_out")),
        transfers_balance=_int(raw.get("transfers_balance")),
    )
```

- [ ] **Step 4: Run the tests**

Run: `PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 python -m pytest tests/test_archive_schema.py -q -p no:warnings`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add app/services/archive_schema.py tests/test_archive_schema.py
git commit -m "Map FPL archive columns across its four schema eras"
```

---

### Task 2a: Stable player identity (plan correction, discovered during Task 2)

**Why this task exists.** The plan originally assumed archive rows could be
keyed on `element` straight onto `Player.id`. That is wrong. FPL re-assigns
element IDs every season: verified on 2026-08-03, `id=1` is Shkodran Mustafi in
2019-20 and Fábio Vieira in 2024-25. Ingesting on `element` would have
attributed ten seasons of history to the wrong players, silently.

`code` is the stable FPL player identifier. It is present on every bootstrap
element (567 unique codes for 567 players) and in each archive season's
`players_raw.csv`. Resolution is therefore
`archive element → season players_raw code → current Player.code`.

**Files:**
- Create: `alembic/versions/0006_player_code.py`
- Modify: `app/db/models.py` (`Player`), `app/services/refresh.py`
- Test: `tests/test_migration.py`, `tests/test_refresh.py`

**Interfaces:**
- Produces: `Player.code: int | None`, unique, indexed.

- [ ] **Step 1:** Add a migration adding `players.code` as a nullable indexed integer with a unique constraint, and the matching model column.
- [ ] **Step 2:** Populate it in `refresh_data` from the bootstrap element's `code`.
- [ ] **Step 3:** Test that a refresh stores codes and that they are unique.

---

### Task 3: Archive ingestion

**Files:**
- Create: `app/services/archive_import.py`
- Test: `tests/test_archive_import.py`

**Interfaces:**
- Consumes: `normalise_row`, `season_label`, `detect_era` from `app.services.archive_schema`.
- Produces: `import_archive_season(db, season_directory, reader) -> dict[str, int]` returning `{"season", "era", "rows_read", "rows_written", "rows_rejected", "derived_starts"}`; `ArchiveReader` protocol with `read(season_directory: str) -> Iterator[dict[str, str]]`; `HttpArchiveReader`; `LocalArchiveReader(root)`.

- [ ] **Step 1: Write the failing test**

```python
# tests/test_archive_import.py
import pytest
from sqlalchemy import create_engine, select
from sqlalchemy.orm import sessionmaker

from app.db.base import Base
from app.db.models import GameweekHistory, Player, Team
from app.services.archive_import import import_archive_season

from datetime import datetime, timezone


class StubReader:
    def __init__(self, rows):
        self._rows = rows

    def read(self, season_directory):
        return iter(self._rows)


def _row(element, gw, minutes=90, **extra):
    payload = {
        "name": f"Player {element}", "element": str(element), "GW": str(gw),
        "round": str(gw), "minutes": str(minutes), "total_points": "5",
        "goals_scored": "0", "assists": "0", "clean_sheets": "0",
        "opponent_team": "4", "fixture": "10", "was_home": "True",
        "value": "55", "selected": "1000", "kickoff_time": "2024-08-17T14:00:00Z",
    }
    payload.update(extra)
    return payload


def _session(tmp_path, name):
    engine = create_engine(f"sqlite:///{tmp_path / name}", connect_args={"check_same_thread": False})
    Base.metadata.create_all(engine)
    Session = sessionmaker(engine, expire_on_commit=False)
    now = datetime.now(timezone.utc)
    with Session() as db:
        db.add(Team(id=1, name="Test", short_name="TST", updated_at=now))
        for element in (1, 2):
            db.add(Player(
                id=element, first_name="A", second_name=f"B{element}",
                web_name=f"P{element}", team_id=1, position="Midfielder",
                position_short="MID", status="a", news="", updated_at=now, raw={},
            ))
        db.commit()
    return Session


def test_import_stores_rows_under_the_named_season(tmp_path):
    Session = _session(tmp_path, "imp.db")
    reader = StubReader([_row(1, 1, starts="1"), _row(2, 1, starts="0", minutes="20")])

    with Session() as db:
        result = import_archive_season(db, "2024-25", reader)
        assert result["season"] == "2024/25"
        assert result["era"] == "modern"
        assert result["rows_written"] == 2
        assert result["derived_starts"] == 0

        rows = db.scalars(select(GameweekHistory)).all()
        assert {r.season for r in rows} == {"2024/25"}
        assert {r.source for r in rows} == {"archive"}


def test_import_flags_derived_starts_for_older_seasons(tmp_path):
    Session = _session(tmp_path, "derived.db")
    reader = StubReader([_row(1, 1, minutes="90"), _row(2, 1, minutes="30")])

    with Session() as db:
        result = import_archive_season(db, "2019-20", reader)
        assert result["era"] == "minimal"
        assert result["derived_starts"] == 2

        rows = {r.player_id: r for r in db.scalars(select(GameweekHistory)).all()}
        assert rows[1].started is True and rows[1].started_is_derived is True
        assert rows[2].started is False and rows[2].started_is_derived is True
        assert rows[1].starts is None


def test_import_is_idempotent(tmp_path):
    Session = _session(tmp_path, "idem.db")
    rows = [_row(1, 1), _row(1, 2)]

    with Session() as db:
        first = import_archive_season(db, "2024-25", StubReader(list(rows)))
        second = import_archive_season(db, "2024-25", StubReader(list(rows)))
        assert first["rows_written"] == 2
        assert second["rows_written"] == 0
        assert len(db.scalars(select(GameweekHistory)).all()) == 2


def test_unknown_players_are_rejected_not_invented(tmp_path):
    Session = _session(tmp_path, "unknown.db")
    with Session() as db:
        result = import_archive_season(db, "2024-25", StubReader([_row(999, 1)]))
        assert result["rows_written"] == 0
        assert result["rows_rejected"] == 1


def test_two_seasons_do_not_collide_on_the_same_gameweek(tmp_path):
    Session = _session(tmp_path, "seasons.db")
    with Session() as db:
        import_archive_season(db, "2023-24", StubReader([_row(1, 1)]))
        import_archive_season(db, "2024-25", StubReader([_row(1, 1)]))
        rows = db.scalars(select(GameweekHistory)).all()
        assert len(rows) == 2
        assert {r.season for r in rows} == {"2023/24", "2024/25"}
```

- [ ] **Step 2: Run to verify it fails**

Expected: FAIL, module not found.

- [ ] **Step 3: Implement `app/services/archive_import.py`**

```python
"""Ingest the FPL community archive into gameweek_history.

The archive is MIT licensed (vaastav/Fantasy-Premier-League). Rows are stored
against the season they describe, never the current one, because a season label
is what stops a previous-season row being differenced against this one.
"""

from __future__ import annotations

import csv
import io
import logging
from pathlib import Path
from typing import Iterable, Iterator, Protocol

import httpx
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db.models import GameweekHistory, Player
from app.services.archive_schema import (
    ArchiveRow, detect_era, normalise_row, season_label,
)

logger = logging.getLogger(__name__)

ARCHIVE_BASE = (
    "https://raw.githubusercontent.com/vaastav/Fantasy-Premier-League/master/data"
)
SEASONS = (
    "2016-17", "2017-18", "2018-19", "2019-20", "2020-21",
    "2021-22", "2022-23", "2023-24", "2024-25", "2025-26",
)


class ArchiveReader(Protocol):
    def read(self, season_directory: str) -> Iterator[dict[str, str]]: ...


class HttpArchiveReader:
    def __init__(self, base_url: str = ARCHIVE_BASE, timeout: float = 120.0):
        self.base_url = base_url
        self.timeout = timeout

    def read(self, season_directory: str) -> Iterator[dict[str, str]]:
        url = f"{self.base_url}/{season_directory}/gws/merged_gw.csv"
        with httpx.Client(timeout=self.timeout, follow_redirects=True) as client:
            response = client.get(url)
            response.raise_for_status()
            text = response.content.decode("utf-8", "replace")
        yield from csv.DictReader(io.StringIO(text))


class LocalArchiveReader:
    def __init__(self, root: str | Path):
        self.root = Path(root)

    def read(self, season_directory: str) -> Iterator[dict[str, str]]:
        path = self.root / season_directory / "gws" / "merged_gw.csv"
        with path.open("r", encoding="utf-8", errors="replace", newline="") as handle:
            yield from csv.DictReader(handle)


def _to_model(row: ArchiveRow) -> dict:
    return {
        "season": row.season,
        "gameweek": row.gameweek,
        "source": "archive",
        "opponent": str(row.opponent_team_id or ""),
        "opponent_team_id": row.opponent_team_id,
        "is_home": row.is_home,
        "minutes": row.minutes,
        "started": row.started,
        "started_is_derived": row.started_is_derived,
        "starts": row.starts,
        "points": row.total_points,
        "goals": row.goals,
        "assists": row.assists,
        "clean_sheets": row.clean_sheets,
        "goals_conceded": row.goals_conceded,
        "saves": row.saves,
        "bonus": row.bonus,
        "bps": row.bps,
        "yellow_cards": row.yellow_cards,
        "red_cards": row.red_cards,
        "own_goals": row.own_goals,
        "penalties_missed": row.penalties_missed,
        "penalties_saved": row.penalties_saved,
        "expected_goals": row.expected_goals,
        "expected_assists": row.expected_assists,
        "expected_goal_involvements": row.expected_goal_involvements,
        "expected_goals_conceded": row.expected_goals_conceded,
        "influence": row.influence,
        "creativity": row.creativity,
        "threat": row.threat,
        "price": row.price,
        "ownership": None,
        "selected": row.selected,
        "transfers_in": row.transfers_in,
        "transfers_out": row.transfers_out,
        "transfers_balance": row.transfers_balance,
        "position": row.position,
        "team_name": row.team_name,
        "fixture_id": row.fixture_id,
        "kickoff_time": row.kickoff_time,
    }


def import_archive_season(
    db: Session,
    season_directory: str,
    reader: ArchiveReader,
) -> dict[str, int | str]:
    """Import one archive season. Idempotent on (player, season, gameweek)."""
    season = season_label(season_directory)
    known_players = {player_id for (player_id,) in db.execute(select(Player.id))}
    existing = {
        (player_id, gameweek)
        for player_id, gameweek in db.execute(
            select(GameweekHistory.player_id, GameweekHistory.gameweek)
            .where(GameweekHistory.season == season)
        )
    }

    rows_read = rows_written = rows_rejected = derived = 0
    era = "unknown"
    captured = None

    for raw in reader.read(season_directory):
        rows_read += 1
        clean = {str(key).strip().strip('"'): value for key, value in raw.items()}
        if era == "unknown":
            era = detect_era(set(clean))
        parsed = normalise_row(clean, season)
        if parsed is None or parsed.player_element not in known_players:
            rows_rejected += 1
            continue
        if (parsed.player_element, parsed.gameweek) in existing:
            continue
        if parsed.started_is_derived:
            derived += 1
        if captured is None:
            captured = parsed.kickoff_time
        db.add(
            GameweekHistory(
                player_id=parsed.player_element,
                captured_at=parsed.kickoff_time or captured,
                raw={},
                **_to_model(parsed),
            )
        )
        existing.add((parsed.player_element, parsed.gameweek))
        rows_written += 1

    db.commit()
    logger.info(
        "archive_import season=%s era=%s read=%s written=%s rejected=%s derived=%s",
        season, era, rows_read, rows_written, rows_rejected, derived,
    )
    return {
        "season": season,
        "era": era,
        "rows_read": rows_read,
        "rows_written": rows_written,
        "rows_rejected": rows_rejected,
        "derived_starts": derived,
    }
```

Note: `GameweekHistory.captured_at` is non-nullable, so it is set from the row's kickoff time, falling back to the first kickoff seen in the file.

- [ ] **Step 4: Run the tests, then the full suite**

Run: `PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 python -m pytest -q -p no:warnings`
Expected: PASS.

- [ ] **Step 5: Add the CLI command in `app/cli.py`**

```python
def command_import_archive(seasons: list[str] | None) -> int:
    from app.services.archive_import import (
        SEASONS, HttpArchiveReader, import_archive_season,
    )

    reader = HttpArchiveReader()
    targets = seasons or list(SEASONS)
    with SessionLocal() as db:
        for directory in targets:
            result = import_archive_season(db, directory, reader)
            print(
                "  ".join(f"{key}={value}" for key, value in result.items())
            )
    return 0
```

Register the subparser:

```python
    archive = subparsers.add_parser("import-archive")
    archive.add_argument(
        "--season", action="append", dest="seasons",
        help="Archive directory such as 2024-25. Repeatable. Defaults to all.",
    )
```

and dispatch `if args.command == "import-archive": return command_import_archive(args.seasons)`.

- [ ] **Step 6: Commit**

```bash
git add app/services/archive_import.py app/cli.py tests/test_archive_import.py
git commit -m "Ingest the FPL community archive by season"
```

---

### Task 4: Point-in-time feature builder

**Files:**
- Create: `app/models/__init__.py` (empty)
- Create: `app/models/features.py`
- Test: `tests/test_features.py`

**Interfaces:**
- Produces: `FeatureVector` dataclass with `values: dict[str, float]`, `mask: dict[str, bool]`, `as_of: datetime`, `version: str`; `FEATURE_NAMES: tuple[str, ...]`; `build_features(history, target, as_of, information_state) -> FeatureVector`; `InformationState` constants `PRESEASON` and `IN_SEASON`; `HistoryRow` protocol.
- Consumes: `GameweekHistory` rows.

**The single contract:** no feature may depend on a row whose `kickoff_time` is at or after `as_of`.

- [ ] **Step 1: Write the failing test**

```python
# tests/test_features.py
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

import pytest

from app.models.features import (
    FEATURE_NAMES, InformationState, build_features,
)

NOW = datetime(2026, 9, 1, 12, 0, tzinfo=timezone.utc)


def _history(n, season="2026/27", start_gw=1, points=5, minutes=90):
    return [
        SimpleNamespace(
            season=season, gameweek=start_gw + i, minutes=minutes, points=points,
            started=True, started_is_derived=False, goals=1, assists=0,
            clean_sheets=0, saves=0, bonus=1, expected_goals=0.5,
            expected_assists=0.2, price=7.0,
            kickoff_time=NOW - timedelta(days=7 * (n - i)),
        )
        for i in range(n)
    ]


def _target(difficulty=3, is_home=True):
    return SimpleNamespace(difficulty=difficulty, is_home=is_home, gameweek=10)


def test_feature_vector_has_a_stable_named_schema():
    v = build_features(_history(6), _target(), NOW, InformationState.IN_SEASON)
    assert tuple(v.values) == FEATURE_NAMES
    assert set(v.mask) == set(FEATURE_NAMES)
    assert v.version


def test_future_rows_cannot_influence_the_result():
    """The leakage guarantee. This is the most important test in the file."""
    past = _history(6)
    future = [
        SimpleNamespace(
            season="2026/27", gameweek=50 + i, minutes=90, points=99,
            started=True, started_is_derived=False, goals=5, assists=5,
            clean_sheets=1, saves=0, bonus=3, expected_goals=4.0,
            expected_assists=4.0, price=99.0,
            kickoff_time=NOW + timedelta(days=7 * (i + 1)),
        )
        for i in range(6)
    ]

    clean = build_features(past, _target(), NOW, InformationState.IN_SEASON)
    poisoned = build_features(past + future, _target(), NOW, InformationState.IN_SEASON)

    assert clean.values == poisoned.values, (
        "a row after as_of changed the feature vector: this is training leakage"
    )


def test_a_row_exactly_at_the_cutoff_is_excluded():
    at_cutoff = _history(1)
    at_cutoff[0].kickoff_time = NOW
    empty = build_features([], _target(), NOW, InformationState.IN_SEASON)
    same = build_features(at_cutoff, _target(), NOW, InformationState.IN_SEASON)
    assert same.values == empty.values


def test_preseason_masks_every_current_season_feature():
    v = build_features(_history(6), _target(), NOW, InformationState.PRESEASON)
    current = [n for n in FEATURE_NAMES if n.startswith("cur_")]
    assert current, "expected current-season features to exist"
    assert all(v.mask[n] is False for n in current)
    assert all(v.values[n] == 0.0 for n in current), (
        "a masked feature must be zeroed so the network cannot read it"
    )


def test_in_season_reveals_current_season_features():
    v = build_features(_history(6), _target(), NOW, InformationState.IN_SEASON)
    assert any(v.mask[n] for n in FEATURE_NAMES if n.startswith("cur_"))


def test_previous_season_features_survive_the_preseason_mask():
    history = _history(30, season="2025/26")
    v = build_features(history, _target(), NOW, InformationState.PRESEASON)
    previous = [n for n in FEATURE_NAMES if n.startswith("prev_")]
    assert any(v.mask[n] for n in previous), (
        "preseason must still see the previous season; that is all it has"
    )


def test_no_history_produces_a_fully_masked_vector_not_a_crash():
    v = build_features([], _target(), NOW, InformationState.IN_SEASON)
    assert tuple(v.values) == FEATURE_NAMES
    assert not any(v.mask[n] for n in FEATURE_NAMES if n.startswith(("cur_", "prev_")))
```

- [ ] **Step 2: Run to verify it fails**

Expected: FAIL, module not found.

- [ ] **Step 3: Implement `app/models/features.py`**

Key requirements the implementation must satisfy:

- Filter history with `row.kickoff_time is not None and row.kickoff_time < as_of` **first**, before any other computation. Rows without a kickoff time are excluded, because their position in time is unknown.
- Split remaining rows into current season (matching the target's season) and previous seasons.
- Emit features named with a `cur_`, `prev_` or `fix_` prefix. `fix_` features (difficulty, home/away) are always available since they describe the fixture, not the past.
- Mask semantics: `mask[name] is False` means unavailable, and `values[name]` must then be exactly `0.0`. Zeroing masked features is what stops the network reading a stale value.
- `InformationState.PRESEASON` forces every `cur_` mask to `False`.
- `version` is a module constant, bumped whenever `FEATURE_NAMES` changes.

Feature set (36 features):

```python
FEATURE_NAMES = (
    # Fixture context, always available
    "fix_difficulty", "fix_is_home", "fix_gameweek",
    # Current season, masked in preseason
    "cur_matches", "cur_minutes_mean", "cur_minutes_last3", "cur_start_rate",
    "cur_points_mean", "cur_points_last3", "cur_points_per_90",
    "cur_goals_per_90", "cur_assists_per_90", "cur_clean_sheet_rate",
    "cur_saves_per_90", "cur_bonus_mean", "cur_xg_per_90", "cur_xa_per_90",
    "cur_price", "cur_price_delta",
    # Previous season, available in both states
    "prev_matches", "prev_minutes_mean", "prev_start_rate", "prev_points_mean",
    "prev_points_per_90", "prev_goals_per_90", "prev_assists_per_90",
    "prev_clean_sheet_rate", "prev_saves_per_90", "prev_bonus_mean",
    "prev_xg_per_90", "prev_xa_per_90", "prev_price_end",
    # Availability of the underlying eras
    "prev_has_xg", "prev_has_starts", "cur_has_xg", "cur_has_starts",
)
```

Each `*_per_90` is `sum(stat) * 90 / sum(minutes)` and is masked when total minutes is zero. `*_rate` values are proportions of matches. `cur_price_delta` is the current price minus the earliest observed current-season price, masked when fewer than two observations exist.

- [ ] **Step 4: Run the tests**

Run: `PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 python -m pytest tests/test_features.py -q -p no:warnings`
Expected: PASS, including the leakage test.

- [ ] **Step 5: Commit**

```bash
git add app/models/__init__.py app/models/features.py tests/test_features.py
git commit -m "Add point-in-time feature builder with availability mask"
```

---

### Task 5: Baselines

**Files:**
- Create: `app/models/baselines.py`
- Test: `tests/test_baselines.py`

**Interfaces:**
- Produces: `Baseline` protocol with `name: str` and `predict(features: FeatureVector) -> float`; `BASELINES: tuple[Baseline, ...]` containing `SeasonPointsPerGame`, `RecentForm`, `PointsPer90Scaled`, `FixtureAdjusted`, `PositionAverage`, `ExistingHeuristic`.
- Consumes: `FeatureVector` from `app.models.features`.

- [ ] **Step 1: Write the failing test**

```python
# tests/test_baselines.py
from app.models.baselines import BASELINES
from app.models.features import FEATURE_NAMES, FeatureVector


def _vector(**overrides):
    values = {name: 0.0 for name in FEATURE_NAMES}
    mask = {name: False for name in FEATURE_NAMES}
    for name, value in overrides.items():
        values[name] = value
        mask[name] = True
    mask["fix_difficulty"] = mask["fix_is_home"] = mask["fix_gameweek"] = True
    return FeatureVector(values=values, mask=mask, as_of=None, version="test")


def test_every_baseline_is_named_and_callable():
    names = [b.name for b in BASELINES]
    assert len(names) == len(set(names)), "baseline names must be unique"
    assert len(BASELINES) >= 6
    vector = _vector(cur_points_mean=4.0)
    for baseline in BASELINES:
        result = baseline.predict(vector)
        assert isinstance(result, float)
        assert result >= 0.0, f"{baseline.name} produced a negative points prediction"


def test_baselines_return_zero_rather_than_crashing_on_an_empty_vector():
    empty = _vector()
    for baseline in BASELINES:
        assert baseline.predict(empty) == 0.0


def test_recent_form_prefers_recent_points_when_available():
    from app.models.baselines import RecentForm
    high = _vector(cur_points_last3=8.0, cur_points_mean=2.0)
    low = _vector(cur_points_last3=1.0, cur_points_mean=2.0)
    assert RecentForm().predict(high) > RecentForm().predict(low)


def test_fixture_adjustment_penalises_hard_fixtures():
    from app.models.baselines import FixtureAdjusted
    easy = _vector(cur_points_mean=5.0, fix_difficulty=2.0)
    hard = _vector(cur_points_mean=5.0, fix_difficulty=5.0)
    assert FixtureAdjusted().predict(easy) > FixtureAdjusted().predict(hard)


def test_a_masked_feature_is_never_read_as_a_real_value():
    """Masked features are zero. A baseline must check the mask, not the value."""
    from app.models.baselines import SeasonPointsPerGame
    masked = _vector()
    masked.values["cur_points_mean"] = 99.0   # zeroed in practice; mask still False
    assert SeasonPointsPerGame().predict(masked) == 0.0
```

- [ ] **Step 2: Run to verify it fails**

Expected: FAIL, module not found.

- [ ] **Step 3: Implement `app/models/baselines.py`**

Every baseline reads `vector.mask[name]` before `vector.values[name]`, falls back to the previous-season equivalent when the current-season feature is masked, and returns `0.0` when it has nothing. `ExistingHeuristic` reproduces the current `project_next_fixtures` weighting (0.40 form, 0.35 points per game, 0.25 points per 90, scaled by minutes and fixture difficulty) so the comparison is against what is actually deployed.

- [ ] **Step 4: Run the tests and commit**

```bash
git add app/models/baselines.py tests/test_baselines.py
git commit -m "Add the baselines a model must beat"
```

---

### Task 6: Metrics

**Files:**
- Create: `app/models/metrics.py`
- Test: `tests/test_model_metrics.py`

**Interfaces:**
- Produces: `mean_absolute_error(actual, predicted) -> float | None`; `root_mean_squared_error(...) -> float | None`; `spearman_correlation(...) -> float | None`; `calibration_error(actual, predicted_quantiles) -> float | None`; `summarise(actual, predicted) -> dict[str, float | None]`.
- Consumes: `numpy`.

- [ ] **Step 1: Write the failing test**

```python
# tests/test_model_metrics.py
import math

from app.models.metrics import (
    calibration_error, mean_absolute_error, root_mean_squared_error,
    spearman_correlation, summarise,
)


def test_perfect_prediction_scores_zero_error():
    actual = [1.0, 5.0, 3.0]
    assert mean_absolute_error(actual, actual) == 0.0
    assert root_mean_squared_error(actual, actual) == 0.0


def test_errors_are_positive_and_rmse_punishes_outliers_harder():
    actual = [0.0, 0.0, 0.0]
    predicted = [0.0, 0.0, 9.0]
    assert mean_absolute_error(actual, predicted) == 3.0
    assert root_mean_squared_error(actual, predicted) > 3.0


def test_rank_correlation_detects_ordering_not_magnitude():
    actual = [1.0, 2.0, 3.0, 4.0]
    scaled = [10.0, 20.0, 30.0, 40.0]
    reversed_ = [4.0, 3.0, 2.0, 1.0]
    assert math.isclose(spearman_correlation(actual, scaled), 1.0)
    assert math.isclose(spearman_correlation(actual, reversed_), -1.0)


def test_metrics_return_none_rather_than_zero_when_undefined():
    assert mean_absolute_error([], []) is None
    assert spearman_correlation([1.0], [1.0]) is None      # needs 2+ points
    assert spearman_correlation([1.0, 1.0], [2.0, 2.0]) is None  # no variance


def test_calibration_is_perfect_when_quantiles_match_the_distribution():
    # 100 actuals uniform on [0,1); predicted 10th/50th/90th quantiles constant.
    actual = [i / 100 for i in range(100)]
    quantiles = [(0.1, 0.5, 0.9)] * 100
    assert calibration_error(actual, quantiles) < 0.05


def test_calibration_detects_an_overconfident_interval():
    actual = [i / 10 for i in range(100)]       # spread 0..10
    quantiles = [(0.49, 0.50, 0.51)] * 100      # absurdly narrow
    assert calibration_error(actual, quantiles) > 0.3


def test_summarise_reports_every_metric_by_name():
    result = summarise([1.0, 2.0, 3.0], [1.0, 2.5, 2.0])
    assert set(result) >= {"mae", "rmse", "spearman", "n"}
    assert result["n"] == 3
```

- [ ] **Step 2: Run to verify it fails, then implement**

`calibration_error` is the mean absolute deviation between nominal and empirical coverage across the supplied quantile levels: for the 10th/50th/90th, the fraction of actuals below each predicted quantile should be 0.1/0.5/0.9. Return `None` when there are fewer than 20 observations, because coverage is meaningless on a small sample.

- [ ] **Step 3: Commit**

```bash
git add app/models/metrics.py tests/test_model_metrics.py
git commit -m "Add accuracy, rank and calibration metrics"
```

---

### Task 7: Walk-forward evaluation harness

**Files:**
- Create: `app/models/evaluation.py`
- Test: `tests/test_evaluation.py`

**Interfaces:**
- Consumes: `build_features`, `BASELINES`, `summarise`.
- Produces: `EvaluationReport` dataclass with `folds: list[FoldResult]`, `by_model: dict[str, dict]`, `seasons: list[str]`, `generated_at`; `FoldResult` with `train_seasons`, `test_season`, `n_examples`, `scores: dict[str, dict]`; `walk_forward(db, seasons, models, information_state) -> EvaluationReport`; `season_order(seasons) -> list[str]`.

- [ ] **Step 1: Write the failing test**

```python
# tests/test_evaluation.py
import pytest

from app.models.evaluation import season_order, walk_forward_folds


def test_seasons_sort_chronologically_not_lexically():
    assert season_order(["2024/25", "2016/17", "2020/21"]) == [
        "2016/17", "2020/21", "2024/25",
    ]


def test_folds_never_train_on_the_future():
    folds = walk_forward_folds(["2021/22", "2022/23", "2023/24", "2024/25"])
    assert folds, "expected at least one fold"
    for train, test in folds:
        assert train, "a fold must have training seasons"
        assert all(season_order([s, test])[0] == s for s in train), (
            f"fold trains on {train} to predict {test}: that is future leakage"
        )


def test_the_first_season_is_never_a_test_season():
    folds = walk_forward_folds(["2021/22", "2022/23", "2023/24"])
    assert "2021/22" not in [test for _, test in folds]


def test_a_single_season_yields_no_folds():
    assert walk_forward_folds(["2024/25"]) == []


def test_minimum_training_seasons_is_respected():
    folds = walk_forward_folds(
        ["2021/22", "2022/23", "2023/24", "2024/25"], min_train_seasons=2
    )
    assert all(len(train) >= 2 for train, _ in folds)
    assert [test for _, test in folds] == ["2023/24", "2024/25"]
```

- [ ] **Step 2: Run to verify it fails, then implement**

`walk_forward_folds(seasons, min_train_seasons=1)` returns `[(train_seasons, test_season), ...]` with expanding training windows. `walk_forward` loads gameweek history per fold, builds features at each row's kickoff time, scores every baseline plus any supplied model, and returns the report. Each fold is scored separately for `PRESEASON` and `IN_SEASON` information states, because a model can be good in one and useless in the other.

- [ ] **Step 3: Add the CLI command**

```python
def command_evaluate(seasons: list[str] | None, output: str | None) -> int:
    import json
    from app.models.evaluation import walk_forward

    with SessionLocal() as db:
        report = walk_forward(db, seasons)
    payload = report.as_dict()
    if output:
        with open(output, "w", encoding="utf-8") as handle:
            json.dump(payload, handle, indent=2)
    for name, scores in payload["by_model"].items():
        print(f"{name:24} mae={scores.get('mae')} spearman={scores.get('spearman')}")
    return 0
```

Register `evaluate` with `--season` (repeatable) and `--output`.

- [ ] **Step 4: Run the full suite and commit**

```bash
git add app/models/evaluation.py app/cli.py tests/test_evaluation.py
git commit -m "Add walk-forward evaluation harness"
```

---

### Task 8: Run it for real and record the result

**Files:**
- Create: `docs/MODEL_EVALUATION.md`
- Modify: `pyproject.toml`, `README.md`, `ARCHITECTURE.md`

- [ ] **Step 1: Add numpy to dependencies**

In `pyproject.toml`, add `"numpy>=2.0,<3"` to `dependencies`, and add a training extra:

```toml
train = [
  "scikit-learn>=1.5,<2"
]
```

- [ ] **Step 2: Import the archive locally**

```bash
python -m app.cli init-db
python -m app.cli refresh --force          # populate players first
python -m app.cli import-archive
```

Record actual row counts per season and per era.

- [ ] **Step 3: Run the evaluation**

```bash
python -m app.cli evaluate --output docs/evaluation-baseline.json
```

- [ ] **Step 4: Write `docs/MODEL_EVALUATION.md`**

Record: seasons ingested and their row counts, which eras contributed derived starts, the fold structure, and every baseline's MAE, RMSE, Spearman and calibration for both information states. State plainly which baseline is strongest — that number is the bar the network must clear.

- [ ] **Step 5: Commit**

```bash
git add pyproject.toml docs/ README.md ARCHITECTURE.md
git commit -m "Record baseline evaluation across archive seasons"
```

---

## Self-review notes

Spec coverage against `2026-08-03-prediction-model-design.md`:

- Layer A data → Tasks 1, 2, 3. Archive eras, season tagging, idempotency, derived-start flagging all covered.
- Layer A features → Task 4, including the leakage guarantee as an explicit test.
- Layer B evaluation → Tasks 5, 6, 7, 8. All six baselines, all four metric families, walk-forward folds, and a recorded result.
- Layers C and D are deliberately absent; they get their own plan once Task 8 reports on data quality.

Type consistency: `FeatureVector` fields (`values`, `mask`, `as_of`, `version`) are used identically in Tasks 4, 5 and 7. `ArchiveRow` field names in Task 2 match `_to_model` in Task 3. `summarise` keys in Task 6 match the report fields read in Tasks 7 and 8.

Deliberate omission: no `torch`, `pandas` or `onnxruntime`. Layers A and B need none of them, and adding them now would bloat the production image for no benefit.
