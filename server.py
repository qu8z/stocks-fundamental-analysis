#!/usr/bin/env python3
"""
Stocks Fundamental Analysis — local app server
------------------------------------------------
Fetches real filings from SEC EDGAR *server-side* (browsers can't do this
directly — SEC's API doesn't send the CORS headers a browser requires).
Running this script removes the need for any public CORS relay.

Usage:
    python3 server.py
    then open http://localhost:8765

No third-party packages required — standard library only.
"""
import gzip
import json
import os
import re
import threading
import time
import mimetypes
import urllib.request
import urllib.error
import urllib.parse
from datetime import date
from pathlib import Path
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

# Cloud hosts (Render, Railway, Fly, etc.) inject a PORT env var and expect
# the app to bind to 0.0.0.0. Locally, default to localhost:8765.
PORT = int(os.environ.get("PORT", 8765))
HOST = "0.0.0.0" if "PORT" in os.environ else "localhost"

# SEC asks that automated requests identify themselves. Edit this if you like —
# it just needs to look like a real app/contact, not a browser UA string.
USER_AGENT = "StocksFundamentalAnalysis/1.0 (personal research tool)"

# ============================================================================
# OPTIONAL: paste your free Alpha Vantage API key between the quotes below.
# Get one free (just an email, no card) at:
#   https://www.alphavantage.co/support/#api-key
# Leave it as "" (empty) to skip this feature — the rest of the app works
# fine either way, this only adds a bonus comparison panel when it's set.
ALPHA_VANTAGE_KEY_HARDCODED = "5USD4432R6508Y3U"
# ============================================================================

# (Advanced/optional: if you'd rather set it as an environment variable
# instead of editing this file, that still works and takes priority over
# the line above.)
ALPHA_VANTAGE_KEY = os.environ.get("ALPHA_VANTAGE_KEY", ALPHA_VANTAGE_KEY_HARDCODED).strip()

ROOT = Path(__file__).parent
CACHE_TTL = 60 * 60  # 1 hour

_ticker_cache = {"list": None, "loaded_at": 0}
_facts_cache = {}  # cik -> (facts_json, timestamp)


# ---------------------------------------------------------------------------
# SEC EDGAR access (server-to-server — no CORS applies here at all)
# ---------------------------------------------------------------------------
def sec_get_json(url):
    # Requesting gzip matters a lot here: a large company's companyfacts JSON
    # can be several MB uncompressed but a fraction of that compressed, which
    # is the single biggest lever on a slow/free-tier connection.
    req = urllib.request.Request(url, headers={
        "User-Agent": USER_AGENT,
        "Accept": "application/json",
        "Accept-Encoding": "gzip",
    })
    t0 = time.time()
    with urllib.request.urlopen(req, timeout=25) as resp:
        raw = resp.read()
        if resp.info().get("Content-Encoding") == "gzip":
            raw = gzip.decompress(raw)
    print(f"[sec_get_json] {url} -> {len(raw)/1024:.0f} KB in {time.time()-t0:.2f}s")
    return json.loads(raw.decode("utf-8"))


def get_ticker_list():
    now = time.time()
    if _ticker_cache["list"] is not None and now - _ticker_cache["loaded_at"] < CACHE_TTL:
        return _ticker_cache["list"]
    data = sec_get_json("https://www.sec.gov/files/company_tickers.json")
    entries = [
        {"cik": str(row["cik_str"]).zfill(10), "ticker": row["ticker"].upper(), "title": row["title"]}
        for row in data.values()
    ]
    _ticker_cache["list"] = entries
    _ticker_cache["loaded_at"] = now
    return entries


def get_ticker_dict():
    return {e["ticker"]: e for e in get_ticker_list()}


def search_companies(q, limit=8):
    q = (q or "").strip().lower()
    if len(q) < 2:
        return []
    scored = []
    for e in get_ticker_list():
        t, n = e["ticker"].lower(), e["title"].lower()
        if t == q:
            rank = 0
        elif t.startswith(q):
            rank = 1
        elif n.startswith(q):
            rank = 2
        elif q in n:
            rank = 3
        else:
            continue
        scored.append((rank, len(e["title"]), e))
    scored.sort(key=lambda x: (x[0], x[1]))
    return [e for _, _, e in scored[:limit]]


def get_company_facts(cik):
    now = time.time()
    cached = _facts_cache.get(cik)
    if cached and now - cached[1] < CACHE_TTL:
        return cached[0]
    facts = sec_get_json(f"https://data.sec.gov/api/xbrl/companyfacts/CIK{cik}.json")
    _facts_cache[cik] = (facts, now)
    return facts


def get_quote(ticker):
    """Independent cross-check price, from Stooq — a second, separate data source."""
    try:
        url = f"https://stooq.com/q/l/?s={urllib.parse.quote(ticker.lower())}.us&f=sd2t2ohlcv&h&e=csv"
        req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
        with urllib.request.urlopen(req, timeout=10) as resp:
            text = resp.read().decode("utf-8").strip()
        lines = text.splitlines()
        if len(lines) < 2:
            return None
        headers_row = lines[0].split(",")
        values = lines[1].split(",")
        row = dict(zip(headers_row, values))
        close = row.get("Close")
        if not close or close in ("N/D", ""):
            return None
        return {"price": close, "date": row.get("Date"), "source": "Stooq"}
    except Exception:
        return None


def get_alpha_vantage_check(ticker):
    """Optional independent second source (real API, not scraped) for a
    handful of metrics, so the SEC-derived numbers have something to be
    checked against. Returns None entirely if no key is configured, or if
    the free tier's 25/day limit has been hit."""
    if not ALPHA_VANTAGE_KEY:
        return None

    def av_get(function):
        url = (f"https://www.alphavantage.co/query?function={function}"
               f"&symbol={urllib.parse.quote(ticker)}&apikey={ALPHA_VANTAGE_KEY}")
        req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT, "Accept-Encoding": "gzip"})
        with urllib.request.urlopen(req, timeout=20) as resp:
            raw = resp.read()
            if resp.info().get("Content-Encoding") == "gzip":
                raw = gzip.decompress(raw)
            return json.loads(raw.decode("utf-8"))

    def f(x):
        try:
            return float(x)
        except (TypeError, ValueError):
            return None

    try:
        income = av_get("INCOME_STATEMENT")
        balance = av_get("BALANCE_SHEET")
        inc_reports = (income or {}).get("annualReports", [])[:2]   # newest first
        bal_reports = (balance or {}).get("annualReports", [])[:2]
        if not inc_reports or not bal_reports:
            return None  # likely an invalid symbol, or the daily rate limit was hit

        rev = [f(r.get("totalRevenue")) for r in inc_reports]
        ni = [f(r.get("netIncome")) for r in inc_reports]
        eq = [f(r.get("totalShareholderEquity")) for r in bal_reports]
        liab = [f(r.get("totalLiabilities")) for r in bal_reports]
        shares = [f(r.get("commonStockSharesOutstanding")) for r in bal_reports]

        out = {"source": "Alpha Vantage"}
        if len(rev) >= 2 and rev[0] is not None and rev[1] is not None:
            out["revenue"] = {"prior": round(rev[1] / 1e6, 2), "latest": round(rev[0] / 1e6, 2)}
        if ni and eq and ni[0] is not None and eq[0]:
            out["roe"] = {"latest": round((ni[0] / eq[0]) * 100, 2)}
        if liab and eq and liab[0] is not None and eq[0]:
            out["debtEquity"] = {"latest": round(liab[0] / eq[0], 2)}
        if len(ni) >= 2 and len(shares) >= 2 and all(v not in (None, 0) for v in [ni[0], ni[1], shares[0], shares[1]]):
            out["eps"] = {"prior": round(ni[1] / shares[1], 2), "latest": round(ni[0] / shares[0], 2)}

        return out if len(out) > 1 else None
    except Exception as e:
        print(f"[alpha_vantage] check failed (non-fatal): {e}")
        return None


# ---------------------------------------------------------------------------
# Fundamental metric computation (same 8-point rubric as the frontend)
# ---------------------------------------------------------------------------
CONCEPTS = {
    "revenue": ["Revenues", "RevenueFromContractWithCustomerExcludingAssessedTax", "SalesRevenueNet"],
    "eps": ["EarningsPerShareDiluted", "EarningsPerShareBasicAndDiluted", "EarningsPerShareBasic"],
    "netIncome": ["NetIncomeLoss"],
    "equity": ["StockholdersEquity", "StockholdersEquityIncludingPortionAttributableToNoncontrollingInterest"],
    "liabilities": ["Liabilities"],
    "shares": ["CommonStockSharesOutstanding", "EntityCommonStockSharesOutstanding"],
    "dividendsPerShare": ["CommonStockDividendsPerShareDeclared", "CommonStockDividendsPerShareCashPaid"],
    "cfo": ["NetCashProvidedByUsedInOperatingActivities", "NetCashProvidedByUsedInOperatingActivitiesContinuingOperations"],
    "capex": ["PaymentsToAcquirePropertyPlantAndEquipment", "PaymentsForCapitalImprovements"],
    "operatingIncome": ["OperatingIncomeLoss"],
    "interestExpense": ["InterestExpense", "InterestExpenseDebt", "InterestAndDebtExpense"],
}


def series_for(facts, names, duration):
    for name in names:
        for tax in ("us-gaap", "dei"):
            concept = facts.get("facts", {}).get(tax, {}).get(name)
            if not concept or "units" not in concept:
                continue
            for unit_entries in concept["units"].values():
                entries = [e for e in unit_entries if str(e.get("form", "")).startswith("10-K")]
                if duration:
                    filtered = []
                    for e in entries:
                        if not e.get("start") or not e.get("end"):
                            continue
                        try:
                            days = (date.fromisoformat(e["end"]) - date.fromisoformat(e["start"])).days
                        except Exception:
                            continue
                        if 340 < days < 390:
                            filtered.append(e)
                    entries = filtered
                else:
                    entries = [e for e in entries if e.get("end")]
                if entries:
                    best = {}
                    for e in entries:
                        k = e["end"]
                        if k not in best or (e.get("filed", "") > best[k].get("filed", "")):
                            best[k] = e
                    ordered = sorted(best.values(), key=lambda e: e["end"])
                    return [{"end": e["end"], "val": e["val"]} for e in ordered]
    return []


def by_year(series):
    return {e["end"][:4]: e["val"] for e in series}


def pair_from(arr):
    if not arr:
        return {"prior": None, "latest": None, "hasPrior": False, "hasLatest": False}
    if len(arr) == 1:
        return {"prior": None, "latest": round(arr[0]["val"], 2), "hasPrior": False, "hasLatest": True}
    return {"prior": round(arr[-2]["val"], 2), "latest": round(arr[-1]["val"], 2), "hasPrior": True, "hasLatest": True}


def single_from(val):
    if val is None:
        return {"prior": None, "latest": None, "hasPrior": False, "hasLatest": False}
    return {"prior": None, "latest": round(val, 2), "hasPrior": False, "hasLatest": True}


def closest_match(target_end, series, tolerance_days=120):
    """Find the value in `series` whose end-date is closest to target_end,
    within tolerance_days. Matching on nearby dates (not calendar year) is
    what makes this work — share counts, for example, are often reported as
    of the 10-K cover-page filing date, which can land in a different
    calendar year than the fiscal year end it actually describes."""
    try:
        target = date.fromisoformat(target_end)
    except Exception:
        return None
    best_val, best_diff = None, None
    for e in series:
        try:
            d = date.fromisoformat(e["end"])
        except Exception:
            continue
        diff = abs((d - target).days)
        if diff <= tolerance_days and (best_diff is None or diff < best_diff):
            best_val, best_diff = e["val"], diff
    return best_val


def safe_div(a, b, mult=1):
    if a is None or b is None or b == 0:
        return None
    return (a / b) * mult


def pair_from_computed(series_a, series_b, fn, tolerance_days=120):
    combined = []
    for e in series_a:
        b_val = closest_match(e["end"], series_b, tolerance_days)
        if b_val is not None:
            v = fn(e["val"], b_val)
            if v is not None:
                combined.append({"end": e["end"], "val": v})
    return pair_from(combined[-2:])


def pair_from_computed3(series_a, series_b, series_c, fn, tolerance_days=120):
    combined = []
    for e in series_a:
        b_val = closest_match(e["end"], series_b, tolerance_days)
        c_val = closest_match(e["end"], series_c, tolerance_days)
        if b_val is not None and c_val is not None:
            v = fn(e["val"], b_val, c_val)
            if v is not None:
                combined.append({"end": e["end"], "val": v})
    return pair_from(combined[-2:])


def ratio_latest(numerator_series, denominator_series, mult=1, tolerance_days=60):
    """Latest-period ratio, aligned to the same fiscal date on both sides —
    instead of blindly taking each series' own most recent entry, which can
    silently pair figures from two different fiscal periods."""
    if not denominator_series:
        return None
    last_den = denominator_series[-1]
    num_val = closest_match(last_den["end"], numerator_series, tolerance_days)
    return safe_div(num_val, last_den["val"], mult)


def compute_metrics(facts):
    rev = series_for(facts, CONCEPTS["revenue"], True)
    eps = series_for(facts, CONCEPTS["eps"], True)
    ni = series_for(facts, CONCEPTS["netIncome"], True)
    eq = series_for(facts, CONCEPTS["equity"], False)
    liab = series_for(facts, CONCEPTS["liabilities"], False)
    shares = series_for(facts, CONCEPTS["shares"], False)
    dps = series_for(facts, CONCEPTS["dividendsPerShare"], True)
    cfo = series_for(facts, CONCEPTS["cfo"], True)
    capex = series_for(facts, CONCEPTS["capex"], True)
    op_inc = series_for(facts, CONCEPTS["operatingIncome"], True)
    int_exp = series_for(facts, CONCEPTS["interestExpense"], True)

    entries = {}
    rev_pair = pair_from(rev[-2:])
    entries["revenue"] = {
        **rev_pair,
        "prior": round(rev_pair["prior"] / 1e6, 2) if rev_pair["prior"] is not None else None,
        "latest": round(rev_pair["latest"] / 1e6, 2) if rev_pair["latest"] is not None else None,
        "source": "sec",
    }
    entries["eps"] = {**pair_from(eps[-2:]), "source": "sec"}
    entries["roe"] = {**single_from(ratio_latest(ni, eq, 100, tolerance_days=45)), "source": "sec-computed"}
    entries["bvps"] = {**pair_from_computed(eq, shares, lambda e, s: e / s if s else None), "source": "sec-computed"}
    entries["fcf"] = {**pair_from_computed3(cfo, capex, shares, lambda c, x, s: (c - x) / s if s else None), "source": "sec-computed"}
    entries["interestCoverage"] = {**single_from(ratio_latest(op_inc, int_exp, 1, tolerance_days=60)), "source": "sec-computed"}
    entries["debtEquity"] = {**single_from(ratio_latest(liab, eq, 1, tolerance_days=45)), "source": "sec-computed"}
    entries["dividends"] = {**pair_from(dps[-2:]), "source": "sec"}
    return entries


# ---------------------------------------------------------------------------
# HTTP server
# ---------------------------------------------------------------------------
class Handler(BaseHTTPRequestHandler):
    def log_message(self, fmt, *args):
        pass  # keep the console quiet

    def _json(self, obj, status=200):
        body = json.dumps(obj).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _file(self, path, content_type=None):
        try:
            body = path.read_bytes()
        except FileNotFoundError:
            self.send_error(404, "Not found")
            return
        self.send_response(200)
        self.send_header("Content-Type", content_type or mimetypes.guess_type(str(path))[0] or "application/octet-stream")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        parsed = urllib.parse.urlparse(self.path)
        path, qs = parsed.path, urllib.parse.parse_qs(parsed.query)

        if path in ("/", "/index.html"):
            self._file(ROOT / "index.html", "text/html; charset=utf-8")
            return

        if path == "/api/status":
            self._json({"alphaVantageConfigured": bool(ALPHA_VANTAGE_KEY)})
            return

        if path == "/api/search":
            q = qs.get("q", [""])[0]
            try:
                self._json({"results": search_companies(q)})
            except urllib.error.HTTPError as e:
                self._json({"error": f"SEC EDGAR returned HTTP {e.code}"}, 502)
            except Exception as e:
                self._json({"error": str(e)}, 502)
            return

        if path == "/api/analyze":
            ticker = qs.get("ticker", [""])[0].strip().upper()
            if not ticker:
                self._json({"error": "Missing ticker."}, 400)
                return
            try:
                hit = get_ticker_dict().get(ticker)
                if not hit:
                    self._json({"error": f'"{ticker}" isn\'t a US-listed SEC filer.'}, 404)
                    return
                t0 = time.time()
                facts = get_company_facts(hit["cik"])
                metrics = compute_metrics(facts)
                quote = get_quote(ticker)
                alt_check = get_alpha_vantage_check(ticker)
                print(f"[analyze] {ticker} total {time.time()-t0:.2f}s")
                self._json({"company": hit, "metrics": metrics, "quote": quote, "altCheck": alt_check})
            except urllib.error.HTTPError as e:
                self._json({"error": f"SEC EDGAR returned HTTP {e.code} for that filer."}, 502)
            except Exception as e:
                self._json({"error": str(e)}, 500)
            return

        self.send_error(404, "Not found")


def _prewarm():
    try:
        t0 = time.time()
        get_ticker_list()
        print(f"[prewarm] ticker list ready in {time.time()-t0:.2f}s")
    except Exception as e:
        print(f"[prewarm] failed (will load on first request instead): {e}")


def main():
    threading.Thread(target=_prewarm, daemon=True).start()
    server = ThreadingHTTPServer((HOST, PORT), Handler)
    display_host = "localhost" if HOST == "localhost" else "0.0.0.0"
    print(f"Stocks Fundamental Analysis running at http://{display_host}:{PORT}")
    print("Press Ctrl+C to stop.")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nStopped.")


if __name__ == "__main__":
    main()
