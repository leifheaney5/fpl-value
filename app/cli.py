from __future__ import annotations

import argparse
from datetime import datetime
from zoneinfo import ZoneInfo

from app.api.fpl_client import FPLClient
from app.config import get_settings
from alembic import command
from alembic.config import Config
from app.db.session import SessionLocal
from app.services.history_import import import_history_directory
from app.services.refresh import refresh_data


def command_init_db() -> int:
    command.upgrade(Config("alembic.ini"), "head")
    print("Database migrated to head.")
    return 0


def command_refresh(scheduled: bool, force: bool) -> int:
    settings = get_settings()

    if scheduled and not force:
        local_now = datetime.now(ZoneInfo(settings.app_timezone))
        if local_now.hour != settings.refresh_hour:
            print(
                "Scheduled refresh skipped: "
                f"local time is {local_now:%H:%M} "
                f"in {settings.app_timezone}; "
                f"configured hour is {settings.refresh_hour:02d}:00."
            )
            return 0

    with SessionLocal() as db:
        run = refresh_data(db, settings, FPLClient(settings))
        print(
            f"Refresh {run.status}: {run.player_count} players, "
            f"{run.schema_change_count} schema changes."
        )
    return 0


def command_import_history(directory: str, season: str) -> int:
    with SessionLocal() as db:
        result = import_history_directory(db, directory, season)
    print("History import: " + ", ".join(f"{key}={value}" for key, value in result.items()))
    return 0


def command_import_archive(seasons: list[str] | None) -> int:
    from app.services.archive_import import (
        SEASONS,
        HttpArchiveReader,
        import_archive_season,
    )

    reader = HttpArchiveReader()
    targets = seasons or list(SEASONS)
    totals = {"rows_read": 0, "rows_written": 0, "rows_rejected": 0, "derived_starts": 0}
    with SessionLocal() as db:
        for directory in targets:
            result = import_archive_season(db, directory, reader)
            for key in totals:
                totals[key] += int(result[key])
            print(
                f"{result['season']}  era={result['era']:<13} "
                f"read={result['rows_read']:>6} written={result['rows_written']:>6} "
                f"rejected={result['rows_rejected']:>6} derived_starts={result['derived_starts']:>6}"
            )
    print("total " + "  ".join(f"{key}={value}" for key, value in totals.items()))
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="FPL Value Studio CLI")
    subparsers = parser.add_subparsers(dest="command", required=True)

    subparsers.add_parser("init-db")

    refresh = subparsers.add_parser("refresh")
    refresh.add_argument("--scheduled", action="store_true")
    refresh.add_argument("--force", action="store_true")

    history = subparsers.add_parser("import-history")
    history.add_argument("--directory", required=True)
    history.add_argument(
        "--season",
        required=True,
        help="Season the files describe, e.g. 2025/26. Never inferred: an "
        "untagged import mixes with the current season.",
    )

    archive = subparsers.add_parser("import-archive")
    archive.add_argument(
        "--season",
        action="append",
        dest="seasons",
        help="Archive directory such as 2024-25. Repeatable. Defaults to all.",
    )

    return parser


def main() -> int:
    args = build_parser().parse_args()
    if args.command == "init-db":
        return command_init_db()
    if args.command == "refresh":
        return command_refresh(args.scheduled, args.force)
    if args.command == "import-history":
        return command_import_history(args.directory, args.season)
    if args.command == "import-archive":
        return command_import_archive(args.seasons)
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
