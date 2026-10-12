"""
Downloads every input once and keeps it on disk.

    data/reference/members_<index>.csv    members and weights on config.AS_OF (committed)
    data/raw/holdings_<index>.csv         the fund file the weights come from (SMI, FTSE 100)
    data/raw/holdings_history_<index>.csv weights of the fund at the end of each past year
    data/raw/krx_companies.csv            the company list of the KOSPI market
    data/raw/krx_profiles.csv             share counts, names and sectors from Yahoo (KOSPI)
    data/raw/close_<index>.parquet        close of every member (adjusted for splits, not for dividends)
    data/raw/dividends_<index>.parquet    dividends of every member, on the day the share trades without them
    data/raw/index_<index>.csv            the real index
    data/raw/risk_free_<index>.csv        3-month interbank rate
    data/raw/manifest.json                source and download time of every file (committed)

`download_all(refresh=False)` only fetches what is missing.
"""
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
import io
import json
import logging
import time

import pandas as pd
import requests
import truststore

from . import config

truststore.inject_into_ssl()          # use the certificates of the operating system

BROWSER = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) "
                         "Chrome/141.0 Safari/537.36"}
MANIFEST = config.RAW_DIR / "manifest.json"
YAHOO_URL = "https://finance.yahoo.com/quote/{code}"       # documentation only
SECTOR_NAMES = {"Communication": "Communication Services", "Technology": "Information Technology",
                "Financial Services": "Financials", "Healthcare": "Health Care",
                "Consumer Cyclical": "Consumer Discretionary", "Consumer Defensive": "Consumer Staples",
                "Basic Materials": "Materials"}


def members_file(key):
    return config.REF_DIR / f"members_{key}.csv"


def _raw(name):
    return config.RAW_DIR / name


def _update_manifest(name, **entry):
    manifest = json.loads(MANIFEST.read_text()) if MANIFEST.exists() else {}
    entry["downloaded_at_utc"] = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M")
    manifest[name] = entry
    MANIFEST.write_text(json.dumps(manifest, indent=2))


def yahoo_ticker(key, code):
    """Yahoo's ticker of a member. London codes such as `BP.` and `BT.A` become BP.L and BT-A.L."""
    return str(code).strip().rstrip(".").replace(".", "-") + config.INDICES[key]["suffix"]


# ---------------------------------------------------------------------------
# Members and weights
# ---------------------------------------------------------------------------
ACRONYMS = {"AG", "SA", "NV", "PS", "ABB", "UBS", "HSBC", "BP", "BT", "GSK", "SSE", "ITV", "JD", "WPP", "RELX",
            "LSEG", "IMI", "ICG", "DCC", "BAE", "IAG", "IHG", "RS", "SGS", "REIT", "UK", "US"}
SMALL_WORDS = {"AND": "and", "OF": "of", "THE": "the", "DE": "de", "PLC": "plc", "3I": "3i"}


def tidy_name(name):
    """`NOVARTIS AG` -> `Novartis AG`: capitals for the first letters and for known acronyms."""
    words = []
    for word in str(name).strip().split():
        if word in SMALL_WORDS and (words or word in ("PLC", "3I")):
            words.append(SMALL_WORDS[word])
        elif word in ACRONYMS:
            words.append(word)
        else:
            words.append("-".join(part.capitalize() for part in word.split("-")))
    return " ".join(words)


def parse_holdings(text):
    """Shares held by an iShares fund: code, name, sector and weight (adding up to 1).

    The file starts with the date of the holdings; the table starts on the
    third line and ends at the first empty one. Cash and futures are left out.
    """
    lines = text.splitlines()
    as_of = lines[0].split(",", 1)[1].strip().strip('"')
    end = next((i for i, line in enumerate(lines) if i > 2 and not line.strip()), len(lines))
    table = pd.read_csv(io.StringIO("\n".join(lines[2:end])), dtype=str, keep_default_na=False)
    table = table[table["Asset Class"] == "Equity"]
    # The weights of the file have two decimals; the market values give them exactly.
    value = table["Market Value"].str.replace("[^0-9.-]", "", regex=True).astype(float)
    out = pd.DataFrame({"code": table["Ticker"].str.strip(), "name": table["Name"].map(tidy_name),
                        "sector": table["Sector"].str.strip(), "weight": value / value.sum()})
    out = out[out["weight"] > 0]
    return out.sort_values("weight", ascending=False).reset_index(drop=True), as_of


def _holdings(key, day):
    fund = config.INDICES[key]["members"]
    url = config.ISHARES_URL.format(product=fund["product"], slug=fund["slug"], fund=fund["fund"], date=day)
    response = requests.get(url, headers=BROWSER, timeout=60)
    response.raise_for_status()
    return response.content.decode("utf-8-sig"), url


def download_members_ishares(key):
    text, url = _holdings(key, pd.Timestamp(config.AS_OF))
    table, as_of = parse_holdings(text)
    if len(table) < 15:
        raise RuntimeError(f"Only {len(table)} holdings parsed from {url}")
    _raw(f"holdings_{key}.csv").write_text(text, encoding="utf-8")
    table.insert(0, "ticker", [yahoo_ticker(key, c) for c in table["code"]])
    table.to_csv(members_file(key), index=False)
    _update_manifest(f"members_{key}", source=config.INDICES[key]["members"]["label"] + ", holdings file", url=url,
                     holdings_as_of=as_of, members=len(table))
    return table


def download_holdings_history(key, days):
    """Weights of the fund on past days (one file with every day)."""
    parts = []
    for day in days:
        for back in range(6):                      # the fund has no file for days without trading
            text, _ = _holdings(key, day - pd.Timedelta(days=back))
            table, as_of = parse_holdings(text)
            time.sleep(1.0)
            if len(table):
                parts.append(table.assign(date=day.date(), holdings_as_of=as_of))
                break
    history = pd.concat(parts)[["date", "holdings_as_of", "code", "name", "weight"]]
    history.to_csv(_raw(f"holdings_history_{key}.csv"), index=False)
    _update_manifest(f"holdings_history_{key}", source=config.INDICES[key]["members"]["label"] + ", holdings of past days",
                     url=config.ISHARES_URL.split("?")[0] + "?...&asOfDate=<day>", days=len(parts))
    return history


def read_holdings_history(key):
    path = _raw(f"holdings_history_{key}.csv")
    return pd.read_csv(path, parse_dates=["date"]) if path.exists() else None


def parse_krx_companies(content):
    """The Korea Exchange company list: code (six characters), Korean name and listing date."""
    table = pd.read_html(io.BytesIO(content), encoding="euc-kr", converters={"종목코드": str})[0]
    out = pd.DataFrame({"code": table["종목코드"].astype(str).str.zfill(6), "korean_name": table["회사명"],
                        "listed": pd.to_datetime(table["상장일"])})
    return out.drop_duplicates("code").sort_values("code").reset_index(drop=True)


def download_krx_companies():
    response = requests.get(config.KRX_LIST_URL, headers=BROWSER, timeout=90)
    response.raise_for_status()
    table = parse_krx_companies(response.content)
    if len(table) < 500:
        raise RuntimeError(f"Only {len(table)} companies in the list of the Korea Exchange")
    table.to_csv(_raw("krx_companies.csv"), index=False)
    _update_manifest("krx_companies", source="Korea Exchange (KIND), companies listed on the KOSPI market",
                     url=config.KRX_LIST_URL, companies=len(table))
    return table


def _profile(ticker):
    """Ordinary shares outstanding, name and sector of one company according to Yahoo."""
    import yfinance as yf
    for attempt in range(3):
        try:
            info = yf.Ticker(ticker).info
            break
        except Exception:                                            # not found / rate limit
            info = {}
            time.sleep(2 + 3 * attempt)
    sector = info.get("sector") or ""
    return {"ticker": ticker, "shares": info.get("sharesOutstanding"),
            "name": info.get("longName") or info.get("shortName") or "",
            "sector": SECTOR_NAMES.get(sector, sector)}


def download_krx_profiles(tickers):
    logging.getLogger("yfinance").setLevel(logging.CRITICAL)
    with ThreadPoolExecutor(max_workers=config.YAHOO_WORKERS) as pool:
        table = pd.DataFrame(list(pool.map(_profile, tickers)))
    table.to_csv(_raw("krx_profiles.csv"), index=False)
    _update_manifest("krx_profiles", source="Yahoo Finance", url=YAHOO_URL.format(code="<ticker>"),
                     field="sharesOutstanding (ordinary shares), longName, sector", companies=len(table),
                     without_share_count=int(table["shares"].isna().sum()))
    return table


def kospi_weights(companies, profiles, close, as_of):
    """Members of the KOSPI and their weights: market value of the ordinary shares on `as_of`.

    The KOSPI weighs every company of the market by the value of all its
    ordinary shares, so the weights can be computed. A company is left out if
    Yahoo has no price for it in the days before `as_of` or no share count.
    """
    table = companies.assign(ticker=[yahoo_ticker("KOSPI", c) for c in companies["code"]])
    table = table.merge(profiles, on="ticker", how="left")
    last = close.loc[:as_of].tail(config.STALE_DAYS).ffill().iloc[-1]
    table["close"] = table["ticker"].map(last)
    table["value"] = table["shares"] * table["close"]
    kept = table[table["value"] > 0].copy()
    kept["weight"] = kept["value"] / kept["value"].sum()
    kept["name"] = kept["name"].where(kept["name"] != "", kept["korean_name"])
    kept = kept.sort_values("weight", ascending=False).reset_index(drop=True)
    left_out = table[~(table["value"] > 0)]
    return kept[["ticker", "code", "name", "sector", "weight", "shares", "close"]], left_out


# ---------------------------------------------------------------------------
# Yahoo Finance
# ---------------------------------------------------------------------------
def _yahoo_batch(tickers):
    """Daily close and dividends of a list of tickers: {ticker: table with `close` and `dividends`}.

    The close is adjusted for splits only. Yahoo's own adjusted close is not
    used: for London shares it leaves out almost all the dividends (prices are
    in pence and its adjustment takes the dividends as pounds).
    """
    import yfinance as yf
    end = pd.Timestamp(config.AS_OF) + pd.Timedelta(days=1)          # `end` is exclusive
    df = yf.download(tickers, start=config.DOWNLOAD_START, end=end, auto_adjust=False,
                     actions=True, threads=True, progress=False, group_by="column")
    if df.empty:
        return {}
    if not isinstance(df.columns, pd.MultiIndex):                    # single ticker
        df.columns = pd.MultiIndex.from_product([df.columns, tickers])
    df.index = pd.to_datetime(df.index).tz_localize(None)
    return {t: pd.DataFrame({"close": df[("Close", t)], "dividends": df[("Dividends", t)].fillna(0.0)}).dropna()
            for t in tickers
            if ("Close", t) in df.columns and df[("Close", t)].notna().any()}


def download_yahoo(tickers):
    """Download all tickers in batches; retry the ones that come back empty."""
    logging.getLogger("yfinance").setLevel(logging.CRITICAL)
    data, pending = {}, list(tickers)
    for _ in range(config.YAHOO_RETRIES):
        for i in range(0, len(pending), config.YAHOO_BATCH):
            try:
                data.update(_yahoo_batch(pending[i:i + config.YAHOO_BATCH]))
            except Exception as error:                               # network / rate limit
                print(f"    batch failed ({type(error).__name__}); will retry")
                time.sleep(30)
        pending = [t for t in tickers if t not in data]
        if not pending:
            break
        time.sleep(10)
    return data, pending


def download_prices(key, tickers):
    data, missing = download_yahoo(tickers)
    close = pd.DataFrame({t: d["close"] for t, d in data.items()}).sort_index()
    dividends = pd.DataFrame({t: d["dividends"] for t, d in data.items()}).sort_index()
    close.to_parquet(_raw(f"close_{key}.parquet"))
    dividends.to_parquet(_raw(f"dividends_{key}.parquet"))
    _update_manifest(f"prices_{key}", source="Yahoo Finance, daily close and dividends",
                     url=YAHOO_URL.format(code="<ticker>"), tickers=len(tickers), with_prices=len(data),
                     without_prices=sorted(missing), first_day=str(close.index.min().date()),
                     last_day=str(close.index.max().date()))
    return close, dividends


def read_prices(key):
    """(close, dividends) of every member."""
    return pd.read_parquet(_raw(f"close_{key}.parquet")), pd.read_parquet(_raw(f"dividends_{key}.parquet"))


def _save_series(series, path):
    series.rename("value").rename_axis("date").to_csv(path)


def read_series(path):
    return pd.read_csv(path, parse_dates=["date"]).set_index("date")["value"]


def download_index(key):
    ticker = config.INDICES[key]["index"]
    data, _ = download_yahoo([ticker])
    if ticker not in data:
        raise RuntimeError(f"Yahoo returned nothing for {ticker}")
    _save_series(data[ticker]["close"], _raw(f"index_{key}.csv"))
    _update_manifest(f"index_{key}", source="Yahoo Finance, close of the index (no dividends)",
                     url=YAHOO_URL.format(code=ticker), last_day=str(data[ticker].index.max().date()))


def download_risk_free(key):
    code = config.INDICES[key]["risk_free"]
    url = config.FRED_URL.format(code=code)
    response = requests.get(url, timeout=60)        # FRED rejects requests with a browser User-Agent
    response.raise_for_status()
    table = pd.read_csv(io.StringIO(response.text))
    series = pd.Series(pd.to_numeric(table.iloc[:, 1], errors="coerce").to_numpy(),
                       index=pd.to_datetime(table.iloc[:, 0])).dropna()
    _save_series(series, _raw(f"risk_free_{key}.csv"))
    _update_manifest(f"risk_free_{key}", source=f"FRED {code}: 3-month interbank rate, % per year, monthly (OECD)",
                     url=url, last_month=str(series.index.max().date()))


# ---------------------------------------------------------------------------
def year_ends():
    """The last day of each year between the start of the sample and AS_OF, and AS_OF."""
    start, end = pd.Timestamp(config.SAMPLE_START), pd.Timestamp(config.AS_OF)
    days = [d for d in pd.date_range(start, end, freq="YE") if d < end]
    return [start] + days + [end]


def download_all(refresh=False):
    for folder in (config.RAW_DIR, config.REF_DIR):
        folder.mkdir(parents=True, exist_ok=True)
    as_of = pd.Timestamp(config.AS_OF)
    for key in config.ORDER:
        source = config.INDICES[key]["members"]["source"]
        prices = _raw(f"dividends_{key}.parquet")
        if source == "ishares":
            if refresh or not members_file(key).exists():
                print(f"  {key}: members and weights from the fund")
                download_members_ishares(key)
            if refresh or not _raw(f"holdings_history_{key}.csv").exists():
                print(f"  {key}: weights of the fund at the end of each past year")
                download_holdings_history(key, year_ends())
            if refresh or not prices.exists():
                tickers = pd.read_csv(members_file(key))["ticker"].tolist()
                print(f"  {key}: prices of {len(tickers)} members")
                download_prices(key, tickers)
        elif refresh or not members_file(key).exists():
            # The weights are computed: company list, then prices, then share counts.
            print(f"  {key}: company list of the Korea Exchange")
            companies = download_krx_companies()
            tickers = [yahoo_ticker(key, c) for c in companies["code"]]
            print(f"  {key}: prices of {len(tickers)} companies (a few minutes)")
            close, _ = download_prices(key, tickers)
            print(f"  {key}: share counts of {len(tickers)} companies (several minutes)")
            profiles = download_krx_profiles(tickers).fillna({"name": "", "sector": ""})
            members, left_out = kospi_weights(companies, profiles, close, as_of)
            members.to_csv(members_file(key), index=False)
            _update_manifest(f"members_{key}", source=config.INDICES[key]["members"]["label"],
                             url=config.KRX_LIST_URL, weights_as_of=config.AS_OF, members=len(members),
                             companies_in_the_list=len(companies), left_out=sorted(left_out["code"]))
        elif not prices.exists():
            tickers = pd.read_csv(members_file(key))["ticker"].tolist()
            print(f"  {key}: prices of {len(tickers)} members (a few minutes)")
            download_prices(key, tickers)
        if refresh or not _raw(f"index_{key}.csv").exists():
            download_index(key)
        if refresh or not _raw(f"risk_free_{key}.csv").exists():
            download_risk_free(key)
