"""
From downloaded prices to clean daily returns, one panel per index.

Every correction is kept in a table so that it can be checked. The rules are
the same for every share of an index; the few corrections made by hand are
listed in config (SPIN_OFFS, WRONG_PRICES, WRONG_RETURNS, FIRST_DAY).
"""
from dataclasses import dataclass

import numpy as np
import pandas as pd

from . import config
from . import data_download as dd

LIMIT_WHY = "move beyond the daily price limit of the exchange; set to zero"


@dataclass
class Panel:
    key: str
    members: pd.DataFrame          # ticker -> name, sector, weight on AS_OF (adding up to 1)
    returns: pd.DataFrame          # daily returns with dividends (trading days x members; NaN before listing)
    price_returns: pd.DataFrame    # daily returns without dividends (what the holder earned from the price)
    close: pd.DataFrame            # price of each member, splits adjusted (moves with the value of the company)
    index: pd.Series               # the real index (no dividends)
    risk_free: pd.Series           # daily return of a 3-month deposit
    corrections: pd.DataFrame      # every return that was changed: date, ticker, source value, value used, why
    left_out: pd.DataFrame         # members that are not used: ticker, name, weight, why


def _log(mask, before, after, why):
    days = mask.stack()
    days = days[days].index
    return pd.DataFrame({"date": days.get_level_values(0), "ticker": days.get_level_values(1),
                         "source": [before.at[d, t] for d, t in days],
                         "used": [after.at[d, t] if isinstance(after, pd.DataFrame) else after for d, t in days],
                         "why": why})


def spikes(returns):
    """Days on which a price jumps and is back the next day (True on both days).

    A move of more than SPIKE followed by a move that leaves the price within
    SPIKE_BACK of where it started is a wrong price, not two returns.
    """
    two_days = (1 + returns) * (1 + returns.shift(-1)) - 1
    first = (returns.abs() > config.SPIKE) & (two_days.abs() < config.SPIKE_BACK)
    return first | first.shift(1, fill_value=False)


def beyond_limit(price_returns, limit):
    """Moves larger than the daily price limit of the exchange (with a margin for rounding)."""
    if limit is None:
        return pd.DataFrame(False, index=price_returns.index, columns=price_returns.columns)
    return price_returns.abs() > limit + 0.005


def drop_first_days(prices, first_day):
    """Prices that are not used: before FIRST_DAY, and the first one of a company listed later.

    The first price that Yahoo gives for a new listing is often a reference
    price set before trading starts, so the first day is never used.
    """
    prices = prices.copy()
    for ticker, day in first_day.items():
        if ticker in prices.columns:
            prices.loc[prices.index < pd.Timestamp(day), ticker] = np.nan
    first = prices.apply(pd.Series.first_valid_index)
    for ticker, day in first.items():
        if day is not None and day > prices.index[0]:
            prices.at[day, ticker] = np.nan
    return prices


def clean(close, dividends, calendar, limit=None, spin_offs=(), wrong_prices=(), wrong_returns=(), first_day=None):
    """Clean prices on the trading calendar: (returns, price returns, close, corrections).

    1. Prices before a share really traded, and its first day, are not used.
    2. Days without a price after that (trading halts) keep the last price.
    3. The return of a day is the change of the price plus the dividend of
       that day over the price of the day before. A dividend larger than
       MAX_DIVIDEND of the price is a wrong figure and is ignored.
    4. Prices that jump and come back the next day are removed.
    5. Moves beyond the price limit of the exchange are set to zero.
    6. The corrections by hand of config are applied.
    """
    close = close.reindex(calendar)
    for ticker, start, end, factor, _ in wrong_prices:
        if ticker in close.columns:
            close.loc[start:end, ticker] *= factor
    close = drop_first_days(close, first_day or {}).ffill()

    price_returns = close.pct_change(fill_method=None)
    paid = (dividends.reindex(index=calendar, columns=close.columns).fillna(0.0) / close.shift(1)).fillna(0.0)
    too_large = paid > config.MAX_DIVIDEND
    logs = [_log(too_large, paid, 0.0, "dividend larger than half the price; ignored")]
    returns = price_returns + paid.mask(too_large, 0.0)

    hand = pd.DataFrame(False, index=returns.index, columns=returns.columns)
    for ticker, day, _ in wrong_returns:
        if ticker in hand.columns and pd.Timestamp(day) in hand.index:
            hand.at[pd.Timestamp(day), ticker] = True
    done = pd.DataFrame(False, index=returns.index, columns=returns.columns)
    for mask, why in [(beyond_limit(price_returns, limit), LIMIT_WHY),
                      (spikes(price_returns), "price jumps and is back the next day; set to zero"),
                      (hand, "not a return (see config.WRONG_RETURNS); set to zero")]:
        mask = mask & returns.notna() & ~done
        done |= mask
        logs.append(_log(mask, returns, 0.0, why))
        returns = returns.mask(mask, 0.0)
        price_returns = price_returns.mask(mask, 0.0)

    # The clean price follows the clean price returns, so that weights are not moved by wrong prices.
    # It is computed before the spin-offs are added: the value of the company did fall on those days.
    growth = (1 + price_returns.fillna(0.0)).cumprod()
    clean_close = (growth / growth.iloc[-1] * close.iloc[-1]).where(close.notna())

    for ticker, day, stayed, what in spin_offs:
        day = pd.Timestamp(day)
        if ticker in returns.columns and day in returns.index and pd.notna(price_returns.at[day, ticker]):
            before = returns.at[day, ticker]
            received = (1 + price_returns.at[day, ticker]) / stayed - 1
            returns.at[day, ticker] = received + (before - price_returns.at[day, ticker])      # plus the dividend, if any
            price_returns.at[day, ticker] = received
            logs.append(pd.DataFrame([{"date": day, "ticker": ticker, "source": before,
                                       "used": returns.at[day, ticker], "why": what}]))

    return returns, price_returns, clean_close, pd.concat(logs, ignore_index=True)


def daily_risk_free(rate, calendar):
    """Daily return of a deposit at the 3-month rate of the previous month (% per year)."""
    monthly = rate.copy()
    monthly.index = monthly.index + pd.offsets.MonthBegin(1)          # known from the next month on
    daily = monthly.reindex(monthly.index.union(calendar)).ffill().reindex(calendar)
    return (daily / 100 / config.TRADING_DAYS).fillna(0.0)


def unreliable(corrections, since):
    """Members with more moves beyond the price limit than a real share can have."""
    impossible = corrections[(corrections["why"] == LIMIT_WHY) & (corrections["date"] >= pd.Timestamp(since))]
    counts = impossible.groupby("ticker").size()
    return counts[counts > config.MAX_IMPOSSIBLE]


def load_panel(key):
    spec = config.INDICES[key]
    members = pd.read_csv(dd.members_file(key), dtype={"code": str}, keep_default_na=False,
                          na_values=[""]).fillna({"name": "", "sector": ""}).set_index("ticker")
    close, dividends = dd.read_prices(key)
    index = dd.read_series(dd._raw(f"index_{key}.csv")).loc[config.DOWNLOAD_START:config.AS_OF]
    calendar = index.index

    recent = close.reindex(calendar).tail(config.STALE_DAYS)
    priced = [t for t in members.index if t in close.columns and recent[t].notna().any()]
    left_out = [members.loc[~members.index.isin(priced), ["name", "weight"]].assign(
        why=f"no price in the {config.STALE_DAYS} trading days before {config.AS_OF}")]

    def cleaned(tickers):
        return clean(close[tickers], dividends[tickers], calendar, spec["price_limit"],
                     [s for s in config.SPIN_OFFS if s[0] in tickers], config.WRONG_PRICES,
                     config.WRONG_RETURNS, config.FIRST_DAY)

    returns, price_returns, clean_close, corrections = cleaned(priced)
    bad = unreliable(corrections, config.SAMPLE_START)
    if len(bad):
        left_out.append(members.loc[bad.index, ["name", "weight"]].assign(
            why=[f"{n} moves beyond the daily price limit since {config.SAMPLE_START}" for n in bad]))
        priced = [t for t in priced if t not in bad.index]
        returns, price_returns, clean_close, corrections = cleaned(priced)

    members = members.loc[priced].copy()
    members["weight"] = members["weight"] / members["weight"].sum()
    risk_free = daily_risk_free(dd.read_series(dd._raw(f"risk_free_{key}.csv")), calendar)
    return Panel(key, members, returns, price_returns, clean_close, index, risk_free, corrections,
                 pd.concat(left_out).reset_index())
