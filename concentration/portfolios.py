"""
The two portfolios that are compared, built from the same members.

Index weights
    Each member weighs what it weighs in the index. The index weighs companies
    by market value, so the weight of a share moves with its price. Going back
    from the day the weights are known, the weight on day t is

        w(t)  proportional to  w(last day) x price(t) / price(last day)

    (the number of shares is taken as constant). Nothing is ever traded: it is
    one purchase that is left alone. A company that was listed later comes in
    on its first day, as it does in the index.

Equal weight
    The same amount in every member that is trading, put back to equal on each
    rebalancing day. Between two of them the weights drift with prices.

Both are held with dividends reinvested.
"""
from dataclasses import dataclass

import numpy as np
import pandas as pd

from . import config


def index_weights(weights, close):
    """Weight of every member on every day (days x members), going back from the last day."""
    value = close / close.iloc[-1] * weights.reindex(close.columns)
    return value.div(value.sum(axis=1), axis=0).fillna(0.0)


def held_return(weights, returns):
    """Daily return of a portfolio that has `weights` at each close (the weights of the day before earn the return)."""
    return (weights.shift(1) * returns.fillna(0.0)).sum(axis=1).iloc[1:]


def rebalancing_days(calendar, months=None):
    """The first day of the calendar and the last trading day of each rebalancing month."""
    months = config.REBALANCE_MONTHS if months is None else months
    days = pd.Series(calendar, index=calendar)
    month_end = days.groupby([calendar.year, calendar.month]).last()
    chosen = [d for (_, month), d in month_end.items() if month in months and d < calendar[-1]]
    return pd.DatetimeIndex(sorted({calendar[0], *chosen}))


@dataclass
class Rebalanced:
    gross: pd.Series               # daily return before costs
    weights: pd.DataFrame          # weight of every member at each close
    trades: pd.DataFrame           # one row per rebalancing day: members held, share of the portfolio traded

    def net(self, cost_bps=config.COST_BPS):
        """Daily return after costs: each rebalance is paid on the day after it."""
        net = self.gross.copy()
        paid = self.trades.iloc[1:]                         # the first purchase is not charged (nor is it in the index)
        days = net.index[np.searchsorted(net.index, paid.index, side="right").clip(max=len(net) - 1)]
        net.loc[days] = (1 + net.loc[days]) * (1 - paid["traded"].to_numpy() * cost_bps / 1e4) - 1
        return net

    def turnover_per_year(self):
        """One-way turnover per year: half of what is traded, without the first purchase."""
        years = len(self.gross) / config.TRADING_DAYS
        return self.trades["traded"].iloc[1:].sum() / 2 / years


def rebalanced(returns, close, target=None, months=None):
    """Portfolio put back to its target on each rebalancing day.

    `target`: raw weight of each member (None = the same for all). On each
    rebalancing day it is shared out among the members that have a price that
    day. `returns` and `close` start on the day of the first purchase.
    """
    calendar = returns.index
    days = rebalancing_days(calendar, months)
    raw = pd.Series(1.0, index=returns.columns) if target is None else target.reindex(returns.columns).fillna(0.0)
    filled = returns.fillna(0.0).to_numpy()
    position = {d: i for i, d in enumerate(calendar)}

    weights = np.zeros_like(filled)
    held = np.zeros(filled.shape[1])
    rows = []
    for k, day in enumerate(days):
        start = position[day]
        stop = position[days[k + 1]] if k + 1 < len(days) else len(calendar) - 1
        available = close.loc[day].notna().to_numpy()
        new = np.where(available, raw.to_numpy(), 0.0)
        new = new / new.sum()
        rows.append({"day": day, "members": int((new > 0).sum()), "traded": float(np.abs(new - held).sum())})
        growth = np.cumprod(1 + filled[start + 1:stop + 1], axis=0) * new
        weights[start] = new
        weights[start + 1:stop + 1] = growth / growth.sum(axis=1, keepdims=True)
        held = weights[stop]
    weights = pd.DataFrame(weights, index=calendar, columns=returns.columns)
    return Rebalanced(held_return(weights, returns), weights, pd.DataFrame(rows).set_index("day"))


def group_contributions(active, returns, order):
    """Return of one portfolio minus another, split by groups of members.

    `active`: weights of the first minus weights of the second, at each close.
    `order`: the members from the largest index weight to the smallest. The
    contribution of a group on a day is the sum of (difference of weight the
    day before) x (return of the member); the groups add up to the difference
    between the two daily returns.
    """
    daily = (active.shift(1) * returns.fillna(0.0)).iloc[1:]
    out = {}
    for name, first, last in config.GROUPS:
        out[name] = daily[order[first:last]].sum(axis=1)
    return pd.DataFrame(out)
