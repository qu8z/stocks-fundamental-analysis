# Stocks Fundamental Analysis

A small local app that grades any US-listed company on 8 fundamentals (out of 15
points), pulling real numbers straight from SEC EDGAR — or from an uploaded
factsheet PDF.

## Why this needs to run locally

Browsers block webpages from directly reading data off other sites (a security
rule called CORS) — SEC EDGAR included. The earlier single-file version worked
around that with a public relay, which is flaky. This version fixes it properly:
a tiny Python server fetches SEC data **server-to-server** (no browser involved,
so no CORS restriction applies) and serves it to the page over `localhost`.

## Requirements

- Python 3.8+ (already installed on most Mac/Linux systems; on Windows, install
  from [python.org](https://python.org))
- No third-party packages — standard library only, nothing to `pip install`

## Run it

1. Unzip this folder somewhere.
2. Open a terminal in that folder.
3. Run:
   ```
   python3 server.py
   ```
   (On Windows, this may just be `python server.py`.)
4. Open **http://localhost:8765** in your browser.
5. Leave the terminal window open while you use the app — closing it stops the server.

## Using it

- **Search Company** — type a name or ticker (e.g. "Apple" or "AAPL"), pick a
  suggestion, and it pulls that company's latest 10-K figures and scores them.
- **Drop PDF** — drag in a factsheet PDF (Jitta-style factsheets work best);
  parsing happens entirely in your browser, no server round-trip needed.
- Every value in the results table is editable — fix a misread number or fill
  in anything the tool couldn't find, and the score updates live.

## Notes on the numbers

- Revenue, EPS, and Dividends per Share come straight from what companies
  report to the SEC.
- ROE, Debt/Equity, Free Cash Flow per Share, and Interest Coverage are
  **calculated** from raw filing figures (e.g. Debt/Equity = Total Liabilities
  ÷ Stockholders' Equity) since companies don't report those as a single line
  item. These are tagged "SEC · derived" in the table — worth a glance before
  trusting them blindly, since methodology can vary from other providers.
- SEC EDGAR only covers US-listed filers. Non-US companies won't be found —
  use the PDF upload instead.

## Customizing

- **Port**: change `PORT = 8765` near the top of `server.py`.
- **User-Agent**: SEC asks automated tools to identify themselves. Feel free
  to edit `USER_AGENT` in `server.py` to include your own contact info — it's
  considered good etiquette, though not required for light personal use.
- **Scoring rubric**: the 8 metrics and their point rules are defined in the
  `METRICS` array in `index.html` (frontend) and mirrored in `compute_metrics()`
  in `server.py` (backend) — edit both if you want to change the rules.

## Deploying it online (so it's a real website, not just localhost)

This app is one Python file with no dependencies, which makes it easy to deploy
to any host that can run a Python process and give it a public URL.

### The reliable way: Render Blueprint (recommended)

A `render.yaml` file is included in this folder — it tells Render exactly how
to build and run the app, so there's no "Root Directory" or "Start Command"
box to fill in by hand (and get wrong).

1. **Get the code onto GitHub**, with all five files — `server.py`,
   `index.html`, `README.md`, `requirements.txt`, `render.yaml` — sitting
   directly at the **top level** of the repo. Not inside a subfolder. To
   check: open your repo on GitHub; you should see all five files listed
   immediately on the repo's main page, with no folder to click into first.
   If you see a folder instead, that's the bug — go into it, and re-upload
   the files at the top level instead (Add file → Upload files, from the
   repo's root page, not from inside a folder).
2. At [render.com](https://render.com), click **New +** → **Blueprint**
   (not "Web Service" this time).
3. Connect the repo. Render reads `render.yaml` automatically and fills in
   every setting for you — build command, start command, plan, all of it.
4. Click **Apply** / **Create**. It deploys with the correct configuration
   guaranteed, since it's coming from the file, not manual entry.

### The manual way: Web Service

If you'd rather configure by hand instead of using the Blueprint:

1. Same GitHub step as above — files at the repo's top level.
2. Render → **New +** → **Web Service** → connect the repo.
3. Settings:
   - **Runtime**: Python 3
   - **Build Command**: `echo "no build needed"`
   - **Start Command**: `python3 server.py`
   - **Instance Type**: Free
4. **Create Web Service**.


**Free tier caveat**: Render's free web services "sleep" after 15 minutes of
no traffic. The first request after sleeping takes 30–60 seconds to wake back
up — normal, not a bug. Paid tiers remove this.

**Alternatives** if you outgrow Render's free tier or want something different:
[Railway](https://railway.app) and [Fly.io](https://fly.io) work almost
identically (connect a repo, set the start command, deploy). Because
`server.py` already reads the `PORT` environment variable and binds to
`0.0.0.0` when it's set, no code changes are needed to move between hosts.

## Why it might feel slow (and what to do about it)

Two separate things can cause slowness, worth telling apart:

**1. Free-tier cold start.** Render's free instances go to sleep after 15
minutes with no traffic and take 30–60 seconds to wake up on the next visit.
This is a one-time wait per sleep cycle, not a bug. Options if it bothers you:
- Use a free uptime pinger like [UptimeRobot](https://uptimerobot.com) to hit
  your URL every 10 minutes, which keeps it from sleeping (note: this uses up
  your free instance hours faster).
- Upgrade to Render's paid **Starter** tier (~$7/mo) — no sleeping, more CPU,
  consistently fast.

**2. Large SEC data on a slow connection.** A big company's full filing
history (`companyfacts` JSON) can be several megabytes. This version now
requests it gzip-compressed, which should cut that transfer time
significantly — but on a free instance with limited CPU/bandwidth, a large
cap like Apple or Microsoft will still be slower than a smaller company.
Results are cached in memory for an hour, so **the second search for the same
company should be fast** — it's the first one that pays the full cost.

The server now prints timing info to Render's logs (e.g.
`[analyze] AAPL total 3.42s`) — if it's still slow after this update, check
those logs (Render dashboard → your service → **Logs**) and share the timing
lines with me; that'll show exactly which step is the bottleneck.

## Optional: independent cross-check via Alpha Vantage

By default the app uses two sources: SEC EDGAR (fundamentals) and Stooq
(price). You can add a third, real, independent source — [Alpha
Vantage](https://www.alphavantage.co) — to see Revenue, EPS, ROE, and
Debt/Equity compared side by side against what SEC EDGAR shows.

This is **off by default** and entirely optional. Setup:

1. Get a free key at
   [alphavantage.co/support/#api-key](https://www.alphavantage.co/support/#api-key)
   (just an email address, no card).
2. Open `server.py` and find this line near the top:
   ```python
   ALPHA_VANTAGE_KEY_HARDCODED = ""
   ```
   Paste your key between the quotes, e.g.:
   ```python
   ALPHA_VANTAGE_KEY_HARDCODED = "ABC123YOURKEYHERE"
   ```
3. Save the file, restart the server (`python3 server.py`), or on Render,
   commit the change and let it redeploy.

**Important — this alone won't change anything on the page.** The key only
does something the *next time you search a company*. Setting it doesn't
change the homepage, the empty state, or anything visible until you actually
run a search — at that point, if the key is working, a blue "Independent
Cross-Check" panel appears above the scorecard table. No search = nothing to
see yet, that's expected.

**Free tier limit**: 25 requests/day (2 requests per analysis), so treat this
as an occasional spot-check rather than something to run on every search.
If the panel still doesn't appear after a real search, check the terminal —
there's a log line (`[alpha_vantage] check failed: ...`) that will say why
(usually an invalid key or the daily limit).

## Not investment advice

This tool is for exploring public filings faster, not a recommendation
engine. Always sanity-check the numbers against the actual SEC filing before
acting on them.
