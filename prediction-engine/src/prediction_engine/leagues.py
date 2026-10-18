"""League catalog.

Tiers describe data quality, not league quality:
  1 = full stats + opening & closing odds (1X2 and over/under 2.5)
  2 = results + closing 1X2 odds only
  3 = no free historical file; comes from API-Football (not implemented yet)
"""

from dataclasses import dataclass
from typing import Literal

Source = Literal["football_data_uk_main", "football_data_uk_extra", "api_football"]


@dataclass(frozen=True)
class League:
    code: str
    name: str
    country: str
    tier: int
    source: Source


MAIN = "football_data_uk_main"
EXTRA = "football_data_uk_extra"
API = "api_football"

LEAGUES: tuple[League, ...] = (
    # Tier 1 — big European leagues
    League("SP1", "La Liga", "Spain", 1, MAIN),
    League("I1", "Serie A", "Italy", 1, MAIN),
    League("F1", "Ligue 1", "France", 1, MAIN),
    League("D1", "Bundesliga", "Germany", 1, MAIN),
    League("E0", "Premier League", "England", 1, MAIN),
    League("P1", "Primeira Liga", "Portugal", 1, MAIN),
    League("N1", "Eredivisie", "Netherlands", 1, MAIN),
    # Tier 1 — second divisions and other European leagues (same data quality)
    League("SP2", "Segunda Division", "Spain", 1, MAIN),
    League("I2", "Serie B", "Italy", 1, MAIN),
    League("F2", "Ligue 2", "France", 1, MAIN),
    League("D2", "2. Bundesliga", "Germany", 1, MAIN),
    League("E1", "Championship", "England", 1, MAIN),
    League("B1", "Jupiler Pro League", "Belgium", 1, MAIN),
    League("T1", "Super Lig", "Turkey", 1, MAIN),
    League("SC0", "Premiership", "Scotland", 1, MAIN),
    League("G1", "Super League", "Greece", 1, MAIN),
    # Tier 2 — calendar-year / other leagues (results + closing odds)
    League("BRA", "Serie A", "Brazil", 2, EXTRA),
    League("ARG", "Liga Profesional", "Argentina", 2, EXTRA),
    League("MEX", "Liga MX", "Mexico", 2, EXTRA),
    League("USA", "MLS", "USA", 2, EXTRA),
    League("AUT", "Bundesliga", "Austria", 2, EXTRA),
    League("SWZ", "Super League", "Switzerland", 2, EXTRA),
    League("DNK", "Superliga", "Denmark", 2, EXTRA),
    League("SWE", "Allsvenskan", "Sweden", 2, EXTRA),
    League("NOR", "Eliteserien", "Norway", 2, EXTRA),
    League("POL", "Ekstraklasa", "Poland", 2, EXTRA),
    League("JPN", "J1 League", "Japan", 2, EXTRA),
    # Tier 3 — API-Football (next phase)
    League("COL", "Primera A", "Colombia", 3, API),
    League("SAU", "Saudi Pro League", "Saudi Arabia", 3, API),
)

LEAGUES_BY_CODE = {lg.code: lg for lg in LEAGUES}


def select_leagues(codes: list[str] | None = None, tier: int | None = None) -> list[League]:
    if codes:
        unknown = [c for c in codes if c not in LEAGUES_BY_CODE]
        if unknown:
            raise ValueError(f"Unknown league codes: {', '.join(unknown)}")
        return [LEAGUES_BY_CODE[c] for c in codes]
    return [lg for lg in LEAGUES if tier is None or lg.tier == tier]
