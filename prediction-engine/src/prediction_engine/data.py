"""Read matches + odds from the database into pandas."""

from __future__ import annotations

import pandas as pd
from sqlalchemy import text

from prediction_engine.db import get_engine

MATCHES_SQL = text(
    """
    SELECT m.id, m.league_code, m.season, m.match_date,
           ht.name AS home_team, at.name AS away_team,
           m.home_goals, m.away_goals, m.result
    FROM matches m
    JOIN teams ht ON ht.id = m.home_team_id
    JOIN teams at ON at.id = m.away_team_id
    WHERE m.league_code = ANY(:leagues)
    ORDER BY m.match_date, m.id
    """
)

ODDS_SQL = text(
    """
    SELECT o.match_id, o.bookmaker, o.phase, o.market, o.outcome, o.price
    FROM match_odds o
    JOIN matches m ON m.id = o.match_id
    WHERE m.league_code = ANY(:leagues)
    """
)


def odds_column(bookmaker: str, phase: str, outcome: str) -> str:
    """e.g. ('market_avg', 'closing', 'home') -> 'odds_market_avg_closing_home'."""
    return f"odds_{bookmaker}_{phase}_{outcome}"


def load_matches_with_odds(leagues: list[str]) -> pd.DataFrame:
    engine = get_engine()
    with engine.connect() as conn:
        matches = pd.read_sql(MATCHES_SQL, conn, params={"leagues": leagues})
        odds = pd.read_sql(ODDS_SQL, conn, params={"leagues": leagues})

    matches["match_date"] = pd.to_datetime(matches["match_date"])
    if odds.empty:
        return matches

    odds["price"] = odds["price"].astype(float)
    odds["column"] = "odds_" + odds["bookmaker"] + "_" + odds["phase"] + "_" + odds["outcome"]
    wide = odds.pivot_table(index="match_id", columns="column", values="price", aggfunc="last")
    return matches.merge(wide, left_on="id", right_index=True, how="left")
