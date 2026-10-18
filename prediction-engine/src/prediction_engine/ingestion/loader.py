"""Write parsed matches and odds into Postgres (idempotent upserts)."""

from __future__ import annotations

from sqlalchemy import Connection, text

from prediction_engine.ingestion.football_data_uk import ParsedMatch
from prediction_engine.leagues import League

UPSERT_MATCH = text(
    """
    INSERT INTO matches (
        league_code, season, match_date, kickoff_time, home_team_id, away_team_id,
        home_goals, away_goals, result,
        ht_home_goals, ht_away_goals, home_shots, away_shots,
        home_shots_target, away_shots_target, home_corners, away_corners,
        home_yellow, away_yellow, home_red, away_red, source
    ) VALUES (
        :league_code, :season, :match_date, CAST(:kickoff_time AS TIME), :home_team_id, :away_team_id,
        :home_goals, :away_goals, :result,
        :ht_home_goals, :ht_away_goals, :home_shots, :away_shots,
        :home_shots_target, :away_shots_target, :home_corners, :away_corners,
        :home_yellow, :away_yellow, :home_red, :away_red, :source
    )
    ON CONFLICT (league_code, match_date, home_team_id, away_team_id) DO UPDATE SET
        season = EXCLUDED.season,
        kickoff_time = COALESCE(EXCLUDED.kickoff_time, matches.kickoff_time),
        home_goals = EXCLUDED.home_goals,
        away_goals = EXCLUDED.away_goals,
        result = EXCLUDED.result,
        ht_home_goals = COALESCE(EXCLUDED.ht_home_goals, matches.ht_home_goals),
        ht_away_goals = COALESCE(EXCLUDED.ht_away_goals, matches.ht_away_goals),
        home_shots = COALESCE(EXCLUDED.home_shots, matches.home_shots),
        away_shots = COALESCE(EXCLUDED.away_shots, matches.away_shots),
        home_shots_target = COALESCE(EXCLUDED.home_shots_target, matches.home_shots_target),
        away_shots_target = COALESCE(EXCLUDED.away_shots_target, matches.away_shots_target),
        home_corners = COALESCE(EXCLUDED.home_corners, matches.home_corners),
        away_corners = COALESCE(EXCLUDED.away_corners, matches.away_corners),
        home_yellow = COALESCE(EXCLUDED.home_yellow, matches.home_yellow),
        away_yellow = COALESCE(EXCLUDED.away_yellow, matches.away_yellow),
        home_red = COALESCE(EXCLUDED.home_red, matches.home_red),
        away_red = COALESCE(EXCLUDED.away_red, matches.away_red),
        updated_at = now()
    RETURNING id
    """
)

UPSERT_ODDS = text(
    """
    INSERT INTO match_odds (match_id, bookmaker, phase, market, outcome, price)
    VALUES (:match_id, :bookmaker, :phase, :market, :outcome, :price)
    ON CONFLICT (match_id, bookmaker, phase, market, outcome)
    DO UPDATE SET price = EXCLUDED.price
    """
)


def _team_ids(conn: Connection, names: set[str], country: str) -> dict[str, int]:
    if not names:
        return {}
    conn.execute(
        text(
            "INSERT INTO teams (name, country) VALUES (:name, :country) "
            "ON CONFLICT (name, country) DO NOTHING"
        ),
        [{"name": n, "country": country} for n in sorted(names)],
    )
    rows = conn.execute(
        text("SELECT name, id FROM teams WHERE country = :country AND name = ANY(:names)"),
        {"country": country, "names": list(names)},
    )
    return {name: team_id for name, team_id in rows}


def save_matches(conn: Connection, league: League, matches: list[ParsedMatch]) -> tuple[int, int]:
    """Upsert matches and their odds. Returns (matches_saved, odds_saved)."""
    if not matches:
        return 0, 0

    # football-data files occasionally repeat a row; keep the last one
    unique: dict[tuple, ParsedMatch] = {}
    for m in matches:
        unique[(m.match_date, m.home_team, m.away_team)] = m
    matches = list(unique.values())

    teams = _team_ids(
        conn, {m.home_team for m in matches} | {m.away_team for m in matches}, league.country
    )

    odds_rows: list[dict] = []
    for m in matches:
        params = {
            "league_code": league.code,
            "season": m.season,
            "match_date": m.match_date,
            "kickoff_time": m.kickoff_time,
            "home_team_id": teams[m.home_team],
            "away_team_id": teams[m.away_team],
            "home_goals": m.home_goals,
            "away_goals": m.away_goals,
            "result": m.result,
            "source": league.source,
            **{k: m.stats.get(k) for k in _STAT_KEYS},
        }
        match_id = conn.execute(UPSERT_MATCH, params).scalar_one()
        odds_rows.extend(
            {
                "match_id": match_id,
                "bookmaker": bookmaker,
                "phase": phase,
                "market": market,
                "outcome": outcome,
                "price": price,
            }
            for (bookmaker, phase, market, outcome), price in m.odds.items()
        )

    if odds_rows:
        conn.execute(UPSERT_ODDS, odds_rows)
    return len(matches), len(odds_rows)


_STAT_KEYS = (
    "ht_home_goals",
    "ht_away_goals",
    "home_shots",
    "away_shots",
    "home_shots_target",
    "away_shots_target",
    "home_corners",
    "away_corners",
    "home_yellow",
    "away_yellow",
    "home_red",
    "away_red",
)
