"""
Every choice of the study in one place.
"""
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
RAW_DIR = ROOT / "data" / "raw"
REF_DIR = ROOT / "data" / "reference"
TABLE_DIR = ROOT / "results" / "tables"
FIG_DIR = ROOT / "results" / "figures"

# ---------------------------------------------------------------------------
# Dates
# ---------------------------------------------------------------------------
# The members of each index and their weights are those of this day. No company
# that left an index before it is in the study: see "Limitations" in the README.
AS_OF = "2026-09-30"
SAMPLE_START = "2016-09-30"        # ten years
DOWNLOAD_START = "2006-06-01"      # the longest horizon of the robustness table needs 20 years
ROBUST_YEARS = [5, 15, 20]
TRADING_DAYS = 252

# ---------------------------------------------------------------------------
# The three indices
# ---------------------------------------------------------------------------
# iShares publishes the holdings of its funds for any past day. The fund of an
# index holds its members at their index weights, so its holdings file is the
# list of members and weights. {site} and {product} identify the fund.
ISHARES_URL = ("https://www.ishares.com/ch/individual/en/products/{product}/{slug}/1495092304805.ajax"
               "?fileType=csv&fileName={fund}_holdings&dataType=fund&asOfDate={date:%Y%m%d}")
# Official list of the companies of the KOSPI market (Korea Exchange).
KRX_LIST_URL = "https://kind.krx.co.kr/corpgeneral/corpList.do?method=download&marketType=stockMkt"
FRED_URL = "https://fred.stlouisfed.org/graph/fredgraph.csv?id={code}"

INDICES = {
    "SMI": {
        "name": "SMI", "country": "Switzerland", "currency": "CHF", "suffix": ".SW",
        "members": {"source": "ishares", "fund": "CSSMI", "product": "261154", "slug": "ishares-smi-ch",
                    "label": "iShares SMI ETF (CH)"},
        "index": "^SSMI",                     # the real index, without dividends
        "risk_free": "IR3TIB01CHM156N",       # 3-month interbank rate (OECD, via FRED)
        "price_limit": None,
    },
    "FTSE100": {
        "name": "FTSE 100", "country": "United Kingdom", "currency": "GBP", "suffix": ".L",
        "members": {"source": "ishares", "fund": "ISF", "product": "251795",
                    "slug": "ishares-ftse-100-ucits-etf-inc-fund", "label": "iShares Core FTSE 100 UCITS ETF"},
        "index": "^FTSE",
        "risk_free": "IR3TIB01GBM156N",
        "price_limit": None,
    },
    "KOSPI": {
        "name": "KOSPI", "country": "South Korea", "currency": "KRW", "suffix": ".KS",
        "members": {"source": "krx", "label": "Korea Exchange company list and Yahoo Finance share counts"},
        "index": "^KS11",
        "risk_free": "IR3TIB01KRM156N",
        # The Korea Exchange does not let a share move more than 30% in a day
        # (15% until June 2015). A larger move in the data is not a real return.
        "price_limit": 0.30,
    },
}
ORDER = ["SMI", "FTSE100", "KOSPI"]

# ---------------------------------------------------------------------------
# Portfolios
# ---------------------------------------------------------------------------
# Index weights: each member weighs what it weighs in the index. Going back
# from AS_OF, the weight of a share moves with its price, as it does in an
# index weighted by market value. Nothing is traded.
# Equal weight: the same amount in every member, put back to equal on the last
# trading day of each of these months.
REBALANCE_MONTHS = [3, 6, 9, 12]
COST_BPS = 10                      # cost of each purchase or sale, in basis points
ROBUST_REBALANCE = {"monthly": list(range(1, 13)), "yearly": [9]}
ROBUST_COST_BPS = [0, 25]
KOSPI_LARGEST = 200                # robustness: only the largest members of the KOSPI

RISK_DAYS = 504                    # window for the risk figures (two years)
TOP = 3                            # "the largest members"
GROUPS = [("Largest 3", 0, 3), ("Next 7", 3, 10), ("All the others", 10, None)]

BOOTSTRAP_DRAWS = 5000
BOOTSTRAP_BLOCK = 21

# ---------------------------------------------------------------------------
# Data cleaning
# ---------------------------------------------------------------------------
# Returns are built from the close and the dividends of Yahoo (not from its
# adjusted close, which leaves out the dividends of London shares). A dividend
# larger than this part of the price is a wrong figure and is ignored.
MAX_DIVIDEND = 0.5
# A price that jumps by more than SPIKE and is back within SPIKE_BACK of where
# it was on the next day is a wrong price (in London, a price in pounds among
# prices in pence), not a return.
SPIKE = 0.40
SPIKE_BACK = 0.15
# A member needs a price on AS_OF, or in the days before it, to be in the study.
STALE_DAYS = 10

YAHOO_BATCH = 100
YAHOO_RETRIES = 3
YAHOO_WORKERS = 4

# A Korean share with more than this many moves beyond the price limit since
# SAMPLE_START has a price history that cannot be trusted, and is left out.
MAX_IMPOSSIBLE = 3

# --- Corrections by hand (SMI and FTSE 100) -------------------------------
# Every one-day move of a member above 10% (SMI) or 17% (FTSE 100) since
# SAMPLE_START was looked at. Most are real news. These are not.
#
# Days on which the holder received something that Yahoo does not record, so
# that the share seems to lose it: a company handed to the shareholders, or the
# right to buy new shares below the market price. (ticker, day, part of the
# value that stayed in the share, what happened.) The return of the day is
# (1 + return of the price) / part that stayed - 1.
SPIN_OFFS = [
    ("HOLN.SW", "2025-06-23", 54.26 / (54.26 + 39.31), "Spin-off of Amrize, one share per Holcim share"),
    ("BGEO.L", "2018-05-29", 1863.8 / (1863.8 + 1113.6), "Demerger of Georgia Capital, one share per share"),
    ("IAG.L", "2020-09-14", (2 * 194.2 + 3 * 85.0) / (5 * 194.2), "Rights issue, 3 new shares for 2 at 0.92 euros"),
    ("EZJ.L", "2021-09-13", (47 * 680.8 + 31 * 410.0) / (78 * 680.8), "Rights issue, 31 new shares for 47 at 410p"),
]
# Prices of Yahoo on the wrong scale. In the week before the spin-off of
# Amrize, Holcim appears at about half its price; the factor leaves the first
# of those days unchanged against the day before. (ticker, first day, last day,
# factor, why.)
WRONG_PRICES = [
    ("HOLN.SW", "2025-06-16", "2025-06-20", 93.10 / 48.07, "prices of the week before the spin-off shown at half"),
]
# Returns that are not returns: set to zero. (ticker, day, why.)
WRONG_RETURNS = [
    ("CCEP.L", "2024-10-21", "the London line changes from euros to pounds"),
    ("MRO.L", "2023-04-20", "demerger of Dowlais with a share consolidation; the day cannot be rebuilt"),
]
# Prices before the share traded on this exchange are another line of the same
# company with stale prices; they are not used. {ticker: first day used}
FIRST_DAY = {"PSH.L": "2017-05-02", "EDV.L": "2021-06-14"}
