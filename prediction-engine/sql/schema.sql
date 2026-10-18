-- Idempotent schema: safe to run many times (`uv run predict init-db`).

CREATE TABLE IF NOT EXISTS leagues (
    code        TEXT PRIMARY KEY,          -- e.g. SP1, I1, BRA
    name        TEXT NOT NULL,
    country     TEXT NOT NULL,
    tier        SMALLINT NOT NULL,         -- data-quality tier (1 = best)
    source      TEXT NOT NULL              -- football_data_uk_main | football_data_uk_extra | api_football
);

CREATE TABLE IF NOT EXISTS teams (
    id          SERIAL PRIMARY KEY,
    name        TEXT NOT NULL,
    country     TEXT NOT NULL,
    UNIQUE (name, country)
);

CREATE TABLE IF NOT EXISTS matches (
    id                  BIGSERIAL PRIMARY KEY,
    league_code         TEXT NOT NULL REFERENCES leagues(code),
    season              TEXT NOT NULL,     -- "2025-2026" or "2025" for calendar-year leagues
    match_date          DATE NOT NULL,
    kickoff_time        TIME,
    home_team_id        INT NOT NULL REFERENCES teams(id),
    away_team_id        INT NOT NULL REFERENCES teams(id),
    home_goals          SMALLINT,
    away_goals          SMALLINT,
    result              CHAR(1),           -- H / D / A
    ht_home_goals       SMALLINT,
    ht_away_goals       SMALLINT,
    home_shots          SMALLINT,
    away_shots          SMALLINT,
    home_shots_target   SMALLINT,
    away_shots_target   SMALLINT,
    home_corners        SMALLINT,
    away_corners        SMALLINT,
    home_yellow         SMALLINT,
    away_yellow         SMALLINT,
    home_red            SMALLINT,
    away_red            SMALLINT,
    source              TEXT NOT NULL,
    updated_at          TIMESTAMPTZ NOT NULL DEFAULT now(),
    UNIQUE (league_code, match_date, home_team_id, away_team_id)
);

CREATE INDEX IF NOT EXISTS idx_matches_league_season ON matches (league_code, season);
CREATE INDEX IF NOT EXISTS idx_matches_date ON matches (match_date);

-- Long format: one row per (match, bookmaker, phase, market, outcome)
CREATE TABLE IF NOT EXISTS match_odds (
    match_id    BIGINT NOT NULL REFERENCES matches(id) ON DELETE CASCADE,
    bookmaker   TEXT NOT NULL,             -- bet365 | pinnacle | market_avg | market_max
    phase       TEXT NOT NULL,             -- opening | closing
    market      TEXT NOT NULL,             -- 1x2 | over_under_2_5
    outcome     TEXT NOT NULL,             -- home | draw | away | over | under
    price       NUMERIC(8, 3) NOT NULL,
    PRIMARY KEY (match_id, bookmaker, phase, market, outcome)
);
