"""Walk-forward backtest.

For every week of the test seasons the model is fitted ONLY with matches
played before that week (no peeking into the future), then it predicts the
week's matches. Predictions are compared with the bookmakers' odds:

  * Accuracy  -> log-loss / Brier of the model vs. the market's own
                 (margin-free) probabilities. Lower is better. If the model
                 is not close to the market here, value bets will not work.
  * Profit    -> flat 1-unit bets whenever  probability * odds - 1 >= min_edge.
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np
import pandas as pd

from prediction_engine.data import odds_column
from prediction_engine.models.dixon_coles import DixonColesModel

MARKETS: dict[str, list[str]] = {
    "1x2": ["home", "draw", "away"],
    "over_under_2_5": ["over", "under"],
}
# model probability keys for each outcome
PROB_KEY = {
    "home": "home",
    "draw": "draw",
    "away": "away",
    "over": "over_2_5",
    "under": "under_2_5",
}


@dataclass
class BacktestConfig:
    test_seasons: list[str]
    bookmaker: str = "market_avg"
    phase: str = "closing"
    min_edge: float = 0.05
    max_odds: float = 6.0
    history_days: int = 3 * 365
    min_history_matches: int = 200
    decay_per_day: float = 0.0019


def _actual_outcomes(row: pd.Series) -> dict[str, int]:
    hg, ag = row["home_goals"], row["away_goals"]
    return {
        "home": int(hg > ag),
        "draw": int(hg == ag),
        "away": int(hg < ag),
        "over": int(hg + ag > 2.5),
        "under": int(hg + ag < 2.5),
    }


def _odds_with_fallback(row: pd.Series, cfg: BacktestConfig, outcome: str) -> float:
    """Requested odds; if missing, closing market average, then opening average."""
    for bookmaker, phase in ((cfg.bookmaker, cfg.phase), ("market_avg", "closing"), ("market_avg", "opening")):
        value = row.get(odds_column(bookmaker, phase, outcome))
        if value is not None and not pd.isna(value):
            return float(value)
    return np.nan


def predict_walk_forward(matches: pd.DataFrame, cfg: BacktestConfig) -> pd.DataFrame:
    """One league at a time. Returns one row per (match, market, outcome)."""
    matches = matches.dropna(subset=["home_goals", "away_goals"]).sort_values("match_date")
    test = matches[matches["season"].isin(cfg.test_seasons)]
    if test.empty:
        return pd.DataFrame()

    rows: list[dict] = []
    week_starts = test["match_date"].dt.to_period("W").dt.start_time
    for week_start, week in test.groupby(week_starts):
        history = matches[
            (matches["match_date"] < week_start)
            & (matches["match_date"] >= week_start - pd.Timedelta(days=cfg.history_days))
        ]
        if len(history) < cfg.min_history_matches:
            continue
        model = DixonColesModel(decay_per_day=cfg.decay_per_day).fit(history, as_of=week_start)

        for _, match in week.iterrows():
            pred = model.predict(match["home_team"], match["away_team"])
            actual = _actual_outcomes(match)
            for market, outcomes in MARKETS.items():
                odds = {o: _odds_with_fallback(match, cfg, o) for o in outcomes}
                if any(np.isnan(v) for v in odds.values()):
                    continue
                inv = {o: 1 / v for o, v in odds.items()}
                overround = sum(inv.values())
                for outcome in outcomes:
                    p = pred.probs[PROB_KEY[outcome]]
                    rows.append(
                        {
                            "match_id": match["id"],
                            "league_code": match["league_code"],
                            "season": match["season"],
                            "match_date": match["match_date"],
                            "home_team": match["home_team"],
                            "away_team": match["away_team"],
                            "score": f"{int(match['home_goals'])}-{int(match['away_goals'])}",
                            "market": market,
                            "outcome": outcome,
                            "model_prob": p,
                            "market_prob": inv[outcome] / overround,
                            "odds": odds[outcome],
                            "edge": p * odds[outcome] - 1,
                            "won": actual[outcome],
                        }
                    )
    return pd.DataFrame(rows)


def select_bets(preds: pd.DataFrame, cfg: BacktestConfig) -> pd.DataFrame:
    """At most one bet per match and market: the outcome with the biggest edge."""
    if preds.empty:
        return preds
    candidates = preds[(preds["edge"] >= cfg.min_edge) & (preds["odds"] <= cfg.max_odds)]
    if candidates.empty:
        return candidates
    best = candidates.sort_values("edge", ascending=False).drop_duplicates(["match_id", "market"])
    best = best.copy()
    best["profit"] = np.where(best["won"] == 1, best["odds"] - 1, -1.0)
    return best.sort_values("match_date")


def summarize(preds: pd.DataFrame, bets: pd.DataFrame) -> pd.DataFrame:
    """One row per market with accuracy and betting results."""
    out = []
    for market in MARKETS:
        p = preds[preds["market"] == market]
        if p.empty:
            continue
        eps = 1e-12
        winners = p[p["won"] == 1]
        b = bets[bets["market"] == market] if not bets.empty else bets
        n_bets = len(b)
        profit = float(b["profit"].sum()) if n_bets else 0.0
        out.append(
            {
                "market": market,
                "matches": p["match_id"].nunique(),
                "model_logloss": float(-np.log(winners["model_prob"] + eps).mean()),
                "market_logloss": float(-np.log(winners["market_prob"] + eps).mean()),
                "model_brier": float(((p["model_prob"] - p["won"]) ** 2).groupby(p["match_id"]).sum().mean()),
                "market_brier": float(((p["market_prob"] - p["won"]) ** 2).groupby(p["match_id"]).sum().mean()),
                "bets": n_bets,
                "hit_rate": float(b["won"].mean()) if n_bets else np.nan,
                "avg_odds": float(b["odds"].mean()) if n_bets else np.nan,
                "profit_units": profit,
                "roi": profit / n_bets if n_bets else np.nan,
            }
        )
    return pd.DataFrame(out)


def run_backtest(matches_by_league: dict[str, pd.DataFrame], cfg: BacktestConfig):
    all_preds = [predict_walk_forward(df, cfg) for df in matches_by_league.values()]
    all_preds = [p for p in all_preds if not p.empty]
    preds = pd.concat(all_preds, ignore_index=True) if all_preds else pd.DataFrame()
    bets = select_bets(preds, cfg)
    return preds, bets, summarize(preds, bets) if not preds.empty else pd.DataFrame()
