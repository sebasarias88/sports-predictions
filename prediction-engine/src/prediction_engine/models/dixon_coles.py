"""Dixon-Coles goal model.

Each team has an attack and a defense rating. Expected goals:

    home_xg = exp(intercept + home_advantage + attack[home] + defense[away])
    away_xg = exp(intercept + attack[away] + defense[home])

Goals follow a Poisson distribution, with the Dixon-Coles correction (rho)
for low scores (0-0, 1-0, 0-1, 1-1). Recent matches weigh more (time decay).

Fitting is done in two stages for speed:
  1. attack / defense / home advantage by weighted Poisson maximum likelihood
     (analytic gradient, L-BFGS) with a small ridge penalty that keeps teams
     with little data close to average;
  2. rho by 1-D maximum likelihood with the ratings fixed.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date

import numpy as np
import pandas as pd
from scipy.optimize import minimize, minimize_scalar
from scipy.stats import poisson

MAX_GOALS = 10
DEFAULT_DECAY_PER_DAY = 0.0019  # half-life ~ 1 year (Dixon & Coles 1997)


@dataclass
class MatchPrediction:
    home_xg: float
    away_xg: float
    score_matrix: np.ndarray  # [home_goals, away_goals] -> probability
    probs: dict[str, float] = field(default_factory=dict)

    def top_scores(self, n: int = 5) -> list[tuple[str, float]]:
        flat = np.argsort(self.score_matrix, axis=None)[::-1][:n]
        rows, cols = np.unravel_index(flat, self.score_matrix.shape)
        return [(f"{r}-{c}", float(self.score_matrix[r, c])) for r, c in zip(rows, cols)]


def _tau(home_goals: np.ndarray, away_goals: np.ndarray, lam: np.ndarray, mu: np.ndarray, rho: float):
    tau = np.ones_like(lam)
    m00 = (home_goals == 0) & (away_goals == 0)
    m01 = (home_goals == 0) & (away_goals == 1)
    m10 = (home_goals == 1) & (away_goals == 0)
    m11 = (home_goals == 1) & (away_goals == 1)
    tau[m00] = 1 - lam[m00] * mu[m00] * rho
    tau[m01] = 1 + lam[m01] * rho
    tau[m10] = 1 + mu[m10] * rho
    tau[m11] = 1 - rho
    return tau


def markets_from_matrix(matrix: np.ndarray) -> dict[str, float]:
    goals = np.arange(matrix.shape[0])
    total = goals[:, None] + goals[None, :]
    probs = {
        "home": float(np.tril(matrix, -1).sum()),
        "draw": float(np.trace(matrix)),
        "away": float(np.triu(matrix, 1).sum()),
        "btts_yes": float(matrix[1:, 1:].sum()),
    }
    probs["btts_no"] = 1 - probs["btts_yes"]
    for line in (0.5, 1.5, 2.5, 3.5, 4.5):
        over = float(matrix[total > line].sum())
        key = str(line).replace(".", "_")
        probs[f"over_{key}"] = over
        probs[f"under_{key}"] = 1 - over
    return probs


class DixonColesModel:
    def __init__(self, decay_per_day: float = DEFAULT_DECAY_PER_DAY, ridge: float = 1.0):
        self.decay_per_day = decay_per_day
        self.ridge = ridge
        self.teams: list[str] = []
        self.attack: dict[str, float] = {}
        self.defense: dict[str, float] = {}
        self.intercept = 0.0
        self.home_advantage = 0.0
        self.rho = 0.0
        self._fallback = (0.0, 0.0)

    # ------------------------------------------------------------------ fit
    def fit(self, matches: pd.DataFrame, as_of: date | pd.Timestamp) -> "DixonColesModel":
        """matches: columns match_date, home_team, away_team, home_goals, away_goals."""
        df = matches.dropna(subset=["home_goals", "away_goals"])
        if len(df) < 30:
            raise ValueError("Not enough matches to fit the model")

        self.teams = sorted(set(df["home_team"]) | set(df["away_team"]))
        idx = {t: i for i, t in enumerate(self.teams)}
        n = len(self.teams)
        h = df["home_team"].map(idx).to_numpy()
        a = df["away_team"].map(idx).to_numpy()
        hg = df["home_goals"].to_numpy(dtype=float)
        ag = df["away_goals"].to_numpy(dtype=float)
        days_ago = (pd.Timestamp(as_of) - pd.to_datetime(df["match_date"])).dt.days.to_numpy()
        w = np.exp(-self.decay_per_day * np.clip(days_ago, 0, None))
        w = w / w.mean()
        ridge = self.ridge

        def objective(x: np.ndarray):
            c, home = x[0], x[1]
            att, dfn = x[2 : 2 + n], x[2 + n :]
            eta_h = c + home + att[h] + dfn[a]
            eta_a = c + att[a] + dfn[h]
            lam, mu = np.exp(eta_h), np.exp(eta_a)
            nll = np.sum(w * (lam - hg * eta_h)) + np.sum(w * (mu - ag * eta_a))
            nll += 0.5 * ridge * (att @ att + dfn @ dfn)

            r_h = w * (lam - hg)
            r_a = w * (mu - ag)
            g_att = np.bincount(h, r_h, n) + np.bincount(a, r_a, n) + ridge * att
            g_def = np.bincount(a, r_h, n) + np.bincount(h, r_a, n) + ridge * dfn
            grad = np.concatenate([[r_h.sum() + r_a.sum(), r_h.sum()], g_att, g_def])
            return nll, grad

        x0 = np.zeros(2 + 2 * n)
        x0[0] = np.log(max(np.average(np.concatenate([hg, ag])), 0.1))
        res = minimize(objective, x0, jac=True, method="L-BFGS-B")
        x = res.x
        self.intercept, self.home_advantage = float(x[0]), float(x[1])
        att, dfn = x[2 : 2 + n], x[2 + n :]
        self.attack = dict(zip(self.teams, att.tolist()))
        self.defense = dict(zip(self.teams, dfn.tolist()))

        # Unknown teams (e.g. just promoted) -> average of the 3 weakest teams
        strength = att - dfn
        weakest = np.argsort(strength)[:3]
        self._fallback = (float(att[weakest].mean()), float(dfn[weakest].mean()))

        # Stage 2: rho
        lam = np.exp(self.intercept + self.home_advantage + att[h] + dfn[a])
        mu = np.exp(self.intercept + att[a] + dfn[h])
        low = (hg <= 1) & (ag <= 1)

        def rho_nll(rho: float) -> float:
            tau = _tau(hg[low], ag[low], lam[low], mu[low], rho)
            if np.any(tau <= 0):
                return 1e10
            return -np.sum(w[low] * np.log(tau))

        self.rho = float(minimize_scalar(rho_nll, bounds=(-0.25, 0.25), method="bounded").x)
        return self

    # -------------------------------------------------------------- predict
    def _rating(self, team: str) -> tuple[float, float]:
        if team in self.attack:
            return self.attack[team], self.defense[team]
        return self._fallback

    def expected_goals(self, home_team: str, away_team: str) -> tuple[float, float]:
        att_h, def_h = self._rating(home_team)
        att_a, def_a = self._rating(away_team)
        home_xg = np.exp(self.intercept + self.home_advantage + att_h + def_a)
        away_xg = np.exp(self.intercept + att_a + def_h)
        return float(home_xg), float(away_xg)

    def predict(self, home_team: str, away_team: str) -> MatchPrediction:
        home_xg, away_xg = self.expected_goals(home_team, away_team)
        goals = np.arange(MAX_GOALS + 1)
        matrix = np.outer(poisson.pmf(goals, home_xg), poisson.pmf(goals, away_xg))
        rho = self.rho
        matrix[0, 0] *= 1 - home_xg * away_xg * rho
        matrix[0, 1] *= 1 + home_xg * rho
        matrix[1, 0] *= 1 + away_xg * rho
        matrix[1, 1] *= 1 - rho
        matrix = np.clip(matrix, 0, None)
        matrix /= matrix.sum()
        return MatchPrediction(home_xg, away_xg, matrix, markets_from_matrix(matrix))
