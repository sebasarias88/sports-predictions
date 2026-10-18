from datetime import date

from prediction_engine.ingestion import football_data_uk as fd

MAIN_CSV = (
    "﻿Div,Date,Time,HomeTeam,AwayTeam,FTHG,FTAG,FTR,HTHG,HTAG,HTR,HS,AS,HST,AST,HC,AC,"
    "HY,AY,HR,AR,B365H,B365D,B365A,PSH,PSD,PSA,AvgH,AvgD,AvgA,B365>2.5,B365<2.5,"
    "Avg>2.5,Avg<2.5,AvgCH,AvgCD,AvgCA,AvgC>2.5,AvgC<2.5\n"
    "SP1,15/08/2025,20:00,Girona,Vallecano,1,3,A,0,2,A,10,12,3,6,5,4,"
    "2,1,0,0,2.10,3.40,3.50,2.15,3.45,3.60,2.08,3.38,3.52,2.00,1.80,"
    "2.02,1.82,2.20,3.30,3.40,1.95,1.88\n"
    "SP1,16/08/2025,21:30,Barcelona,Mallorca,3,0,H,2,0,H,20,5,9,1,8,2,"
    "1,3,0,1,1.20,7.00,13.00,,,,1.21,6.80,12.50,1.40,2.90,"
    "1.41,2.85,,,,,\n"
    ",,,,,,,,,,,,,,,,,,,,,,,,,,,,,,,,,,,,,,\n"
)

OLD_MAIN_CSV = (
    "Div,Date,HomeTeam,AwayTeam,FTHG,FTAG,FTR,BbAvH,BbAvD,BbAvA,BbAv>2.5,BbAv<2.5\n"
    "I1,22/08/15,Verona,Roma,1,1,D,4.50,3.60,1.80,1.90,1.90\n"
)

EXTRA_CSV = (
    "Country,League,Season,Date,Time,Home,Away,HG,AG,Res,PSCH,PSCD,PSCA,"
    "MaxCH,MaxCD,MaxCA,AvgCH,AvgCD,AvgCA\n"
    "Brazil,Serie A,2014,19/04/2014,21:00,Flamengo,Goias,0,0,D,1.70,3.70,5.60,"
    "1.75,3.80,5.90,1.65,3.55,5.20\n"
    "Brazil,Serie A,2025,29/03/2025,18:30,Palmeiras,Botafogo RJ,0,0,D,2.10,3.30,3.80,"
    "2.20,3.40,3.90,2.05,3.25,3.70\n"
)


def test_season_helpers():
    assert fd.season_code(2025) == "2526"
    assert fd.season_code(2099) == "9900"
    assert fd.season_label(2025) == "2025-2026"
    assert fd.current_season_start(date(2026, 10, 18)) == 2026
    assert fd.current_season_start(date(2027, 3, 1)) == 2026


def test_parse_main_league_recent_format():
    df = fd.read_csv_bytes(MAIN_CSV.encode("utf-8"))
    matches = fd.parse_main_league(df, "2025-2026")

    assert len(matches) == 2  # empty trailing row is ignored
    girona = matches[0]
    assert girona.match_date == date(2025, 8, 15)
    assert girona.kickoff_time == "20:00"
    assert (girona.home_team, girona.away_team) == ("Girona", "Vallecano")
    assert (girona.home_goals, girona.away_goals, girona.result) == (1, 3, "A")
    assert girona.stats["home_shots_target"] == 3
    assert girona.odds[("bet365", "opening", "1x2", "home")] == 2.10
    assert girona.odds[("pinnacle", "opening", "1x2", "away")] == 3.60
    assert girona.odds[("market_avg", "closing", "1x2", "draw")] == 3.30
    assert girona.odds[("market_avg", "closing", "over_under_2_5", "over")] == 1.95

    barca = matches[1]
    # Pinnacle and closing odds are empty for this row -> not stored
    assert not any(k[0] == "pinnacle" for k in barca.odds)
    assert not any(k[1] == "closing" for k in barca.odds)
    assert barca.stats["away_red"] == 1


def test_parse_main_league_legacy_columns():
    df = fd.read_csv_bytes(OLD_MAIN_CSV.encode("latin-1"))
    [match] = fd.parse_main_league(df, "2015-2016")
    assert match.match_date == date(2015, 8, 22)
    assert match.kickoff_time is None
    assert match.odds[("market_avg", "opening", "1x2", "away")] == 1.80
    assert match.odds[("market_avg", "opening", "over_under_2_5", "under")] == 1.90


def test_parse_extra_league_filters_by_season():
    df = fd.read_csv_bytes(EXTRA_CSV.encode("utf-8"))
    matches = fd.parse_extra_league(df, from_year=2015)
    assert len(matches) == 1
    m = matches[0]
    assert m.season == "2025"
    assert m.home_team == "Palmeiras"
    assert m.odds[("pinnacle", "closing", "1x2", "home")] == 2.10
    assert ("bet365", "closing", "1x2", "home") not in m.odds
