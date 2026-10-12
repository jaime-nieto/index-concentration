"""
Performance statistics of daily return series.
"""
import numpy as np
import pandas as pd

from . import config


def annual_return(returns):
    """Compound return per year."""
    years = len(returns) / config.TRADING_DAYS
    return float((1 + returns).prod() ** (1 / years) - 1)


def sharpe(excess):
    return float(excess.mean() / excess.std() * np.sqrt(config.TRADING_DAYS))


def drawdown(returns):
    wealth = (1 + returns).cumprod()
    return wealth / wealth.cummax() - 1


def performance(returns, risk_free):
    """Annualised statistics of a daily return series."""
    return {
        "annual_return": annual_return(returns),
        "volatility": float(returns.std() * np.sqrt(config.TRADING_DAYS)),
        "sharpe": sharpe(returns - risk_free.reindex(returns.index)),
        "max_drawdown": float(drawdown(returns).min()),
    }


def relative(returns, other):
    """How one portfolio moves against another: beta, tracking error, correlation."""
    diff = returns - other
    return {
        "beta": float(returns.cov(other) / other.var()),
        "tracking_error": float(diff.std() * np.sqrt(config.TRADING_DAYS)),
        "correlation": float(returns.corr(other)),
    }


def difference_interval(returns, other, risk_free, draws=config.BOOTSTRAP_DRAWS,
                        block=config.BOOTSTRAP_BLOCK, seed=0):
    """Annual return and Sharpe ratio of `returns` minus those of `other`, with 95% intervals.

    Two portfolios of the same shares move together, so the difference between
    them is known much better than each of them. The intervals come from a
    circular block bootstrap of the pairs of daily returns (blocks of `block`
    days keep the clustering of volatility): the days are redrawn many times
    and the differences are computed on each redraw. An interval that contains
    0 means that the sample cannot tell the two portfolios apart.
    """
    frame = pd.DataFrame({"a": returns, "b": other, "rf": risk_free.reindex(returns.index)}).dropna()
    a, b, rf = (frame[c].to_numpy() for c in ["a", "b", "rf"])
    n = len(a)
    years = n / config.TRADING_DAYS

    def growth(x):
        return np.exp(np.log1p(x).sum(axis=-1) / years) - 1

    def ratio(x):
        return x.mean(axis=-1) / x.std(axis=-1, ddof=1) * np.sqrt(config.TRADING_DAYS)

    rng = np.random.default_rng(seed)
    starts = rng.integers(0, n, (draws, n // block + 1))
    days = ((starts[:, :, None] + np.arange(block)) % n).reshape(draws, -1)[:, :n]
    out = {}
    for name, simulated, observed in [
            ("return", growth(a[days]) - growth(b[days]), growth(a) - growth(b)),
            ("sharpe", ratio((a - rf)[days]) - ratio((b - rf)[days]), ratio(a - rf) - ratio(b - rf))]:
        out[name] = {"difference": float(observed), "low": float(np.quantile(simulated, 0.025)),
                     "high": float(np.quantile(simulated, 0.975))}
    return out
