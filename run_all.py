"""
Index weights against equal weight in the SMI, the FTSE 100 and the KOSPI.

    python run_all.py              # everything (downloads what is missing)
    python run_all.py --refresh    # download everything again

The members of each index on config.AS_OF are held in two ways over the ten
years before it: at the weights they have in the index, and in equal parts.
Every table goes to results/tables/, every chart to results/figures/, and all
tables together to results/summary.md.
"""
import argparse
from dataclasses import dataclass

import numpy as np
import pandas as pd

from concentration import config, measures, metrics
from concentration import data_download as dd
from concentration import plotting as pl
from concentration import portfolios as pf
from concentration.data_prep import LIMIT_WHY, load_panel
from concentration.reporting import LABELS, interval, num, pct, points, save_table, write_summary


def name(key):
    return config.INDICES[key]["name"]


@dataclass
class Study:
    """The two portfolios of one index from one starting day."""
    panel: object
    returns: pd.DataFrame          # from the starting day
    close: pd.DataFrame
    index_weights: pd.DataFrame    # weight of each member at each close
    equal: pf.Rebalanced
    cost_bps: float

    @property
    def index_return(self):
        return pf.held_return(self.index_weights, self.returns)

    @property
    def equal_return(self):
        return self.equal.net(self.cost_bps)

    @property
    def risk_free(self):
        return self.panel.risk_free.reindex(self.index_return.index)


def study(panel, years=None, months=None, cost_bps=config.COST_BPS, largest=None):
    """Build both portfolios. `years`: length of the period (None = from SAMPLE_START)."""
    end = pd.Timestamp(config.AS_OF)
    start = pd.Timestamp(config.SAMPLE_START) if years is None else end - pd.DateOffset(years=years)
    returns, close = panel.returns.loc[start:], panel.close.loc[start:]
    weights = panel.members["weight"]
    if largest is not None:
        weights = weights.nlargest(largest)
        weights = weights / weights.sum()
        returns, close = returns[weights.index], close[weights.index]
    return Study(panel, returns, close, pf.index_weights(weights, close), pf.rebalanced(returns, close, None, months),
                 cost_bps)


# ---------------------------------------------------------------------------
# Step 0. The data
# ---------------------------------------------------------------------------
def step0_data(panels, studies):
    rows, raw = [], []
    for key, p in panels.items():
        s = studies[key]
        first = s.close.iloc[0].notna()
        kinds = p.corrections["why"].value_counts()
        row = {"index": key, "members_in_source": len(p.members) + len(p.left_out), "members_used": len(p.members),
               "left_out": len(p.left_out), "weight_left_out": float(p.left_out["weight"].sum()),
               "trading_at_start": int(first.sum()), "weight_today_of_those": float(p.members["weight"][first].sum()),
               "returns_corrected": len(p.corrections),
               "beyond_price_limit": int(kinds.get(LIMIT_WHY, 0))}
        raw.append(row)
        rows.append({"Index": name(key), "Members in the source": row["members_in_source"],
                     "Members used": row["members_used"], "Left out": row["left_out"],
                     f"Already trading on {s.close.index[0].date()}": row["trading_at_start"],
                     "Weight today of those": pct(row["weight_today_of_those"]),
                     "Returns corrected": row["returns_corrected"],
                     "of which beyond the price limit": row["beyond_price_limit"] if row["beyond_price_limit"] else "–"})
        p.corrections.assign(index=key).to_csv(config.TABLE_DIR / f"corrections_{key}.csv", index=False)
        p.members[["name", "sector", "weight"]].to_csv(config.TABLE_DIR / f"members_{key}.csv")
    save_table("t0a_data", "Data: members and corrections", pd.DataFrame(rows), raw=pd.DataFrame(raw),
               note=f"Members and weights of {config.AS_OF}. SMI and FTSE 100: holdings of the iShares fund of each "
                    "index. KOSPI: every company of the Korea Exchange list, weighted by the market value of its "
                    "ordinary shares (share count from Yahoo Finance). 'Left out': members without a usable price "
                    "history (see the next table). A member that was listed after the start comes into the "
                    "portfolios when it starts trading. 'Returns corrected': days on which the return of the "
                    "source was changed; the full list is in corrections_<index>.csv, and the members that are used, "
                    "with their weights, in members_<index>.csv.")

    left = pd.concat([p.left_out.assign(index=name(key)) for key, p in panels.items()])
    if len(left):
        pretty = pd.DataFrame({"Index": left["index"], "Ticker": left["ticker"], "Company": left["name"],
                               "Weight in the source": [pct(w, 3) for w in left["weight"]], "Why": left["why"]})
        save_table("t0b_left_out", "Data: members that are not used", pretty, raw=left,
                   note="The weight is the one computed from the source before these members were removed. "
                        "A weight of several per cent for an unknown company is the sign of a wrong price.")

    rows = []
    for key, p in panels.items():
        r = studies[key].returns
        stacked = r.stack()
        for label, picked in [("Largest gains", stacked.nlargest(4)), ("Largest falls", stacked.nsmallest(4))]:
            for (day, ticker), value in picked.items():
                rows.append({"Index": name(key), "": label, "Day": day.date(), "Company": p.members.at[ticker, "name"],
                             "Return": pct(value), "Weight today": pct(p.members.at[ticker, "weight"], 2)})
    save_table("t0c_largest_moves", "Data: the largest one-day moves that are left in the data", pd.DataFrame(rows),
               note="After the corrections. Listed so that they can be checked against the news of the day. "
                    "In the KOSPI no move can exceed the 30% daily limit of the exchange.")


# ---------------------------------------------------------------------------
# Step 1. How concentrated is each index today?
# ---------------------------------------------------------------------------
def step1_concentration(panels, studies):
    rows, raw, curves, shares, top_rows = [], [], {}, {}, []
    for key, p in panels.items():
        w = p.members["weight"].sort_values(ascending=False)
        window = p.returns.tail(config.RISK_DAYS)
        c = measures.concentration(w)
        risk = measures.risk_shares(w, window)
        equal = pd.Series(1 / len(w), index=w.index)
        sector = p.members.groupby("sector")["weight"].sum().drop("", errors="ignore").sort_values(ascending=False)
        c.update(risk_top3=float(risk[w.index[:3]].sum()), risk_top10=float(risk[w.index[:10]].sum()),
                 dr_index=measures.diversification_ratio(w, window),
                 dr_equal=measures.diversification_ratio(equal, window),
                 largest_sector=sector.index[0], largest_sector_weight=float(sector.iloc[0]))
        raw.append({"index": key, **c})
        rows.append({"Index": name(key), "Members": c["members"], "Largest member": pct(c["largest"]),
                     "Largest 3": pct(c["top3"]), "Largest 5": pct(c["top5"]), "Largest 10": pct(c["top10"]),
                     "Members that make half": c["members_for_half"],
                     "Effective number of members": num(c["effective_number"], 1),
                     "Risk in the largest 3": pct(c["risk_top3"]),
                     "DR, index weights": num(c["dr_index"]), "DR, equal weight": num(c["dr_equal"]),
                     "Largest sector": f"{c['largest_sector']} ({pct(c['largest_sector_weight'], 0)})"})
        curves[key] = w.to_numpy()
        shares[key] = (c["top3"], c["risk_top3"])
        for rank, ticker in enumerate(w.index[:10], start=1):
            top_rows.append({"Index": name(key), "Rank": rank, "Company": p.members.at[ticker, "name"],
                             "Sector": p.members.at[ticker, "sector"] or "–", "Weight": pct(w[ticker]),
                             "Share of the risk": pct(risk[ticker])})
    save_table("t1a_concentration", f"Concentration of each index on {config.AS_OF}", pd.DataFrame(rows),
               raw=pd.DataFrame(raw),
               note="'Effective number of members': how many equal positions would be as concentrated "
                    "(1 / sum of squared weights). 'Members that make half': the largest members needed to add up "
                    f"to 50%. 'Risk in the largest 3': share of the variance of the index that comes from them, "
                    f"with the weights of that day and the daily returns of the {config.RISK_DAYS} trading days "
                    "before it. DR: diversification ratio (weighted average volatility of the members divided by "
                    "the volatility of the portfolio), same window. Sectors: GICS names of the fund (SMI, FTSE 100) "
                    "and of Yahoo Finance (KOSPI).")
    save_table("t1b_largest_members", "The ten largest members of each index", pd.DataFrame(top_rows))
    pl.plot_concentration(curves, shares)


# ---------------------------------------------------------------------------
# Step 2. Has it always been like this?
# ---------------------------------------------------------------------------
def step2_through_time(panels, studies):
    days = dd.year_ends()
    table = pd.DataFrame({"Day": [d.date() for d in days]})
    raw = table.rename(columns={"Day": "day"})
    series, fund = {}, {}
    for key, p in panels.items():
        top = measures.top_weight(studies[key].index_weights, config.TOP)
        series[key] = top
        carried = [float(top.loc[:d].iloc[-1]) for d in days]
        table[f"{name(key)}: today's members"] = [pct(v) for v in carried]
        raw[f"{key}_carried_back"] = carried
        history = dd.read_holdings_history(key) if config.INDICES[key]["members"]["source"] == "ishares" else None
        if history is not None:
            # Two classes of shares of one company (Royal Dutch Shell A and B until 2022) count as one member.
            company = history["name"].str.replace(r"\s+Class [AB]$", "", regex=True)
            by_company = history.groupby(["date", company])["weight"].sum()
            held = by_company.groupby("date").apply(lambda w: w.nlargest(config.TOP).sum())
            fund[key] = held
            table[f"{name(key)}: the fund that day"] = [pct(held[d]) if d in held.index else "–" for d in days]
            raw[f"{key}_fund"] = [held.get(d, np.nan) for d in days]
    save_table("t2a_top3_through_time", f"Weight of the {config.TOP} largest members through time", table, raw=raw,
               note="'Today's members': the weights of today carried back with prices, as in the index-weights "
                    "portfolio. 'The fund that day': what the iShares fund of the index really held on that day, "
                    "with the members the index had then. The two differ when companies have entered or left the "
                    "index, issued or bought back shares, or, in the SMI, when the 18% cap was applied. The KOSPI "
                    "has no such file.")
    pl.plot_top_weight(series, fund)


# ---------------------------------------------------------------------------
# Step 3. Index weights against equal weight
# ---------------------------------------------------------------------------
def step3_performance(panels, studies):
    rows, raw, diff_rows, diff_raw, curves = [], [], [], [], {}
    for key, s in studies.items():
        rf = s.risk_free
        index, equal = s.index_return, s.equal_return
        curves[key] = pd.DataFrame({"index": index, "equal": equal})
        rel = metrics.relative(equal, index)
        for label, r in [("index", index), ("equal", equal)]:
            p = metrics.performance(r, rf)
            p.update(index=key, portfolio=label)
            row = {"Index": name(key), "Portfolio": LABELS[label], "Return per year": pct(p["annual_return"]),
                   "Volatility": pct(p["volatility"]), "Sharpe ratio": num(p["sharpe"]),
                   "Worst drawdown": pct(p["max_drawdown"]), "Beta to the index weights": "1.00",
                   "Tracking error": "–", "Turnover per year": "0%", "Members held": "–"}
            if label == "equal":
                p.update(rel, turnover=s.equal.turnover_per_year(), members_held=float(s.equal.trades["members"].mean()),
                         before_costs=metrics.annual_return(s.equal.gross))
                row.update({"Beta to the index weights": num(p["beta"]), "Tracking error": pct(p["tracking_error"]),
                            "Turnover per year": pct(p["turnover"], 0), "Members held": f"{p['members_held']:.0f}"})
            else:
                row["Members held"] = f"{(s.index_weights > 0).sum(axis=1).mean():.0f}"
            raw.append(p)
            rows.append(row)
        d = metrics.difference_interval(equal, index, s.panel.risk_free)
        diff_raw.append({"index": key, **{f"{k}_{f}": v for k, x in d.items() for f, v in x.items()}})
        clear = lambda x: "yes" if x["low"] > 0 or x["high"] < 0 else "no"
        diff_rows.append({"Index": name(key),
                          "Return per year: equal minus index weights (points)": interval(d["return"]),
                          "Different from 0?": clear(d["return"]),
                          "Sharpe ratio: equal minus index weights": interval(d["sharpe"], 2, 1),
                          "Different from 0? ": clear(d["sharpe"])})
    first, last = next(iter(studies.values())).index_return.index[[0, -1]]
    save_table("t3a_performance", "Performance of today's members: index weights against equal weight",
               pd.DataFrame(rows), raw=pd.DataFrame(raw),
               note=f"{first.date()} to {last.date()}, in the currency of each index, dividends reinvested. "
                    "Index weights: no trading. Equal weight: rebalanced four times a year, after costs of "
                    f"{config.COST_BPS} basis points per purchase or sale. Sharpe ratio over the 3-month interbank "
                    "rate of each country. Only companies that are in the index today are held, so both rows are "
                    "better than what an investor could have earned (see step 5); the comparison between them "
                    "is also affected, in favour of equal weight.")
    save_table("t3b_difference", "Equal weight minus index weights, with 95% intervals", pd.DataFrame(diff_rows),
               raw=pd.DataFrame(diff_raw),
               note=f"Intervals from a circular block bootstrap of the daily returns ({config.BOOTSTRAP_DRAWS} "
                    f"redraws, blocks of {config.BOOTSTRAP_BLOCK} days). If the interval contains 0 the sample "
                    "cannot tell the two portfolios apart.")
    pl.plot_performance(curves)
    pd.concat({key: c for key, c in curves.items()}, axis=1, sort=True).to_csv(config.TABLE_DIR / "daily_returns.csv")
    return curves


# ---------------------------------------------------------------------------
# Step 4. Where does the difference come from?
# ---------------------------------------------------------------------------
def step4_where(panels, studies, curves):
    rows, raw = [], []
    for key, s in studies.items():
        order = list(s.panel.members["weight"].sort_values(ascending=False).index)
        active = s.equal.weights - s.index_weights
        groups = pf.group_contributions(active, s.returns, order)
        per_year = groups.mean() * config.TRADING_DAYS
        for group, first, last in config.GROUPS:
            members = order[first:last]
            entry = {"index": key, "group": group, "members": len(members),
                     "index_weight": float(s.index_weights[members].sum(axis=1).mean()),
                     "equal_weight": float(s.equal.weights[members].sum(axis=1).mean()),
                     "contribution": float(per_year[group])}
            raw.append(entry)
            rows.append({"Index": name(key), "Members": group, "How many": entry["members"],
                         "Average weight in the index": pct(entry["index_weight"]),
                         "Average weight in equal weight": pct(entry["equal_weight"]),
                         "Part of the difference (points per year)": points(entry["contribution"], 2)})
        total = float(per_year.sum())
        raw.append({"index": key, "group": "Total", "contribution": total})
        rows.append({"Index": name(key), "Members": "Total, before costs", "How many": len(order),
                     "Average weight in the index": "100%", "Average weight in equal weight": "100%",
                     "Part of the difference (points per year)": points(total, 2)})
    save_table("t4a_where", "Where the difference between equal weight and the index weights comes from",
               pd.DataFrame(rows), raw=pd.DataFrame(raw),
               note="Members are ranked by their weight today. 'Part of the difference': each day, (weight in equal "
                    "weight minus weight in the index, the day before) times the return of the member, added up "
                    "over the group and averaged over the period (times 252). The groups add up to the average "
                    "daily return of equal weight minus that of the index weights, before costs; it is an average "
                    "of daily differences, close to but not the same as the difference between compound returns.")

    years, rows = {}, []
    for key, c in curves.items():
        top = measures.top_weight(studies[key].index_weights, config.TOP)
        yearly = (1 + c).groupby(c.index.year).prod() - 1
        yearly["difference"] = yearly["equal"] - yearly["index"]
        yearly["top"] = top.groupby(top.index.year).last() - top.groupby(top.index.year).first()
        years[key] = yearly.reset_index(names="year")
        for year, r in yearly.iterrows():
            rows.append({"Index": name(key), "Year": year, "Index weights": pct(r["index"]), "Equal weight": pct(r["equal"]),
                         "Equal minus index weights (points)": points(r["difference"]),
                         f"Change in the weight of the largest {config.TOP} (points)": points(r["top"])})
    save_table("t4b_years", "Year by year", pd.DataFrame(rows),
               raw=pd.concat([y.assign(index=key) for key, y in years.items()]),
               note="The first and the last year are not complete. The weight of the largest members is the one of "
                    "the index-weights portfolio (today's members).")
    pl.plot_years(years)


# ---------------------------------------------------------------------------
# Step 5. How far are today's members from the real index?
# ---------------------------------------------------------------------------
def step5_real_index(panels):
    rows, raw, ratios = [], [], {}
    end = pd.Timestamp(config.AS_OF)
    for key, p in panels.items():
        real = p.index.pct_change(fill_method=None)
        for years in [1, 3, 5, 10]:
            start = end - pd.DateOffset(years=years)
            close, price_returns = p.close.loc[start:], p.price_returns.loc[start:]
            members = pf.held_return(pf.index_weights(p.members["weight"], close), price_returns)
            index = real.reindex(members.index).fillna(0.0)
            entry = {"index": key, "years": years, "members": metrics.annual_return(members),
                     "real": metrics.annual_return(index),
                     "tracking_error": float((members - index).std() * np.sqrt(config.TRADING_DAYS)),
                     "correlation": float(members.corr(index))}
            entry["gap"] = entry["members"] - entry["real"]
            raw.append(entry)
            rows.append({"Index": name(key), "Period": f"Last {years} year" + ("s" if years > 1 else ""),
                         "Today's members at index weights": pct(entry["members"]),
                         "The real index": pct(entry["real"]), "Gap (points per year)": points(entry["gap"]),
                         "Tracking error": pct(entry["tracking_error"]), "Correlation": num(entry["correlation"], 3)})
            if years == 10:
                ratios[key] = (1 + members).cumprod() / (1 + index).cumprod()
    save_table("t5a_real_index", "Survivorship bias: today's members at index weights against the real index",
               pd.DataFrame(rows), raw=pd.DataFrame(raw),
               note="Both without dividends, return per year. The real index held the members of each day, "
                    "including the companies that have since left it. Today's members are, by construction, the "
                    "ones that did well enough to be in the index now, so the gap is the survivorship bias of the "
                    "index-weights portfolio (plus the changes in the number of shares, which it ignores). Over "
                    "one year the gap should be close to 0: it checks that the members and weights are right.")
    pl.plot_real_index(ratios)


# ---------------------------------------------------------------------------
# Step 6. Robustness
# ---------------------------------------------------------------------------
def step6_robustness(panels, studies):
    designs = [("Baseline: 10 years, quarterly rebalance", {})]
    designs += [(f"Last {y} years", {"years": y}) for y in config.ROBUST_YEARS]
    designs += [(f"Equal weight rebalanced {label}", {"months": months})
                for label, months in config.ROBUST_REBALANCE.items()]
    designs += [(f"Costs: {c} basis points", {"cost_bps": c}) for c in config.ROBUST_COST_BPS]
    rows, raw = [], []
    for label, changes in designs:
        for key, p in panels.items():
            s = studies[key] if not changes else study(p, **changes)
            rows.append(_robust_row(label, key, s))
            raw.append(_robust_raw(label, key, s))
    key = "KOSPI"
    s = study(panels[key], largest=config.KOSPI_LARGEST)
    label = f"Only the {config.KOSPI_LARGEST} largest members"
    rows.append(_robust_row(label, key, s))
    raw.append(_robust_raw(label, key, s))
    save_table("t6a_robustness", "Robustness: one change at a time", pd.DataFrame(rows), raw=pd.DataFrame(raw),
               note="Return per year. The longer the period, the more the members of today differ from the members "
                    "of the index at the start, and the larger the survivorship bias of both portfolios.")

    rows, raw = [], []
    for key, s in studies.items():
        fixed = pf.rebalanced(s.returns, s.close, s.panel.members["weight"])
        entry = {"index": key, "carried_back": metrics.annual_return(s.index_return),
                 "fixed": metrics.annual_return(fixed.net(config.COST_BPS)),
                 "equal": metrics.annual_return(s.equal_return),
                 "top_start": float(measures.top_weight(s.index_weights, config.TOP).iloc[0]),
                 "top_end": float(measures.top_weight(s.index_weights, config.TOP).iloc[-1])}
        raw.append(entry)
        rows.append({"Index": name(key), "Index weights (weights move with prices)": pct(entry["carried_back"]),
                     "Today's weights held all along": pct(entry["fixed"]), "Equal weight": pct(entry["equal"]),
                     f"Weight of the largest {config.TOP} at the start": pct(entry["top_start"]),
                     "today": pct(entry["top_end"])})
    save_table("t6b_hindsight", "What holding today's weights all along would have shown", pd.DataFrame(rows),
               raw=pd.DataFrame(raw),
               note="Return per year. 'Today's weights held all along' puts the portfolio back to the weights of "
                    f"{config.AS_OF} every quarter. Those weights are the result of the ten years (the companies "
                    "that rose the most weigh the most today), so that portfolio knows the future. It is shown "
                    "only to measure how much that mistake would be worth.")


def _robust_raw(label, key, s):
    rf = s.risk_free
    return {"design": label, "index": key, "first_day": s.returns.index[0].date(),
            "members_at_start": int(s.close.iloc[0].notna().sum()),
            "index_return": metrics.annual_return(s.index_return), "equal_return": metrics.annual_return(s.equal_return),
            "index_sharpe": metrics.sharpe(s.index_return - rf), "equal_sharpe": metrics.sharpe(s.equal_return - rf)}


def _robust_row(label, key, s):
    r = _robust_raw(label, key, s)
    return {"Design": label, "Index": name(key), "Members at the start": r["members_at_start"],
            "Index weights": pct(r["index_return"]), "Equal weight": pct(r["equal_return"]),
            "Equal minus index weights (points)": points(r["equal_return"] - r["index_return"]),
            "Sharpe: index weights": num(r["index_sharpe"]), "Sharpe: equal weight": num(r["equal_sharpe"])}


# ---------------------------------------------------------------------------
def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--refresh", action="store_true", help="re-download every file")
    args = parser.parse_args()

    print("Data (downloads only what is missing)")
    dd.download_all(refresh=args.refresh)
    panels = {key: load_panel(key) for key in config.ORDER}
    studies = {key: study(p) for key, p in panels.items()}
    config.TABLE_DIR.mkdir(parents=True, exist_ok=True)
    pl.setup_style()

    print("Step 0: data checks")
    step0_data(panels, studies)
    print("Step 1: concentration today")
    step1_concentration(panels, studies)
    print("Step 2: concentration through time")
    step2_through_time(panels, studies)
    print("Step 3: index weights against equal weight")
    curves = step3_performance(panels, studies)
    print("Step 4: where the difference comes from")
    step4_where(panels, studies, curves)
    print("Step 5: today's members against the real index")
    step5_real_index(panels)
    print("Step 6: robustness")
    step6_robustness(panels, studies)
    write_summary()
    print(f"Done. Tables in {config.TABLE_DIR}, figures in {config.FIG_DIR}, summary in results/summary.md")


if __name__ == "__main__":
    main()
