#!/usr/bin/env python3
"""Find what Polymarket's top earners hold, and write data.json.
Usage: python fetch_consensus.py [--top 50] [--period MONTH] [--category OVERALL]
Standard library only. Uses Polymarket's public Data API (no key needed).
"""
import argparse, json, os, re, statistics, time, urllib.parse, urllib.request
from collections import Counter, defaultdict
from datetime import datetime, timezone

BASE = "https://data-api.polymarket.com"

# Approximate topic labels from market titles (Polymarket positions carry no category).
CATEGORIES = [
    ("Crypto", ["bitcoin", "btc", "ethereum", "ether ", "solana", "crypto", "xrp", "dogecoin", "coinbase", "binance", "token", "stablecoin"]),
    ("Sports", [" vs. ", " vs ", "nba", "nfl", "mlb", "nhl", "wnba", "ufc", "ncaa", "premier league", "la liga", "serie a", "bundesliga", "champions league", "world cup", "super bowl", "stanley cup", "tennis", "formula 1", "grand prix", "o/u", "spread:", "playoffs", "wimbledon", "open:", "fc "]),
    ("Politics", ["president", "election", "trump", "biden", "harris", "senate", "congress", "governor", "mayor", "democrat", "republican", "prime minister", "parliament", "ukraine", "russia", "israel", "gaza", "iran", "china", "tariff", "supreme court", "nominee", "primary", "vote", "shutdown", "putin", "zelensky"]),
    ("Economy", ["fed ", "interest rate", "rate cut", "inflation", "cpi", "gdp", "recession", "unemployment", "s&p", "nasdaq", "stock", "oil", "gold", "fomc", "treasury"]),
    ("Culture", ["movie", "film", "oscar", "grammy", "emmy", "album", "song", "taylor swift", "box office", "netflix", "celebrity", "tweet", "elon musk", "spotify", "youtube", "tiktok", "openai", "gpt", "ai "]),
]


# Sports leagues. Pass 1 looks for league words, pass 2 for team nicknames.
# Shared nicknames (Giants, Panthers, Jets, Kings...) go to the first league listed, so a few are guesses.
LEAGUE_WORDS = [
    ("College", r"ncaa|college football|college basketball|march madness|cfp|ncaaf|ncaab"),
    ("MMA", r"ufc|mma|fight night|bellator"),
    ("Tennis", r"tennis|atp|wta|wimbledon|us open|french open|australian open|roland garros"),
    ("Soccer", r"premier league|la liga|serie a|bundesliga|ligue 1|champions league|europa league|world cup|mls|fc"),
    ("NHL", r"nhl|stanley cup"),
    ("MLB", r"mlb|world series"),
    ("NBA", r"nba|wnba"),
    ("NFL", r"nfl|super bowl"),
]
LEAGUE_TEAMS = [
    ("NFL", r"cardinals|falcons|ravens|bills|panthers|bears|bengals|browns|cowboys|broncos|lions|packers|texans|colts|jaguars|chiefs|raiders|chargers|rams|dolphins|vikings|patriots|saints|giants|jets|eagles|steelers|49ers|seahawks|buccaneers|titans|commanders"),
    ("NBA", r"hawks|celtics|nets|hornets|bulls|cavaliers|mavericks|nuggets|pistons|warriors|rockets|pacers|clippers|lakers|grizzlies|heat|bucks|timberwolves|pelicans|knicks|thunder|magic|76ers|suns|trail blazers|kings|spurs|raptors|jazz|wizards"),
    ("NHL", r"bruins|maple leafs|canadiens|oilers|flames|canucks|avalanche|blackhawks|red wings|lightning|penguins|capitals|hurricanes|devils|islanders|sabres|senators|blue jackets|wild|golden knights|kraken|ducks|sharks|predators|flyers"),
    ("MLB", r"yankees|dodgers|red sox|mets|braves|astros|phillies|padres|cubs|blue jays|mariners|orioles|guardians|twins|tigers|rays|brewers|white sox|royals|athletics|rockies|diamondbacks|nationals|pirates|reds|marlins|angels"),
]


def classify_league(title):
    t = (title or "").lower()
    for table in (LEAGUE_WORDS, LEAGUE_TEAMS):
        for name, pattern in table:
            if re.search(rf"\b(?:{pattern})\b", t):
                return name
    return None


def classify(title):
    t = f" {(title or '').lower()} "
    for name, words in CATEGORIES:
        if any(w in t for w in words):
            return name
    return "Sports" if classify_league(title) else "Other"


def get(path, **params):
    url = f"{BASE}{path}?{urllib.parse.urlencode(params)}"
    req = urllib.request.Request(url, headers={"User-Agent": "consensus-dashboard/0.2"})
    for attempt in range(3):
        try:
            with urllib.request.urlopen(req, timeout=30) as r:
                return json.load(r)
        except Exception as e:
            if attempt == 2:
                body = ""
                if hasattr(e, "read"):
                    try:
                        body = e.read().decode()[:300]
                    except Exception:
                        pass
                print(f"  failed {path}: {e} {body}")
                return []
            time.sleep(1.5 * (attempt + 1))


def recent_trades(wallet, name, hours, typical, held, entry):
    """Recent trades by one trader, merged per market side. Returns (buys, sells).
    held: {(condition, outcome): current value}; entry: {(condition, outcome): their average buy price}."""
    since = time.time() - hours * 3600
    acts = get("/activity", user=wallet, type="TRADE", limit=500)
    merged = {}
    for a in acts if isinstance(acts, list) else []:
        side = a.get("side")
        if side not in ("BUY", "SELL") or float(a.get("timestamp") or 0) < since:
            continue
        usd = float(a.get("usdcSize") or 0) or float(a.get("size") or 0) * float(a.get("price") or 0)
        k = (side, a.get("conditionId"), a.get("outcome"))
        m = merged.setdefault(k, {"usd": 0.0, "shares": 0.0, "n": 0, "ts": 0,
                                  "title": a.get("title"), "slug": a.get("slug"), "event_slug": a.get("eventSlug")})
        m["usd"] += usd
        m["shares"] += float(a.get("size") or 0)
        m["n"] += 1
        m["ts"] = max(m["ts"], int(float(a.get("timestamp") or 0)))
    buys, sells = [], []
    for (side, cond, outcome), m in merged.items():
        if m["usd"] < 100:
            continue
        price = round(m["usd"] / m["shares"], 3) if m["shares"] else None
        item = {
            "wallet": wallet, "name": name, "conditionId": cond, "outcome": outcome,
            "title": m["title"], "slug": m["slug"], "event_slug": m.get("event_slug"), "usd": round(m["usd"]), "n": m["n"], "ts": m["ts"],
            "price": price, "typical": round(typical),
            "big": bool(typical and m["usd"] >= 3 * typical and m["usd"] >= 500),
        }
        if side == "BUY":
            buys.append(item)
        else:
            still = held.get((cond, outcome)) or 0
            bought = entry.get((cond, outcome))
            item["exit"] = "partial" if still >= 25 else "full"
            item["bought_at"] = round(bought, 3) if bought else None
            item["gain"] = round(price / bought - 1, 3) if (bought and price) else None
            sells.append(item)
    return buys, sells


GAMMA_SEARCH = "https://gamma-api.polymarket.com/public-search"


def find_wallet(name, leaders):
    """Look up a Polymarket user's wallet by username: the leaderboard first, then Polymarket's public profile search."""
    want = name.strip().lower()
    for t in leaders:
        if str(t.get("userName") or "").lower() == want:
            return t["proxyWallet"]
    try:
        qs = urllib.parse.urlencode({"q": name, "search_profiles": "true", "limit_per_type": 10})
        req = urllib.request.Request(f"{GAMMA_SEARCH}?{qs}", headers={"User-Agent": "consensus-dashboard/0.2"})
        with urllib.request.urlopen(req, timeout=30) as r:
            found = json.load(r)
        for p in (found.get("profiles") or []):
            if want in (str(p.get("name") or "").lower(), str(p.get("pseudonym") or "").lower()) and p.get("proxyWallet"):
                return p["proxyWallet"]
    except Exception as e:
        print(f"  profile search failed for {name}: {e}")
    return None


def watch_user(name, leaders, days=7):
    """Every trade a watched user made in the last few days (no minimum size), newest first."""
    wallet = find_wallet(name, leaders)
    out = {"name": name, "wallet": wallet, "found": bool(wallet), "checked_at": int(time.time()), "trades": []}
    if not wallet:
        return out
    since = time.time() - days * 86400
    acts = get("/activity", user=wallet, type="TRADE", limit=500)
    merged = {}
    for a in acts if isinstance(acts, list) else []:
        side = a.get("side")
        if side not in ("BUY", "SELL") or float(a.get("timestamp") or 0) < since:
            continue
        usd = float(a.get("usdcSize") or 0) or float(a.get("size") or 0) * float(a.get("price") or 0)
        k = (side, a.get("conditionId"), a.get("outcome"))
        m = merged.setdefault(k, {"side": side, "conditionId": a.get("conditionId"), "outcome": a.get("outcome"),
                                  "title": a.get("title"), "slug": a.get("slug"), "event_slug": a.get("eventSlug"),
                                  "usd": 0.0, "shares": 0.0, "n": 0, "ts": 0})
        m["usd"] += usd
        m["shares"] += float(a.get("size") or 0)
        m["n"] += 1
        m["ts"] = max(m["ts"], int(float(a.get("timestamp") or 0)))
    for m in merged.values():
        m["price"] = round(m["usd"] / m["shares"], 3) if m["shares"] else None
        m["usd"] = round(m["usd"])
        del m["shares"]
        out["trades"].append(m)
    out["trades"].sort(key=lambda m: -m["ts"])
    out["trades"] = out["trades"][:40]
    return out


US_GATEWAY = "https://gateway.polymarket.us/v1/events"


def norm(text):
    return re.sub(r"[^a-z0-9]+", " ", (text or "").lower()).strip()


def fetch_us_events(page=100, max_pages=60):
    """Open events (games, questions) on Polymarket US with their markets. Public, no login. Returns (events, ok)."""
    events = []
    for i in range(max_pages):
        qs = urllib.parse.urlencode({"limit": page, "offset": i * page, "active": "true", "closed": "false"})
        req = urllib.request.Request(f"{US_GATEWAY}?{qs}", headers={"User-Agent": "consensus-dashboard/0.4"})
        batch = None
        for attempt in range(3):
            try:
                with urllib.request.urlopen(req, timeout=60) as r:
                    batch = json.load(r).get("events") or []
                break
            except Exception as e:
                if attempt == 2:
                    print(f"  Polymarket US event list failed at offset {i * page}: {e}")
                    return events, False
                time.sleep(1.5 * (attempt + 1))
        events += batch
        if len(batch) < page:
            break
    return events, True


def us_path(ev):
    """Page path on polymarket.us. Confirmed only for sports games: /sports/<league>/<game-slug>."""
    slug = ev.get("slug")
    if slug and ev.get("gameId"):
        return f"/sports/{slug.split('-')[0]}/{slug}"
    return None


def us_index(events):
    by_slug, by_title = {}, {}
    for ev in events:
        path = us_path(ev)
        hit = {"slug": ev.get("slug"), "path": path}
        if ev.get("slug"):
            by_slug[ev["slug"]] = hit
        t = norm(ev.get("title"))
        if t:
            by_title.setdefault(t, hit)
        for m in ev.get("markets") or []:
            mhit = {"slug": m.get("slug") or ev.get("slug"), "path": path}
            if m.get("slug"):
                by_slug[m["slug"]] = mhit
            t = norm(m.get("question"))
            if t:
                by_title.setdefault(t, mhit)
    return by_slug, by_title


def us_match(idx, slug, event_slug, title):
    """Return {"slug", "path"} for the matching Polymarket US market, or None. Matches by slug, then exact wording."""
    by_slug, by_title = idx
    for s in (slug, event_slug):
        if s and s in by_slug:
            return by_slug[s]
    return by_title.get(norm(title))


KALSHI_BASES = ("https://external-api.kalshi.com/trade-api/v2", "https://api.elections.kalshi.com/trade-api/v2")
STOP = {"will", "the", "a", "an", "of", "by", "in", "on", "at", "to", "vs", "and", "or", "be", "is", "for", "win", "wins"}


def tokens(text):
    return {w for w in re.findall(r"[a-z0-9]+", (text or "").lower()) if w not in STOP}


def fetch_kalshi_markets(max_pages=40, budget=150):
    """Open markets on Kalshi. Public endpoint, no login. Returns (markets, ok)."""
    start = time.time()
    for base in KALSHI_BASES:
        markets, cursor = [], None
        for _ in range(max_pages):
            if time.time() - start > budget:
                break
            q = {"status": "open", "limit": 1000}
            if cursor:
                q["cursor"] = cursor
            data = None
            for attempt in range(3):
                try:
                    req = urllib.request.Request(f"{base}/markets?{urllib.parse.urlencode(q)}",
                                                 headers={"User-Agent": "consensus-dashboard/0.6"})
                    with urllib.request.urlopen(req, timeout=60) as r:
                        data = json.load(r)
                    break
                except Exception as e:
                    if attempt == 2:
                        print(f"  Kalshi market list failed ({base}): {e}")
                    else:
                        time.sleep(1.5 * (attempt + 1))
            if data is None:
                break
            batch = data.get("markets") or []
            markets += batch
            cursor = data.get("cursor")
            if not cursor or not batch:
                break
        if markets:
            return markets, True
    return [], False


def kalshi_index(markets):
    items, postings = [], defaultdict(list)
    for m in markets:
        toks = tokens(m.get("title"))
        if len(toks) < 2:
            continue
        series = m.get("series_ticker") or (m.get("event_ticker") or "").split("-")[0]
        if not series:
            continue
        i = len(items)
        items.append((toks, {t for t in toks if t.isdigit()}, series, m.get("ticker")))
        for t in toks:
            postings[t].append(i)
    return items, postings


def kalshi_match(idx, title, threshold=0.75):
    """Conservative: nearly identical wording and identical numbers. Returns the Kalshi series page URL or None."""
    items, postings = idx
    q = tokens(title)
    if len(q) < 3:
        return None
    qn = {t for t in q if t.isdigit()}
    hits = Counter()
    for t in q:
        lst = postings.get(t)
        if lst and len(lst) <= 3000:
            hits.update(lst)
    best, best_score = None, 0.0
    for i, n in hits.items():
        if n < 3:
            continue
        toks, nums, series, ticker = items[i]
        if nums != qn:
            continue
        score = len(q & toks) / len(q | toks)
        if score > best_score:
            best, best_score = (series, ticker), score
    if best and best_score >= threshold:
        return {"url": f"https://kalshi.com/markets/{best[0].lower()}", "ticker": best[1]}
    return None


def win_rate(wallet):
    """Share of a trader's 50 most recently closed positions that made money, plus their average buy prices."""
    closed = get("/closed-positions", user=wallet, limit=50,
                 sortBy="TIMESTAMP", sortDirection="DESC")
    if not isinstance(closed, list) or not closed:
        return None, 0, {}
    wins = sum(1 for c in closed if float(c.get("realizedPnl") or 0) > 0)
    avg = {(c.get("conditionId"), c.get("outcome")): float(c.get("avgPrice") or 0)
           for c in closed if c.get("conditionId") and float(c.get("avgPrice") or 0) > 0}
    return round(wins / len(closed), 3), len(closed), avg


def update_history(rows, keep_days=7, min_traders=3):
    """Append this run's trader counts to history.json so we can chart consensus over time."""
    path = "history.json"
    try:
        with open(path) as f:
            hist = json.load(f)
    except Exception:
        hist = {"snaps": []}
    now = datetime.now(timezone.utc)
    counts = {f"{r['conditionId'][:12]}|{r['outcome']}": r["trader_count"]
              for r in rows if r["trader_count"] >= min_traders}
    hist["snaps"].append({"t": now.isoformat(timespec="minutes"), "c": counts})
    cutoff = now.timestamp() - keep_days * 86400
    hist["snaps"] = [s for s in hist["snaps"]
                     if datetime.fromisoformat(s["t"]).timestamp() >= cutoff]
    with open(path, "w") as f:
        json.dump(hist, f, separators=(",", ":"))
    print(f"history.json now has {len(hist['snaps'])} snapshots")


CALLS_FILE = "calls.json"
TIERS = (3, 5, 8)           # "calls" are logged the first time this many top traders hold the same side
GAMMA = "https://gamma-api.polymarket.com/markets/keyset"


def load_calls():
    try:
        with open(CALLS_FILE) as f:
            d = json.load(f)
        d.setdefault("calls", {})
        return d
    except Exception:
        return {"started": datetime.now(timezone.utc).isoformat(timespec="minutes"), "calls": {}}


def contested(r, rows):
    """True when top traders are split across the sides of this market (neither side has 70% of the people or money)."""
    mine = {t["wallet"] for t in r["traders"]}
    opp, oppv = set(), 0.0
    for o in rows:
        if o["conditionId"] == r["conditionId"] and o is not r:
            opp |= {t["wallet"] for t in o["traders"]}
            oppv += o["total_value"]
    if not opp:
        return False
    return len(mine) / (len(mine) + len(opp)) < 0.7 or r["total_value"] / max(1.0, r["total_value"] + oppv) < 0.7


def update_calls(store, rows, pool):
    """Log every market side the first time 3, 5 and 8 top traders hold it, with the price at that moment."""
    now = int(time.time())
    added = 0
    for r in rows:
        n, p = r["trader_count"], r.get("price")
        if n < TIERS[0] or p is None or not (0.05 <= p <= 0.95):
            continue   # skip near-settled prices, where "agreement" tells you nothing
        if contested(r, rows):
            continue   # top traders are on both sides, so this is not a real consensus
        key = f"{pool}|{r['conditionId']}|{r['outcome']}"
        c = store["calls"].get(key)
        if c is None:
            c = store["calls"][key] = {"pool": pool, "cid": r["conditionId"], "o": r["outcome"], "t": r["title"],
                                       "s": r["slug"], "es": r.get("event_slug"), "cat": r["category"], "lg": r.get("league"),
                                       "tiers": {}, "pk": 0, "st": "open"}
            added += 1
        c["pk"] = max(c.get("pk", 0), n)
        c["end"] = r.get("end")
        for tier in TIERS:
            if n >= tier and str(tier) not in c["tiers"]:
                c["tiers"][str(tier)] = {"p": round(p, 3), "ts": now, "n": n}
    return added


def gamma_markets(cids):
    """Look up markets by condition id (includes closed ones). Returns a list, or None if the lookup failed."""
    def call(query):
        req = urllib.request.Request(f"{GAMMA}?{query}", headers={"User-Agent": "consensus-dashboard/0.5"})
        for attempt in range(3):
            try:
                with urllib.request.urlopen(req, timeout=40) as r:
                    return json.load(r).get("markets") or []
            except Exception as e:
                if attempt == 2:
                    print(f"  market lookup failed: {e}")
                    return None
                time.sleep(1.5 * (attempt + 1))
    base = [("closed", "any"), ("limit", 100)]
    out = call(urllib.parse.urlencode([("condition_ids", c) for c in cids] + base))
    if out == [] and len(cids) > 1:      # the docs don't say whether ids are repeated or comma-separated: try both
        out = call(urllib.parse.urlencode([("condition_ids", ",".join(cids))] + base))
    return out


def settle(market, outcome):
    """'won' / 'lost' / 'void' once the market has settled, else None."""
    if not market or not market.get("closed"):
        return None
    try:
        names = [str(x).lower() for x in json.loads(market.get("outcomes") or "[]")]
        prices = [float(x) for x in json.loads(market.get("outcomePrices") or "[]")]
    except Exception:
        return None
    if len(names) != len(prices) or str(outcome).lower() not in names:
        return None
    mine, top = prices[names.index(str(outcome).lower())], max(prices)
    if mine >= 0.99:
        return "won"
    if mine <= 0.01 and top >= 0.99:
        return "lost"
    if all(abs(x - 0.5) <= 0.02 for x in prices):
        return "void"
    return None


def resolve_calls(store, max_ids=160):
    """Ask Polymarket which logged calls have settled. Only checks calls whose end date is near or past."""
    now = time.time()
    def end_ts(c):
        try:
            e = c.get("end") or ""
            return datetime.fromisoformat(e.replace("Z", "+00:00")).timestamp() if len(e) > 10 else \
                   datetime.fromisoformat(e + "T23:59:59+00:00").timestamp()
        except Exception:
            return None
    open_calls = [c for c in store["calls"].values() if c["st"] == "open"]
    due = [c for c in open_calls if end_ts(c) is None or end_ts(c) <= now + 86400]
    due.sort(key=lambda c: min(t["ts"] for t in c["tiers"].values()))
    cids = list(dict.fromkeys(c["cid"] for c in due))[:max_ids]
    settled = failed = 0
    for i in range(0, len(cids), 20):
        chunk = cids[i:i + 20]
        found = gamma_markets(chunk)
        if found is None:
            failed += 1
            continue
        by_id = {str(m.get("conditionId", "")).lower(): m for m in found}
        for c in due:
            if c["cid"] in chunk:
                result = settle(by_id.get(c["cid"].lower()), c["o"])
                if result:
                    c["st"], c["rs"] = result, int(now)
                    settled += 1
        time.sleep(0.3)
    for c in open_calls:
        if c["st"] == "open" and now - min(t["ts"] for t in c["tiers"].values()) > 150 * 86400:
            c["st"] = "stale"
    print(f"Track record: checked {len(cids)} markets, {settled} newly settled, {failed} lookups failed")


def save_calls(store):
    store["updated"] = datetime.now(timezone.utc).isoformat(timespec="minutes")
    with open(CALLS_FILE, "w") as f:
        json.dump(store, f, separators=(",", ":"))


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--top", type=int, default=50)
    ap.add_argument("--period", default="MONTH", choices=["DAY", "WEEK", "MONTH", "ALL"])
    ap.add_argument("--category", default="OVERALL")
    ap.add_argument("--min-value", type=float, default=50)
    ap.add_argument("--out", default="data.json")
    ap.add_argument("--recent-hours", type=int, default=48)
    ap.add_argument("--watch", default="", help="Polymarket username to watch; writes watch.json")
    ap.add_argument("--no-history", action="store_true")
    ap.add_argument("--no-us", action="store_true", help="skip the Polymarket US market list")
    ap.add_argument("--no-kalshi", action="store_true", help="skip the Kalshi market list")
    ap.add_argument("--no-calls", action="store_true", help="do not log consensus calls to calls.json")
    ap.add_argument("--skip-resolve", action="store_true", help="log calls but do not check which have settled")
    args = ap.parse_args()

    leaders = []
    while len(leaders) < args.top:   # the leaderboard returns at most 50 per request, so page through it
        chunk = get("/v1/leaderboard", category=args.category, timePeriod=args.period,
                    orderBy="PNL", limit=min(50, args.top - len(leaders)), offset=len(leaders))
        if not chunk:
            break
        seen = {l.get("proxyWallet") for l in leaders}
        chunk = [l for l in chunk if l.get("proxyWallet") not in seen]
        if not chunk:
            break
        leaders += chunk
    print(f"Tracking {len(leaders)} traders ({args.category}, {args.period}, by profit)")

    groups = defaultdict(lambda: {"traders": [], "value": 0.0, "invested": 0.0, "cost": 0.0, "shares": 0.0})
    trader_rows = []
    us_events, us_ok = ([], False) if args.no_us else fetch_us_events()
    us_idx = us_index(us_events)
    us_count = sum(len(e.get("markets") or []) for e in us_events)
    print(f"Polymarket US: {len(us_events)} open events, {us_count} markets (ok={us_ok})")
    kal_markets, kal_ok = ([], False) if args.no_kalshi else fetch_kalshi_markets()
    kal_idx = kalshi_index(kal_markets)
    print(f"Kalshi: {len(kal_markets)} open markets (ok={kal_ok})")
    recent, exits = [], []
    for i, t in enumerate(leaders, 1):
        wallet = t["proxyWallet"]
        name = t.get("userName") or wallet[:8]
        print(f"[{i}/{len(leaders)}] {name}")
        positions = get("/positions", user=wallet, sizeThreshold=1, limit=500,
                        sortBy="CURRENT", sortDirection="DESC")
        if not isinstance(positions, list):
            positions = []
        print(f"  {len(positions)} positions")
        values = [float(p.get("currentValue") or 0) for p in positions]
        typical = statistics.median(values) if values else 0
        portfolio = sum(values)
        wr, wr_n, closed_avg = win_rate(wallet)
        held = {(p.get("conditionId"), p.get("outcome")): float(p.get("currentValue") or 0) for p in positions}
        entry = {**closed_avg, **{(p.get("conditionId"), p.get("outcome")): float(p.get("avgPrice") or 0)
                                  for p in positions if float(p.get("avgPrice") or 0) > 0}}
        buys, sells = recent_trades(wallet, name, args.recent_hours, typical, held, entry)
        recent += buys
        exits += sells
        trader_rows.append({
            "rank": i, "name": name, "wallet": wallet,
            "pnl": round(float(t.get("pnl") or 0)), "volume": round(float(t.get("vol") or 0)),
            "win_rate": wr, "closed_sample": wr_n, "open_positions": len(positions),
        })
        for p in positions:
            value = float(p.get("currentValue") or 0)
            if value < args.min_value:
                continue
            g = groups[(p["conditionId"], p["outcome"])]
            g.update(title=p.get("title"), slug=p.get("slug"), event_slug=p.get("eventSlug"), outcome=p["outcome"],
                     price=p.get("curPrice"), end=p.get("endDate"))
            g["traders"].append({"name": name, "wallet": wallet, "value": round(value, 2),
                                 "share": round(value / portfolio, 3) if portfolio else 0,
                                 "heavy": bool(typical and value >= 3 * typical and value >= 500)})
            g["value"] += value
            g["invested"] += float(p.get("initialValue") or 0)
            g["cost"] += float(p.get("avgPrice") or 0) * float(p.get("size") or 0)
            g["shares"] += float(p.get("size") or 0)
        time.sleep(0.25)

    rows = []
    for (cond, _), g in groups.items():
        rows.append({
            "conditionId": cond, "title": g["title"], "slug": g["slug"], "event_slug": g.get("event_slug"),
            "outcome": g["outcome"], "price": g["price"], "end": g["end"],
            "category": classify(g["title"]),
            "league": None,
            "trader_count": len(g["traders"]),
            "total_value": round(g["value"], 2),
            "invested": round(g["invested"], 2),
            "avg_entry": round(g["cost"] / g["shares"], 3) if g["shares"] else None,
            "avg_share": round(sum(t["share"] for t in g["traders"]) / len(g["traders"]), 3),
            "heavy": sum(1 for t in g["traders"] if t["heavy"]),
            "traders": sorted(g["traders"], key=lambda x: -x["value"]),
        })
    for r in rows:
        if r["category"] == "Sports":
            r["league"] = classify_league(r["title"]) or "Other sports"
    for r in rows:
        m = us_match(us_idx, r["slug"], r.get("event_slug"), r["title"]) if us_ok else None
        r["us"] = bool(m) if us_ok else None
        r["us_slug"] = m["slug"] if m else None
        r["us_path"] = m["path"] if m else None
    for r in rows:
        m = kalshi_match(kal_idx, r["title"]) if kal_ok else None
        r["kalshi_url"] = m["url"] if m else None
    rows.sort(key=lambda r: (-r["trader_count"], -r["total_value"]))
    held = {(r["conditionId"], r["outcome"]): r for r in rows}
    for b in recent + exits:
        g = held.get((b["conditionId"], b["outcome"]))
        b["holders"] = g["trader_count"] if g else None
        b["now"] = g["price"] if g else None
        m = us_match(us_idx, b["slug"], b.get("event_slug"), b["title"]) if us_ok else None
        b["us"] = bool(m) if us_ok else None
        b["us_slug"] = m["slug"] if m else None
        b["us_path"] = m["path"] if m else None
        m = kalshi_match(kal_idx, b["title"]) if kal_ok else None
        b["kalshi_url"] = m["url"] if m else None
        b["category"] = classify(b["title"])
        b["league"] = (classify_league(b["title"]) or "Other sports") if b["category"] == "Sports" else None
    recent.sort(key=lambda b: -b["ts"])
    exits.sort(key=lambda b: -b["ts"])

    if not args.no_calls:
        store = load_calls()
        added = update_calls(store, rows, args.category)
        if not args.skip_resolve:
            resolve_calls(store)
        save_calls(store)
        print(f"calls.json: {len(store['calls'])} calls logged ({added} new this run)")

    out = {"generated_at": datetime.now(timezone.utc).isoformat(),
           "trader_pool": len(leaders), "period": args.period, "category": args.category,
           "traders": trader_rows, "positions": rows, "recent": recent[:400], "exits": exits[:400],
           "kalshi": {"ok": bool(kal_ok), "markets": len(kal_markets), "matched": sum(1 for r in rows if r.get("kalshi_url")), "total": len(rows)},
           "us": {"ok": bool(us_ok and us_events), "markets": us_count,
                  "matched": sum(1 for r in rows if r.get("us")), "total": len(rows)}}
    with open(args.out, "w") as f:
        json.dump(out, f)
    if args.watch:
        w = watch_user(args.watch, leaders)
        with open("watch.json", "w") as f:
            json.dump(w, f)
        print(f"watch.json: {args.watch} -> {'wallet found' if w['found'] else 'NOT FOUND'}, {len(w['trades'])} trades in the last 7 days")
    if not args.no_history:
        update_history(rows)
    print(f"Wrote {args.out} with {len(rows)} position groups")


if __name__ == "__main__":
    main()
