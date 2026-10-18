"""Historical results and odds from football-data.co.uk (free CSV files).

Two file layouts exist:
  * main leagues  -> one CSV per league per season:  /mmz4281/2526/SP1.csv
  * extra leagues -> one CSV per league, all seasons: /new/BRA.csv

Column names changed over the years (e.g. BbAvH before 2019, AvgH after),
so every odds field lists candidate columns and the first one present wins.
"""

from __future__ import annotations

import io
import logging
import time
from dataclasses import dataclass, field
from datetime import date
from pathlib import Path

import httpx
import pandas as pd

from prediction_engine.leagues import League

log = logging.getLogger(__name__)

BASE_URL = "https://www.football-data.co.uk"
USER_AGENT = "prediction-engine/0.1 (personal research)"

# --------------------------------------------------------------------------- #
# Season helpers
# --------------------------------------------------------------------------- #


def current_season_start(today: date | None = None) -> int:
    """European seasons start in July/August."""
    today = today or date.today()
    return today.year if today.month >= 7 else today.year - 1


def season_code(start_year: int) -> str:
    """2025 -> '2526' (football-data.co.uk URL format)."""
    return f"{start_year % 100:02d}{(start_year + 1) % 100:02d}"


def season_label(start_year: int) -> str:
    """2025 -> '2025-2026' (stored in the database)."""
    return f"{start_year}-{start_year + 1}"


# --------------------------------------------------------------------------- #
# Odds column mapping
# --------------------------------------------------------------------------- #

# (bookmaker, phase) -> {outcome: [candidate columns]}
ODDS_1X2: dict[tuple[str, str], dict[str, list[str]]] = {
    ("bet365", "opening"): {"home": ["B365H"], "draw": ["B365D"], "away": ["B365A"]},
    ("bet365", "closing"): {"home": ["B365CH"], "draw": ["B365CD"], "away": ["B365CA"]},
    ("pinnacle", "opening"): {"home": ["PSH"], "draw": ["PSD"], "away": ["PSA"]},
    ("pinnacle", "closing"): {"home": ["PSCH"], "draw": ["PSCD"], "away": ["PSCA"]},
    ("market_avg", "opening"): {
        "home": ["AvgH", "BbAvH"],
        "draw": ["AvgD", "BbAvD"],
        "away": ["AvgA", "BbAvA"],
    },
    ("market_avg", "closing"): {"home": ["AvgCH"], "draw": ["AvgCD"], "away": ["AvgCA"]},
    ("market_max", "opening"): {
        "home": ["MaxH", "BbMxH"],
        "draw": ["MaxD", "BbMxD"],
        "away": ["MaxA", "BbMxA"],
    },
    ("market_max", "closing"): {"home": ["MaxCH"], "draw": ["MaxCD"], "away": ["MaxCA"]},
}

ODDS_OU25: dict[tuple[str, str], dict[str, list[str]]] = {
    ("bet365", "opening"): {"over": ["B365>2.5"], "under": ["B365<2.5"]},
    ("bet365", "closing"): {"over": ["B365C>2.5"], "under": ["B365C<2.5"]},
    ("pinnacle", "opening"): {"over": ["P>2.5"], "under": ["P<2.5"]},
    ("pinnacle", "closing"): {"over": ["PC>2.5"], "under": ["PC<2.5"]},
    ("market_avg", "opening"): {"over": ["Avg>2.5", "BbAv>2.5"], "under": ["Avg<2.5", "BbAv<2.5"]},
    ("market_avg", "closing"): {"over": ["AvgC>2.5"], "under": ["AvgC<2.5"]},
    ("market_max", "opening"): {"over": ["Max>2.5", "BbMx>2.5"], "under": ["Max<2.5", "BbMx<2.5"]},
    ("market_max", "closing"): {"over": ["MaxC>2.5"], "under": ["MaxC<2.5"]},
}

MARKETS = {"1x2": ODDS_1X2, "over_under_2_5": ODDS_OU25}

# Main-league match stats: db column -> csv column
STAT_COLUMNS = {
    "ht_home_goals": "HTHG",
    "ht_away_goals": "HTAG",
    "home_shots": "HS",
    "away_shots": "AS",
    "home_shots_target": "HST",
    "away_shots_target": "AST",
    "home_corners": "HC",
    "away_corners": "AC",
    "home_yellow": "HY",
    "away_yellow": "AY",
    "home_red": "HR",
    "away_red": "AR",
}


# --------------------------------------------------------------------------- #
# Parsed record
# --------------------------------------------------------------------------- #


@dataclass
class ParsedMatch:
    season: str
    match_date: date
    kickoff_time: str | None
    home_team: str
    away_team: str
    home_goals: int | None
    away_goals: int | None
    result: str | None
    stats: dict[str, int | None] = field(default_factory=dict)
    # (bookmaker, phase, market, outcome) -> price
    odds: dict[tuple[str, str, str, str], float] = field(default_factory=dict)


# --------------------------------------------------------------------------- #
# Parsing (pure functions, no network / database)
# --------------------------------------------------------------------------- #


def read_csv_bytes(raw: bytes) -> pd.DataFrame:
    for encoding in ("utf-8-sig", "latin-1"):
        try:
            df = pd.read_csv(io.BytesIO(raw), encoding=encoding, on_bad_lines="skip")
            break
        except UnicodeDecodeError:
            continue
    df.columns = [str(c).strip() for c in df.columns]
    return df.dropna(how="all")


def _parse_date(value: object) -> date | None:
    if value is None or (isinstance(value, float) and pd.isna(value)):
        return None
    text = str(value).strip()
    for fmt in ("%d/%m/%Y", "%d/%m/%y", "%Y-%m-%d"):
        try:
            return pd.to_datetime(text, format=fmt).date()
        except (ValueError, TypeError):
            continue
    return None


def _to_int(value: object) -> int | None:
    try:
        if value is None or pd.isna(value):
            return None
        return int(float(value))
    except (TypeError, ValueError):
        return None


def _to_price(value: object) -> float | None:
    try:
        if value is None or pd.isna(value):
            return None
        price = float(value)
    except (TypeError, ValueError):
        return None
    return price if price > 1.0 else None  # odds <= 1 are invalid


def _kickoff(value: object) -> str | None:
    if value is None or (isinstance(value, float) and pd.isna(value)):
        return None
    text = str(value).strip()
    return text if len(text) in (4, 5) and ":" in text else None


def _extract_odds(row: pd.Series, columns: set[str]) -> dict[tuple[str, str, str, str], float]:
    odds: dict[tuple[str, str, str, str], float] = {}
    for market, mapping in MARKETS.items():
        for (bookmaker, phase), outcomes in mapping.items():
            prices: dict[str, float] = {}
            for outcome, candidates in outcomes.items():
                col = next((c for c in candidates if c in columns), None)
                price = _to_price(row[col]) if col else None
                if price is not None:
                    prices[outcome] = price
            # keep a market only if every outcome has a price
            if len(prices) == len(outcomes):
                for outcome, price in prices.items():
                    odds[(bookmaker, phase, market, outcome)] = price
    return odds


def _normalize_result(value: object) -> str | None:
    text = str(value).strip().upper() if value is not None else ""
    return text if text in {"H", "D", "A"} else None


def parse_main_league(df: pd.DataFrame, season: str) -> list[ParsedMatch]:
    columns = set(df.columns)
    matches: list[ParsedMatch] = []
    for _, row in df.iterrows():
        match_date = _parse_date(row.get("Date"))
        home, away = row.get("HomeTeam"), row.get("AwayTeam")
        if match_date is None or pd.isna(home) or pd.isna(away):
            continue
        matches.append(
            ParsedMatch(
                season=season,
                match_date=match_date,
                kickoff_time=_kickoff(row.get("Time")),
                home_team=str(home).strip(),
                away_team=str(away).strip(),
                home_goals=_to_int(row.get("FTHG")),
                away_goals=_to_int(row.get("FTAG")),
                result=_normalize_result(row.get("FTR")),
                stats={
                    db_col: _to_int(row[csv_col]) if csv_col in columns else None
                    for db_col, csv_col in STAT_COLUMNS.items()
                },
                odds=_extract_odds(row, columns),
            )
        )
    return matches


def _extra_season_label(value: object) -> str:
    text = str(value).strip()
    return text.replace("/", "-")  # "2012/2013" -> "2012-2013"; "2024" stays "2024"


def _season_start_year(label: str) -> int:
    return int(label[:4])


def parse_extra_league(df: pd.DataFrame, from_year: int) -> list[ParsedMatch]:
    columns = set(df.columns)
    matches: list[ParsedMatch] = []
    for _, row in df.iterrows():
        season = _extra_season_label(row.get("Season"))
        if not season[:4].isdigit() or _season_start_year(season) < from_year:
            continue
        match_date = _parse_date(row.get("Date"))
        home, away = row.get("Home"), row.get("Away")
        if match_date is None or pd.isna(home) or pd.isna(away):
            continue
        matches.append(
            ParsedMatch(
                season=season,
                match_date=match_date,
                kickoff_time=_kickoff(row.get("Time")),
                home_team=str(home).strip(),
                away_team=str(away).strip(),
                home_goals=_to_int(row.get("HG")),
                away_goals=_to_int(row.get("AG")),
                result=_normalize_result(row.get("Res")),
                odds=_extract_odds(row, columns),
            )
        )
    return matches


# --------------------------------------------------------------------------- #
# Download with local cache
# --------------------------------------------------------------------------- #


def _download(url: str, cache_file: Path, force: bool) -> bytes | None:
    if cache_file.exists() and not force:
        return cache_file.read_bytes()
    for attempt in range(3):
        try:
            resp = httpx.get(
                url, timeout=30, follow_redirects=True, headers={"User-Agent": USER_AGENT}
            )
        except httpx.HTTPError as exc:
            log.warning("Download error %s (%s), retry %d", url, exc, attempt + 1)
            time.sleep(2 * (attempt + 1))
            continue
        if resp.status_code == 404:
            log.info("Not available: %s", url)
            return None
        resp.raise_for_status()
        cache_file.parent.mkdir(parents=True, exist_ok=True)
        cache_file.write_bytes(resp.content)
        time.sleep(1)  # be polite with a free service
        return resp.content
    raise RuntimeError(f"Could not download {url}")


def fetch_main_league(
    league: League, start_year: int, data_dir: Path, current_start: int
) -> list[ParsedMatch]:
    code = season_code(start_year)
    url = f"{BASE_URL}/mmz4281/{code}/{league.code}.csv"
    cache = data_dir / "raw" / "football_data_uk" / code / f"{league.code}.csv"
    # past seasons never change -> use cache; current season -> always refresh
    raw = _download(url, cache, force=start_year >= current_start)
    if raw is None:
        return []
    return parse_main_league(read_csv_bytes(raw), season_label(start_year))


def fetch_extra_league(league: League, from_year: int, data_dir: Path) -> list[ParsedMatch]:
    url = f"{BASE_URL}/new/{league.code}.csv"
    cache = data_dir / "raw" / "football_data_uk" / "extra" / f"{league.code}.csv"
    raw = _download(url, cache, force=True)  # one file holds every season
    if raw is None:
        return []
    return parse_extra_league(read_csv_bytes(raw), from_year)
