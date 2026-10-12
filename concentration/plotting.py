"""
All charts. Each portfolio and each index keeps the same colour in every
figure, and no chart uses two y-axes.
"""
import matplotlib

matplotlib.use("Agg")
import matplotlib.dates as mdates
import matplotlib.pyplot as plt
import numpy as np
import pandas as pd
from matplotlib.ticker import FuncFormatter, NullLocator, PercentFormatter

from . import config
from .reporting import LABELS

COLORS = {"index": "#2a78d6", "equal": "#eb6834", "SMI": "#d6453d", "FTSE100": "#2a78d6", "KOSPI": "#1baf7a"}
INK, INK_2, INK_3 = "#0b0b0b", "#52514e", "#8a8985"
SURFACE, GRID, SHADE = "#fcfcfb", "#e4e3df", "#ebeae6"


def setup_style():
    plt.rcParams.update({
        "figure.facecolor": SURFACE, "axes.facecolor": SURFACE, "savefig.facecolor": SURFACE,
        "font.size": 9.5, "text.color": INK, "axes.labelcolor": INK_2,
        "axes.titlesize": 11, "axes.titlelocation": "left", "axes.titlecolor": INK,
        "xtick.color": INK_2, "ytick.color": INK_2, "axes.edgecolor": INK_3,
        "axes.spines.top": False, "axes.spines.right": False,
        "axes.grid": True, "axes.grid.axis": "y", "grid.color": GRID, "grid.linewidth": 0.8,
        "legend.frameon": False, "legend.fontsize": 9, "lines.linewidth": 1.6,
    })


def _save(fig, name):
    config.FIG_DIR.mkdir(parents=True, exist_ok=True)
    fig.savefig(config.FIG_DIR / name, dpi=150, bbox_inches="tight")
    plt.close(fig)


def _date_axis(ax):
    locator = mdates.AutoDateLocator()
    ax.xaxis.set_major_locator(locator)
    ax.xaxis.set_major_formatter(mdates.ConciseDateFormatter(locator))


def _name(key):
    return config.INDICES[key]["name"]


def plot_concentration(weights, shares):
    """Left: weight of the k largest members. Right: money and risk in the largest three.

    `weights`: {index: weights sorted from largest}. `shares`: {index: (weight, share of the risk) of the largest three}.
    """
    fig, (a, b) = plt.subplots(1, 2, figsize=(11, 4.6), gridspec_kw={"width_ratios": [1.5, 1]})
    for key, w in weights.items():
        k = np.arange(1, min(30, len(w)) + 1)
        a.plot(k, np.cumsum(w[:30]), color=COLORS[key], lw=2, marker="o", ms=3,
               label=f"{_name(key)} ({len(w)} members)")
    a.axhline(0.5, color=INK_3, lw=0.8, ls=":")
    a.set_xlabel("Number of members, from the largest")
    a.set_ylim(0, 1)
    a.yaxis.set_major_formatter(PercentFormatter(1.0, decimals=0))
    a.set_title(f"Share of the index in its largest members ({config.AS_OF})")
    a.legend(loc="lower right")

    keys = list(shares)
    x = np.arange(len(keys))
    b.bar(x - 0.2, [shares[k][0] for k in keys], width=0.4, color=INK_3, label="Share of the money")
    b.bar(x + 0.2, [shares[k][1] for k in keys], width=0.4, color=INK, label="Share of the risk")
    for i, k in enumerate(keys):
        for dx, v in [(-0.2, shares[k][0]), (0.2, shares[k][1])]:
            b.annotate(f"{100 * v:.0f}%", (i + dx, v), xytext=(0, 3), textcoords="offset points",
                       ha="center", fontsize=8.5, color=INK_2)
    b.set_xticks(x, [_name(k) for k in keys])
    b.yaxis.set_major_formatter(PercentFormatter(1.0, decimals=0))
    b.set_ylim(0, 1)
    b.set_title("The three largest members")
    b.legend(loc="upper left")
    fig.tight_layout()
    _save(fig, "fig01_concentration.png")


def plot_top_weight(series, fund):
    """Weight of the three largest members through time; dots: what the index fund held."""
    fig, axes = plt.subplots(len(series), 1, figsize=(10, 2.5 * len(series)), sharex=True)
    for ax, (key, s) in zip(axes, series.items()):
        ax.plot(s.index, s, color=COLORS[key], lw=2, label="Today's members, weights carried back with prices")
        if fund.get(key) is not None:
            ax.scatter(fund[key].index, fund[key], s=26, facecolor=SURFACE, edgecolor=INK, lw=1.2, zorder=3,
                       label="Holdings of the index fund on that day (the members of that day)")
        ax.set_ylim(0, max(0.7, float(s.max()) + 0.05))
        ax.yaxis.set_major_formatter(PercentFormatter(1.0, decimals=0))
        ax.set_title(f"{_name(key)}: weight of the three largest members")
        ax.legend(loc="lower left" if key != "KOSPI" else "upper left")
    _date_axis(axes[-1])
    fig.tight_layout()
    _save(fig, "fig02_top3_through_time.png")


def plot_performance(curves):
    """Growth of one unit of money: index weights and equal weight, one panel per index (log scale)."""
    fig, axes = plt.subplots(1, len(curves), figsize=(12, 4.3))
    for ax, (key, frame) in zip(axes, curves.items()):
        wealth = (1 + frame).cumprod()
        for column in ["index", "equal"]:
            ax.plot(wealth.index, wealth[column], color=COLORS[column], lw=1.8,
                    label=f"{LABELS[column]}: {wealth[column].iloc[-1]:.2f}")
        ax.set_yscale("log")
        low, high = float(wealth.min().min()), float(wealth.max().max())
        ax.set_yticks([v for v in (0.5, 0.75, 1, 1.5, 2, 3, 4, 6, 8) if low * 0.95 <= v <= high * 1.05])
        ax.yaxis.set_major_formatter(FuncFormatter(lambda v, _: f"{v:g}"))
        ax.yaxis.set_minor_locator(NullLocator())
        ax.set_title(f"{_name(key)} ({config.INDICES[key]['currency']})")
        ax.legend(loc="upper left", title="Value at the end", alignment="left")
        ax.xaxis.set_major_locator(mdates.YearLocator(2))
        ax.xaxis.set_major_formatter(mdates.DateFormatter("%Y"))
    fig.suptitle(f"Growth of 1 invested on {pd.Timestamp(config.SAMPLE_START):%d %b %Y} in today's members "
                 "(dividends reinvested, log scale)", x=0.01, ha="left", fontsize=11)
    fig.tight_layout()
    _save(fig, "fig03_performance.png")


def plot_years(years):
    """Equal weight minus index weights, year by year, one panel per index."""
    fig, axes = plt.subplots(1, len(years), figsize=(12, 3.8), sharey=False)
    for ax, (key, table) in zip(axes, years.items()):
        diff = table["difference"]
        ax.bar(table["year"].astype(str), diff, color=[COLORS["equal"] if v >= 0 else COLORS["index"] for v in diff])
        ax.axhline(0, color=INK_3, lw=0.8)
        ax.yaxis.set_major_formatter(PercentFormatter(1.0, decimals=0))
        ax.set_title(_name(key))
        ax.tick_params(axis="x", rotation=90)
    fig.suptitle("Return of equal weight minus return of the index weights, by calendar year "
                 "(orange: equal weight ahead)", x=0.01, ha="left", fontsize=11)
    fig.tight_layout()
    _save(fig, "fig04_years.png")


def plot_real_index(ratios):
    """Today's members at index weights, divided by the real index (both without dividends)."""
    fig, ax = plt.subplots(figsize=(10, 4.4))
    for key, ratio in ratios.items():
        ax.plot(ratio.index, ratio, color=COLORS[key], lw=1.8, label=f"{_name(key)}: {ratio.iloc[-1]:.2f}")
    ax.axhline(1, color=INK_3, lw=0.8)
    ax.set_title("Survivorship bias: today's members at index weights, divided by the real index (no dividends)")
    ax.legend(loc="upper left", title="At the end", alignment="left")
    _date_axis(ax)
    fig.tight_layout()
    _save(fig, "fig05_real_index.png")
