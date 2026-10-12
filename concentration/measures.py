"""
How concentrated a set of weights is, in money and in risk.
"""
import numpy as np
import pandas as pd


def effective_number(weights):
    """Number of equal positions that would be as concentrated: 1 / sum of squared weights."""
    w = np.asarray(weights, dtype=float)
    return float(1.0 / (w ** 2).sum())


def members_for_half(weights):
    """How many of the largest members add up to half of the index."""
    w = np.sort(np.asarray(weights, dtype=float))[::-1]
    return int(np.searchsorted(np.cumsum(w), 0.5 - 1e-12) + 1)


def concentration(weights):
    """Summary of a set of weights that add up to 1."""
    w = np.sort(np.asarray(weights, dtype=float))[::-1]
    return {"members": int((w > 0).sum()), "largest": float(w[0]), "top3": float(w[:3].sum()),
            "top5": float(w[:5].sum()), "top10": float(w[:10].sum()),
            "effective_number": effective_number(w), "members_for_half": members_for_half(w)}


def risk_shares(weights, returns):
    """Share of the variance of the portfolio that comes from each member (they add up to 1).

    For a portfolio with constant weights w over the window, the share of
    member i is w_i x cov(r_i, r_p) / var(r_p), where r_p is the return of the
    portfolio.
    """
    w = weights.reindex(returns.columns).fillna(0.0)
    r = returns.fillna(0.0)
    portfolio = r @ w
    centred = r - r.mean()
    covariance = centred.mul(portfolio - portfolio.mean(), axis=0).sum() / (len(r) - 1)
    return w * covariance / portfolio.var(ddof=1)


def diversification_ratio(weights, returns):
    """Weighted average volatility of the members divided by the volatility of the portfolio."""
    w = weights.reindex(returns.columns).fillna(0.0)
    r = returns.fillna(0.0)
    return float((w * r.std(ddof=1)).sum() / (r @ w).std(ddof=1))


def top_weight(weights, n):
    """Sum of the n largest weights on each day (days x members -> one number per day)."""
    values = np.sort(weights.to_numpy(), axis=1)[:, ::-1][:, :n].sum(axis=1)
    return pd.Series(values, index=weights.index)
