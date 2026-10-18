"""Command line entry point:  uv run predict <command>

Commands:
  check-db                 Test the database connection
  init-db                  Create tables and load the league catalog
  leagues                  List available leagues
  ingest                   Download historical results + odds into the database
  backtest                 Test the model on past seasons against real odds
  match                    Probabilities for one match
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


def _league_codes(args: argparse.Namespace) -> list[str]:
    codes = [c.strip().upper() for c in args.leagues.split(",")] if args.leagues else None
    return [lg.code for lg in select_leagues(codes, args.tier) if lg.source != "api_football"]


def cmd_backtest(args: argparse.Namespace) -> int:
    import pandas as pd

    from prediction_engine.backtest import BacktestConfig, run_backtest
    from prediction_engine.data import load_matches_with_odds

    codes = _league_codes(args)
    data = load_matches_with_odds(codes)
    if data.empty:
        print("No matches in the database. Run `predict ingest` first.")
        return 1

    requested = [s.strip() for s in args.seasons.split(",")] if args.seasons else None
    all_preds, all_bets = [], []
    for code, league_df in data.groupby("league_code"):
        seasons = sorted(league_df["season"].unique())
        # default: the two most recent finished seasons (skip the one in progress)
        test_seasons = requested or seasons[-3:-1]
        cfg = BacktestConfig(
            test_seasons=test_seasons,
            bookmaker=args.bookmaker,
            phase=args.phase,
            min_edge=args.min_edge,
            max_odds=args.max_odds,
        )
        preds, bets, summary = run_backtest({code: league_df}, cfg)
        if summary.empty:
            print(f"[{code}] not enough data")
            continue
        all_preds.append(preds)
        all_bets.append(bets)
        print(f"\n=== {code}  seasons {', '.join(test_seasons)} ===")
        print(_format_summary(summary))

    if not all_preds:
        return 1

    preds = pd.concat(all_preds, ignore_index=True)
    bets = pd.concat(all_bets, ignore_index=True)
    if len(all_preds) > 1:
        from prediction_engine.backtest import summarize

        print("\n=== ALL LEAGUES ===")
        print(_format_summary(summarize(preds, bets)))

    out_dir = get_settings().data_dir / "backtests"
    out_dir.mkdir(parents=True, exist_ok=True)
    preds.to_csv(out_dir / "predictions.csv", index=False)
    bets.to_csv(out_dir / "bets.csv", index=False)
    print(f"\nDetail saved in {out_dir}/predictions.csv and bets.csv")
    return 0


def _format_summary(summary) -> str:
    df = summary.copy()
    for col in ("model_logloss", "market_logloss", "model_brier", "market_brier"):
        df[col] = df[col].map("{:.4f}".format)
    df["hit_rate"] = df["hit_rate"].map(lambda v: f"{v:.1%}" if v == v else "-")
    df["avg_odds"] = df["avg_odds"].map(lambda v: f"{v:.2f}" if v == v else "-")
    df["profit_units"] = df["profit_units"].map("{:+.1f}".format)
    df["roi"] = df["roi"].map(lambda v: f"{v:+.1%}" if v == v else "-")
    return df.to_string(index=False)


def cmd_match(args: argparse.Namespace) -> int:
    import pandas as pd

    from prediction_engine.data import load_matches_with_odds
    from prediction_engine.models.dixon_coles import DixonColesModel

    league = args.league.upper()
    data = load_matches_with_odds([league])
    today = pd.Timestamp.today().normalize()
    history = data[data["match_date"] >= today - pd.Timedelta(days=3 * 365)]
    teams = set(history["home_team"]) | set(history["away_team"])
    for team in (args.home, args.away):
        if team not in teams:
            print(f"Unknown team '{team}'. Teams in {league}:\n  " + "\n  ".join(sorted(teams)))
            return 1

    model = DixonColesModel().fit(history, as_of=today)
    pred = model.predict(args.home, args.away)
    p = pred.probs
    print(f"\n{args.home} vs {args.away}  ({league})")
    print(f"Expected goals: {pred.home_xg:.2f} - {pred.away_xg:.2f}\n")
    rows = [
        ("Home win", p["home"]),
        ("Draw", p["draw"]),
        ("Away win", p["away"]),
        ("Over 1.5", p["over_1_5"]),
        ("Over 2.5", p["over_2_5"]),
        ("Under 2.5", p["under_2_5"]),
        ("Over 3.5", p["over_3_5"]),
        ("Both teams score", p["btts_yes"]),
    ]
    for label, prob in rows:
        print(f"  {label:<18} {prob:6.1%}   fair odds {1 / prob:6.2f}")
    print("\nMost likely scores:")
    for score, prob in pred.top_scores(5):
        print(f"  {score:<6} {prob:6.1%}")
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

    bt = sub.add_parser("backtest", help="Test the model on past seasons against real odds")
    bt.add_argument("--leagues", help="Comma separated codes, e.g. SP1,I1")
    bt.add_argument("--tier", type=int)
    bt.add_argument("--seasons", help='e.g. "2023-2024,2024-2025" (default: last 2 finished)')
    bt.add_argument(
        "--bookmaker",
        default="market_avg",
        choices=["market_avg", "market_max", "bet365", "pinnacle"],
    )
    bt.add_argument("--phase", default="closing", choices=["opening", "closing"])
    bt.add_argument("--min-edge", type=float, default=0.05, help="Minimum expected value (0.05 = 5%%)")
    bt.add_argument("--max-odds", type=float, default=6.0)
    bt.set_defaults(func=cmd_backtest)

    match = sub.add_parser("match", help="Probabilities for one match")
    match.add_argument("--league", required=True)
    match.add_argument("--home", required=True)
    match.add_argument("--away", required=True)
    match.set_defaults(func=cmd_match)
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
