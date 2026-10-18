import numpy as np
import pandas as pd
import pytest
from scipy.stats import poisson

from prediction_engine.backtest import BacktestConfig, run_backtest
from prediction_engine.data import odds_column
from prediction_engine.models.dixon_coles import DixonColesModel

N_TEAMS = 20
TRUE_HOME_ADV = 0.25


def simulate_league(seasons: int = 4, seed: int = 7) -> tuple[pd.DataFrame, dict]:
    rng = np.random.default_rng(seed)
    teams = [f"Team{i:02d}" for i in range(N_TEAMS)]
    attack = dict(zip(teams, rng.normal(0, 0.3, N_TEAMS)))
    defense = dict(zip(teams, rng.normal(0, 0.3, N_TEAMS)))
    rows, match_id = [], 0
    for s in range(seasons):
        start = pd.Timestamp(f"{2020 + s}-08-15")
        fixtures = [(h, a) for h in teams for a in teams if h != a]
        rng.shuffle(fixtures)
        for k, (h, a) in enumerate(fixtures):
            lam = np.exp(0.1 + TRUE_HOME_ADV + attack[h] + defense[a])
            mu = np.exp(0.1 + attack[a] + defense[h])
            hg, ag = rng.poisson(lam), rng.poisson(mu)
            # bookmaker odds = true probabilities + 5% margin
            goals = np.arange(11)
            m = np.outer(poisson.pmf(goals, lam), poisson.pmf(goals, mu))
            total = goals[:, None] + goals[None, :]
            probs = {
                "home": np.tril(m, -1).sum(),
                "draw": np.trace(m),
                "away": np.triu(m, 1).sum(),
                "over": m[total > 2.5].sum(),
                "under": m[total < 2.5].sum(),
            }
            match_id += 1
            row = {
                "id": match_id,
                "league_code": "SIM",
                "season": f"{2020 + s}-{2021 + s}",
                "match_date": start + pd.Timedelta(days=int(k * 270 / len(fixtures))),
                "home_team": h,
                "away_team": a,
                "home_goals": hg,
                "away_goals": ag,
            }
            for outcome, p in probs.items():
                row[odds_column("market_avg", "closing", outcome)] = round(1 / (p * 1.05), 3)
            rows.append(row)
    return pd.DataFrame(rows), {"attack": attack, "defense": defense}


@pytest.fixture(scope="module")
def league():
    return simulate_league()


def test_fit_recovers_parameters(league):
    df, truth = league
    model = DixonColesModel(decay_per_day=0.0).fit(df, as_of=df["match_date"].max())
    assert abs(model.home_advantage - TRUE_HOME_ADV) < 0.08
    est = np.array([model.attack[t] - model.defense[t] for t in sorted(truth["attack"])])
    true = np.array([truth["attack"][t] - truth["defense"][t] for t in sorted(truth["attack"])])
    assert np.corrcoef(est, true)[0, 1] > 0.9


def test_prediction_probabilities_are_consistent(league):
    df, _ = league
    model = DixonColesModel().fit(df, as_of=df["match_date"].max())
    pred = model.predict("Team00", "Team01")
    p = pred.probs
    assert pred.score_matrix.sum() == pytest.approx(1.0)
    assert p["home"] + p["draw"] + p["away"] == pytest.approx(1.0)
    assert p["over_2_5"] + p["under_2_5"] == pytest.approx(1.0)
    assert len(pred.top_scores(3)) == 3


def test_unknown_team_gets_weak_rating(league):
    df, _ = league
    model = DixonColesModel().fit(df, as_of=df["match_date"].max())
    newcomer = model.predict("Team00", "Promoted FC").probs["home"]
    average = np.mean([model.predict("Team00", t).probs["home"] for t in model.teams if t != "Team00"])
    assert newcomer > average


def test_backtest_runs_and_market_is_hard_to_beat(league):
    df, _ = league
    cfg = BacktestConfig(test_seasons=["2023-2024"], min_edge=0.05)
    preds, bets, summary = run_backtest({"SIM": df}, cfg)
    assert not preds.empty
    assert set(summary["market"]) == {"1x2", "over_under_2_5"}
    one_x_two = summary.set_index("market").loc["1x2"]
    # market odds here come from the TRUE probabilities, so the model can't beat them
    assert one_x_two["model_logloss"] >= one_x_two["market_logloss"] - 0.01
    assert one_x_two["model_logloss"] < one_x_two["market_logloss"] + 0.05
    # no future data leaks: every prediction week has history before it
    assert preds["match_date"].min() >= pd.Timestamp("2023-08-15")
