"""
Tests of the pieces the results depend on. None of them needs the network or
the downloaded data: `pytest` runs in a few seconds.
"""
import numpy as np
import pandas as pd
import pytest

from concentration import config, measures, metrics
from concentration import data_download as dd
from concentration import data_prep as prep
from concentration import portfolios as pf

HOLDINGS = """Fund Holdings as of,"30/Sept/2026"
\xa0
Ticker,Name,Sector,Asset Class,Market Value,Weight (%),Notional Value,Shares,Price,Location,Exchange,Market Currency
"ROP","ROCHE PS PAR AG","Health Care","Equity","600’000.00","59.41","600’000.00","1’000.00","600.00","Switzerland","SIX Swiss Exchange","CHF"
"NOVN","NOVARTIS AG","Health Care","Equity","300’000.00","29.70","300’000.00","3’000.00","100.00","Switzerland","SIX Swiss Exchange","CHF"
"ABBN","ABB LTD","Industrials","Equity","100’000.00","9.90","100’000.00","2’000.00","50.00","Switzerland","SIX Swiss Exchange","CHF"
"CHF","CHF CASH","Cash and/or Derivatives","Cash","10’000.00","0.99","10’000.00","10’000.00","1.00","Switzerland","-","CHF"
\xa0
"The content contained herein is owned or licensed by BlackRock."
"""


def days(n, start="2024-01-01"):
    return pd.bdate_range(start, periods=n)


def frame(values, columns, start="2024-01-01"):
    values = np.asarray(values, dtype=float)
    return pd.DataFrame(values, index=days(len(values), start), columns=columns)


# ---------------------------------------------------------------------------
# Members and weights
# ---------------------------------------------------------------------------
def test_holdings_file_gives_the_shares_and_their_weights():
    table, as_of = dd.parse_holdings(HOLDINGS)
    assert as_of == "30/Sept/2026"
    assert list(table["code"]) == ["ROP", "NOVN", "ABBN"]              # the cash line is not a member
    assert table["weight"].tolist() == pytest.approx([0.6, 0.3, 0.1])   # from the market values, adding up to 1
    assert list(table["sector"]) == ["Health Care", "Health Care", "Industrials"]


def test_names_are_tidied():
    assert dd.tidy_name("NOVARTIS AG") == "Novartis AG"
    assert dd.tidy_name("HSBC HOLDINGS PLC") == "HSBC Holdings plc"
    assert dd.tidy_name("LEGAL AND GENERAL GROUP PLC") == "Legal and General Group plc"
    assert dd.tidy_name("ROLLS-ROYCE HOLDINGS PLC") == "Rolls-Royce Holdings plc"
    assert dd.tidy_name("ABB LTD") == "ABB Ltd"
    assert dd.tidy_name("RIO TINTO") == "Rio Tinto" and dd.tidy_name("3I GROUP PLC") == "3i Group plc"


def test_yahoo_tickers():
    assert dd.yahoo_ticker("SMI", "NESN") == "NESN.SW"
    assert dd.yahoo_ticker("FTSE100", "BP.") == "BP.L"
    assert dd.yahoo_ticker("FTSE100", "BT.A") == "BT-A.L"
    assert dd.yahoo_ticker("KOSPI", "005930") == "005930.KS"


def test_korean_company_list_keeps_the_leading_zeros():
    html = ("<table><tr><th>회사명</th><th>시장구분</th><th>종목코드</th><th>상장일</th></tr>"
            "<tr><td>삼성전자</td><td>유가</td><td>005930</td><td>1975-06-11</td></tr>"
            "<tr><td>SK하이닉스</td><td>유가</td><td>000660</td><td>1996-12-26</td></tr></table>")
    table = dd.parse_krx_companies(html.encode("euc-kr"))
    assert list(table["code"]) == ["000660", "005930"]
    assert table["listed"].iloc[1] == pd.Timestamp("1975-06-11")


def test_kospi_weights_are_market_values_and_gaps_are_left_out():
    companies = pd.DataFrame({"code": ["000001", "000002", "000003", "000004"],
                              "korean_name": ["가", "나", "다", "라"], "listed": pd.Timestamp("2000-01-03")})
    profiles = pd.DataFrame({"ticker": ["000001.KS", "000002.KS", "000003.KS", "000004.KS"],
                             "shares": [100.0, 50.0, np.nan, 10.0], "name": ["One", "", "Three", "Four"],
                             "sector": ["Industrials", "", "", ""]})
    close = frame([[10, 40, 5, np.nan]] * 12, ["000001.KS", "000002.KS", "000003.KS", "000004.KS"], "2026-09-15")
    members, left_out = dd.kospi_weights(companies, profiles, close, close.index[-1])
    assert list(members["ticker"]) == ["000002.KS", "000001.KS"]            # 2000 and 1000 of market value
    assert members["weight"].tolist() == pytest.approx([2 / 3, 1 / 3])
    assert members["name"].tolist() == ["나", "One"]                         # the Korean name if Yahoo has none
    assert sorted(left_out["code"]) == ["000003", "000004"]                  # no share count; no price


# ---------------------------------------------------------------------------
# Cleaning
# ---------------------------------------------------------------------------
def clean(close, dividends=None, **kwargs):
    dividends = close * 0.0 if dividends is None else dividends
    return prep.clean(close, dividends, close.index, **kwargs)


def test_return_is_price_change_plus_dividend():
    close = frame([[100], [102], [99], [99]], ["A"])
    dividends = frame([[0], [0], [3], [0]], ["A"])
    returns, price_returns, _, log = clean(close, dividends)
    assert price_returns["A"].iloc[2] == pytest.approx(99 / 102 - 1)
    assert returns["A"].iloc[2] == pytest.approx((99 + 3) / 102 - 1)       # ex-dividend day: the holder has both
    assert len(log) == 0


def test_dividend_larger_than_half_the_price_is_ignored():
    close = frame([[100], [100], [100]], ["A"])
    dividends = frame([[0], [80], [0]], ["A"])
    returns, _, _, log = clean(close, dividends)
    assert returns["A"].iloc[1] == 0.0
    assert len(log) == 1 and "dividend" in log["why"].iloc[0]


def test_price_that_jumps_and_comes_back_is_removed():
    close = frame([[100, 50], [100, 50], [10000, 51], [101, 50], [102, 50]], ["A", "B"])
    returns, price_returns, cleaned, log = clean(close)
    assert returns["A"].iloc[2] == 0.0 and returns["A"].iloc[3] == 0.0
    assert returns["B"].iloc[2] == pytest.approx(0.02)                      # a real move is left alone
    assert set(log["date"]) == {close.index[2], close.index[3]}
    assert cleaned["A"].max() < 110                                         # the wrong price is not in the clean close


def test_jump_that_stays_is_a_return():
    close = frame([[100], [100], [150], [152]], ["A"])
    returns, _, _, log = clean(close)
    assert returns["A"].iloc[2] == pytest.approx(0.5) and len(log) == 0


def test_moves_beyond_the_price_limit_are_set_to_zero():
    close = frame([[100, 100], [129, 140], [130, 141]], ["A", "B"])
    returns, price_returns, _, log = clean(close, limit=0.30)
    assert returns["A"].iloc[1] == pytest.approx(0.29)                      # inside the limit
    assert returns["B"].iloc[1] == 0.0 and price_returns["B"].iloc[1] == 0.0
    assert list(log["why"]) == [prep.LIMIT_WHY]
    no_limit, _, _, _ = clean(close)
    assert no_limit["B"].iloc[1] == pytest.approx(0.40)                     # an exchange without a limit


def test_first_day_of_a_new_listing_is_not_used():
    close = frame([[100, np.nan], [101, np.nan], [102, 50], [103, 40], [104, 41]], ["A", "NEW"])
    returns, _, cleaned, _ = clean(close)
    assert np.isnan(cleaned["NEW"].iloc[2])                                 # the reference price of the first day
    assert np.isnan(returns["NEW"].iloc[3])                                 # so there is no return from it
    assert returns["NEW"].iloc[4] == pytest.approx(41 / 40 - 1)
    assert cleaned["A"].notna().all()                                       # a share with a full history keeps its first day


def test_prices_before_the_first_real_day_are_dropped():
    close = frame([[10], [10], [10], [30], [31], [32]], ["A"])
    returns, _, cleaned, _ = clean(close, first_day={"A": str(close.index[3].date())})
    assert cleaned["A"].iloc[:4].isna().all()
    assert returns["A"].iloc[5] == pytest.approx(32 / 31 - 1)


def test_trading_halt_keeps_the_last_price():
    close = frame([[100], [np.nan], [np.nan], [110]], ["A"])
    returns, _, cleaned, _ = clean(close)
    assert cleaned["A"].tolist() == pytest.approx([100, 100, 100, 110])
    assert returns["A"].tolist()[1:] == pytest.approx([0.0, 0.0, 0.10])


def test_spin_off_is_given_back_to_the_holder_but_not_to_the_price():
    close = frame([[100], [100], [60], [60]], ["A"])
    spin = [("A", str(close.index[2].date()), 0.6, "spin-off")]
    returns, price_returns, cleaned, log = clean(close, spin_offs=spin)
    assert returns["A"].iloc[2] == pytest.approx(0.0)                       # 60 that stayed + 40 handed over
    assert price_returns["A"].iloc[2] == pytest.approx(0.0)
    assert cleaned["A"].iloc[1] / cleaned["A"].iloc[2] == pytest.approx(100 / 60)   # the company is smaller
    assert list(log["why"]) == ["spin-off"]


def test_wrong_prices_and_wrong_returns_of_config_are_applied():
    close = frame([[100], [50], [51], [102], [103]], ["A"])
    d = [str(x.date()) for x in close.index]
    returns, _, _, log = clean(close, wrong_prices=[("A", d[1], d[2], 2.0, "half")],
                               wrong_returns=[("A", d[4], "not a return")])
    assert returns["A"].iloc[1] == pytest.approx(0.0) and returns["A"].iloc[2] == pytest.approx(0.02)
    assert returns["A"].iloc[4] == 0.0 and len(log) == 1


def test_unreliable_members_are_those_with_too_many_impossible_moves():
    day = pd.Timestamp("2020-01-02")
    log = pd.DataFrame({"date": [day] * 6 + [pd.Timestamp("2010-01-04")] * 5,
                        "ticker": ["BAD"] * 4 + ["OK"] * 2 + ["OLD"] * 5, "why": prep.LIMIT_WHY})
    bad = prep.unreliable(log, "2016-09-30")
    assert list(bad.index) == ["BAD"] and bad["BAD"] == config.MAX_IMPOSSIBLE + 1


def test_risk_free_uses_the_rate_of_the_previous_month():
    rate = pd.Series([2.52, 5.04], index=pd.to_datetime(["2024-01-01", "2024-02-01"]))
    calendar = pd.bdate_range("2024-01-29", "2024-03-05")
    daily = prep.daily_risk_free(rate, calendar)
    assert daily["2024-01-31"] == 0.0                                       # nothing known before
    assert daily["2024-02-15"] == pytest.approx(0.0001)                     # January's rate, used in February
    assert daily["2024-03-04"] == pytest.approx(0.0002)


# ---------------------------------------------------------------------------
# Portfolios
# ---------------------------------------------------------------------------
def test_index_weights_go_back_with_prices():
    close = frame([[50, 100], [75, 100], [100, 100]], ["A", "B"])
    w = pf.index_weights(pd.Series({"A": 0.5, "B": 0.5}), close)
    assert w.iloc[-1].tolist() == pytest.approx([0.5, 0.5])
    assert w.iloc[0].tolist() == pytest.approx([1 / 3, 2 / 3])              # A was worth half of what it is today
    assert w.sum(axis=1).tolist() == pytest.approx([1, 1, 1])


def test_index_weights_portfolio_is_one_purchase_left_alone():
    rng = np.random.default_rng(0)
    returns = pd.DataFrame(rng.normal(0, 0.02, (60, 3)), index=days(60), columns=list("ABC"))
    close = (1 + returns).cumprod()
    weights = pd.Series({"A": 0.6, "B": 0.3, "C": 0.1})
    held = pf.held_return(pf.index_weights(weights, close), returns)
    shares = weights / close.iloc[-1]                                       # fixed number of shares
    value = (close * shares).sum(axis=1)
    assert held.to_numpy() == pytest.approx(value.pct_change().iloc[1:].to_numpy())


def test_company_listed_later_has_no_weight_before():
    close = frame([[100, np.nan], [100, np.nan], [100, 50], [100, 100]], ["A", "NEW"])
    w = pf.index_weights(pd.Series({"A": 0.5, "NEW": 0.5}), close)
    assert w["NEW"].tolist() == pytest.approx([0, 0, 1 / 3, 0.5])
    assert w["A"].tolist() == pytest.approx([1, 1, 2 / 3, 0.5])


def test_rebalancing_days_are_the_first_day_and_month_ends():
    calendar = pd.bdate_range("2024-01-15", "2024-07-15")
    got = pf.rebalancing_days(calendar, [3, 6])
    assert list(got) == [pd.Timestamp("2024-01-15"), pd.Timestamp("2024-03-29"), pd.Timestamp("2024-06-28")]


def test_equal_weight_is_put_back_to_equal():
    calendar = pd.bdate_range("2024-03-25", "2024-04-05")
    returns = pd.DataFrame(0.0, index=calendar, columns=["A", "B"])
    returns.loc["2024-03-27", "A"] = 1.0                                     # A doubles
    close = (1 + returns).cumprod()
    result = pf.rebalanced(returns, close, months=[3])
    w = result.weights
    assert w.loc["2024-03-25"].tolist() == pytest.approx([0.5, 0.5])
    assert w.loc["2024-03-27"].tolist() == pytest.approx([2 / 3, 1 / 3])     # drifted
    assert w.loc["2024-03-29"].tolist() == pytest.approx([0.5, 0.5])         # last trading day of March
    assert result.trades["traded"].tolist() == pytest.approx([1.0, 1 / 3])
    assert result.gross.loc["2024-03-27"] == pytest.approx(0.5)


def test_costs_are_paid_the_day_after_each_rebalance_but_not_on_the_first_purchase():
    calendar = pd.bdate_range("2024-03-25", "2024-04-05")
    returns = pd.DataFrame(0.0, index=calendar, columns=["A", "B"])
    returns.loc["2024-03-27", "A"] = 1.0
    result = pf.rebalanced(returns, (1 + returns).cumprod(), months=[3])
    net = result.net(cost_bps=100)
    assert net.loc["2024-03-26"] == 0.0                                      # day after the first purchase
    assert net.loc["2024-04-01"] == pytest.approx(-(1 / 3) * 0.01)           # day after the March rebalance
    assert (net.drop(pd.Timestamp("2024-04-01")) == result.gross.drop(pd.Timestamp("2024-04-01"))).all()
    assert result.net(0).equals(result.gross)


def test_new_listing_enters_equal_weight_at_the_next_rebalance():
    calendar = pd.bdate_range("2024-03-20", "2024-04-05")
    close = pd.DataFrame({"A": 100.0, "NEW": np.nan}, index=calendar)
    close.loc["2024-03-26":, "NEW"] = 10.0
    returns = close.pct_change(fill_method=None)
    w = pf.rebalanced(returns, close, months=[3]).weights
    assert w.loc["2024-03-27", "NEW"] == 0.0                                 # trading, but not bought yet
    assert w.loc["2024-03-28", "NEW"] == 0.0
    assert w.loc["2024-03-29", "NEW"] == pytest.approx(0.5)                  # last day of March in this calendar


def test_target_weights_are_shared_among_the_members_that_trade():
    calendar = pd.bdate_range("2024-01-01", periods=5)
    close = pd.DataFrame({"A": 1.0, "B": 1.0, "C": np.nan}, index=calendar)
    result = pf.rebalanced(close.pct_change(fill_method=None), close, target=pd.Series({"A": 0.2, "B": 0.6, "C": 0.2}))
    assert result.weights.iloc[0].tolist() == pytest.approx([0.25, 0.75, 0.0])


def test_group_contributions_add_up_to_the_difference():
    rng = np.random.default_rng(1)
    columns = [f"S{i}" for i in range(12)]
    returns = pd.DataFrame(rng.normal(0, 0.02, (80, 12)), index=days(80), columns=columns)
    close = (1 + returns).cumprod()
    index = pf.index_weights(pd.Series(np.arange(12, 0, -1) / 78.0, index=columns), close)
    equal = pf.rebalanced(returns, close)
    groups = pf.group_contributions(equal.weights - index, returns, columns)
    assert list(groups.columns) == [g[0] for g in config.GROUPS]
    difference = equal.gross - pf.held_return(index, returns)
    assert groups.sum(axis=1).to_numpy() == pytest.approx(difference.to_numpy())


# ---------------------------------------------------------------------------
# Concentration
# ---------------------------------------------------------------------------
def test_effective_number_of_equal_weights_is_their_number():
    assert measures.effective_number([0.25] * 4) == pytest.approx(4.0)
    assert measures.effective_number([1.0, 0.0, 0.0]) == pytest.approx(1.0)
    assert measures.effective_number([0.5, 0.25, 0.25]) == pytest.approx(1 / 0.375)


def test_members_for_half():
    assert measures.members_for_half([0.6, 0.3, 0.1]) == 1
    assert measures.members_for_half([0.3, 0.3, 0.2, 0.2]) == 2
    assert measures.members_for_half([0.25] * 4) == 2


def test_concentration_summary():
    c = measures.concentration(pd.Series([0.1, 0.4, 0.3, 0.2]))
    assert c["members"] == 4 and c["largest"] == pytest.approx(0.4) and c["top3"] == pytest.approx(0.9)


def test_risk_shares_add_up_to_one_and_follow_the_volatile_member():
    rng = np.random.default_rng(2)
    returns = pd.DataFrame(rng.normal(0, [0.01, 0.01, 0.05], (500, 3)), index=days(500), columns=list("ABC"))
    shares = measures.risk_shares(pd.Series({"A": 1 / 3, "B": 1 / 3, "C": 1 / 3}), returns)
    assert shares.sum() == pytest.approx(1.0)
    assert shares["C"] > 0.8                                                # a third of the money, most of the risk


def test_diversification_ratio():
    rng = np.random.default_rng(3)
    a = rng.normal(0, 0.01, 400)
    same = pd.DataFrame({"A": a, "B": 2 * a}, index=days(400))
    assert measures.diversification_ratio(pd.Series({"A": 0.5, "B": 0.5}), same) == pytest.approx(1.0)
    independent = pd.DataFrame(rng.normal(0, 0.01, (4000, 4)), index=days(4000), columns=list("ABCD"))
    dr = measures.diversification_ratio(pd.Series(0.25, index=list("ABCD")), independent)
    assert dr == pytest.approx(2.0, abs=0.1)                                # square root of 4 independent members


def test_top_weight_through_time():
    weights = frame([[0.5, 0.3, 0.2], [0.1, 0.2, 0.7]], list("ABC"))
    assert measures.top_weight(weights, 2).tolist() == pytest.approx([0.8, 0.9])


# ---------------------------------------------------------------------------
# Statistics
# ---------------------------------------------------------------------------
def test_annual_return_compounds():
    daily = pd.Series((1.10 ** (1 / 252) - 1), index=days(504))
    assert metrics.annual_return(daily) == pytest.approx(0.10)


def test_performance_and_relative():
    rng = np.random.default_rng(4)
    base = pd.Series(rng.normal(0.0004, 0.01, 1000), index=days(1000))
    p = metrics.performance(base, base * 0.0)
    assert p["volatility"] == pytest.approx(base.std() * np.sqrt(252))
    assert p["max_drawdown"] < 0
    rel = metrics.relative(2 * base, base)
    assert rel["beta"] == pytest.approx(2.0) and rel["correlation"] == pytest.approx(1.0)
    assert rel["tracking_error"] == pytest.approx(base.std() * np.sqrt(252))


def test_difference_interval_knows_a_sure_difference_from_noise():
    rng = np.random.default_rng(5)
    base = pd.Series(rng.normal(0.0003, 0.01, 1500), index=days(1500))
    rf = base * 0.0
    sure = metrics.difference_interval(base + 0.0002, base, rf, draws=400)
    assert sure["return"]["low"] > 0 and sure["return"]["difference"] == pytest.approx(0.05, abs=0.02)
    noise = pd.Series(rng.normal(0.0003, 0.01, 1500), index=days(1500))
    unsure = metrics.difference_interval(noise, base, rf, draws=400)
    assert unsure["return"]["low"] < 0 < unsure["return"]["high"]
    assert unsure["sharpe"]["low"] < 0 < unsure["sharpe"]["high"]
    same = metrics.difference_interval(base, base, rf, draws=50)
    assert same["return"] == {"difference": 0.0, "low": 0.0, "high": 0.0}
