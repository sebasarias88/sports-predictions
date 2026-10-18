"""Command line entry point:  uv run predict <command>

Commands:
  check-db                 Test the database connection
  init-db                  Create tables and load the league catalog
  leagues                  List available leagues
  ingest                   Download historical results + odds into the database
"""

from __future__ import annotations

import argparse
import logging
import sys

from prediction_engine.config import get_settings
from prediction_engine.leagues import LEAGUES, select_leagues

log = logging.getLogger("prediction_engine")


def cmd_check_db(_: argparse.Namespace) -> int:
    from prediction_engine.db import check_connection

    print(f"OK -> {check_connection()}")
    return 0


def cmd_init_db(_: argparse.Namespace) -> int:
    from prediction_engine.db import init_db

    init_db()
    print(f"Database ready. {len(LEAGUES)} leagues in catalog.")
    return 0


def cmd_leagues(_: argparse.Namespace) -> int:
    for lg in LEAGUES:
        print(f"tier {lg.tier}  {lg.code:<4} {lg.country:<14} {lg.name:<22} ({lg.source})")
    return 0


def cmd_ingest(args: argparse.Namespace) -> int:
    from prediction_engine.db import get_engine
    from prediction_engine.ingestion import football_data_uk as fd
    from prediction_engine.ingestion.loader import save_matches

    codes = [c.strip().upper() for c in args.leagues.split(",")] if args.leagues else None
    leagues = select_leagues(codes, args.tier)
    settings = get_settings()
    current = fd.current_season_start()
    engine = get_engine()

    total_matches = total_odds = 0
    for league in leagues:
        if league.source == "api_football":
            print(f"[{league.code}] skipped: API-Football ingestion comes in a later phase")
            continue

        if league.source == "football_data_uk_main":
            batches = [
                (fd.season_label(y), fd.fetch_main_league(league, y, settings.data_dir, current))
                for y in range(args.from_season, current + 1)
            ]
        else:
            batches = [("all", fd.fetch_extra_league(league, args.from_season, settings.data_dir))]

        for label, matches in batches:
            with engine.begin() as conn:
                n_matches, n_odds = save_matches(conn, league, matches)
            total_matches += n_matches
            total_odds += n_odds
            print(f"[{league.code}] {label}: {n_matches} matches, {n_odds} odds")

    print(f"Done: {total_matches} matches, {total_odds} odds rows.")
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="predict", description="Football prediction engine")
    parser.add_argument("-v", "--verbose", action="store_true")
    sub = parser.add_subparsers(dest="command", required=True)

    sub.add_parser("check-db").set_defaults(func=cmd_check_db)
    sub.add_parser("init-db").set_defaults(func=cmd_init_db)
    sub.add_parser("leagues").set_defaults(func=cmd_leagues)

    ingest = sub.add_parser("ingest")
    ingest.add_argument("--leagues", help="Comma separated codes, e.g. SP1,I1,BRA")
    ingest.add_argument("--tier", type=int, help="Only leagues of this tier (1, 2 or 3)")
    ingest.add_argument(
        "--from-season", type=int, default=2015, help="First season start year (default 2015)"
    )
    ingest.set_defaults(func=cmd_ingest)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(levelname)s %(message)s",
    )
    logging.getLogger("httpx").setLevel(logging.WARNING)
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
