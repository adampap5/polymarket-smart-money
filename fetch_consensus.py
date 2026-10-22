#!/usr/bin/env python3
"""Find what Polymarket's top earners hold, and write data.json.
Usage: python fetch_consensus.py [--top 50] [--period MONTH] [--category OVERALL]
Standard library only. Uses Polymarket's public Data API (no key needed).
"""
import argparse, json, os, re, statistics, time, urllib.parse, urllib.request
from collections import defaultdict
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


def recent_buys(wallet, name, hours, typical):
    """Recent BUY trades by one trader, merged per market side."""
    since = time.time() - hours * 3600
    acts = get("/activity", user=wallet, type="TRADE", limit=500)
    merged = {}
    for a in acts if isinstance(acts, list) else []:
        if a.get("side") != "BUY" or float(a.get("timestamp") or 0) < since:
            continue
        usd = float(a.get("usdcSize") or 0) or float(a.get("size") or 0) * float(a.get("price") or 0)
        k = (a.get("conditionId"), a.get("outcome"))
        m = merged.setdefault(k, {"usd": 0.0, "shares": 0.0, "n": 0, "ts": 0,
                                  "title": a.get("title"), "slug": a.get("slug"), "event_slug": a.get("eventSlug")})
        m["usd"] += usd
        m["shares"] += float(a.get("size") or 0)
        m["n"] += 1
        m["ts"] = max(m["ts"], int(float(a.get("timestamp") or 0)))
    out = []
    for (cond, outcome), m in merged.items():
        if m["usd"] < 100:
            continue
        out.append({
            "wallet": wallet, "name": name, "conditionId": cond, "outcome": outcome,
            "title": m["title"], "slug": m["slug"], "event_slug": m.get("event_slug"), "usd": round(m["usd"]), "n": m["n"], "ts": m["ts"],
            "price": round(m["usd"] / m["shares"], 3) if m["shares"] else None,
            "typical": round(typical),
            "big": bool(typical and m["usd"] >= 3 * typical and m["usd"] >= 500),
        })
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


def win_rate(wallet):
    """Share of a trader's 50 most recently closed positions that made money."""
    closed = get("/closed-positions", user=wallet, limit=50,
                 sortBy="TIMESTAMP", sortDirection="DESC")
    if not isinstance(closed, list) or not closed:
        return None, 0
    wins = sum(1 for c in closed if float(c.get("realizedPnl") or 0) > 0)
    return round(wins / len(closed), 3), len(closed)


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


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--top", type=int, default=50)
    ap.add_argument("--period", default="MONTH", choices=["DAY", "WEEK", "MONTH", "ALL"])
    ap.add_argument("--category", default="OVERALL")
    ap.add_argument("--min-value", type=float, default=50)
    ap.add_argument("--out", default="data.json")
    ap.add_argument("--recent-hours", type=int, default=48)
    ap.add_argument("--no-history", action="store_true")
    ap.add_argument("--no-us", action="store_true", help="skip the Polymarket US market list")
    args = ap.parse_args()

    leaders = get("/v1/leaderboard", category=args.category, timePeriod=args.period,
                  orderBy="PNL", limit=min(args.top, 50))
    print(f"Tracking {len(leaders)} traders ({args.category}, {args.period}, by profit)")

    groups = defaultdict(lambda: {"traders": [], "value": 0.0, "invested": 0.0, "cost": 0.0, "shares": 0.0})
    trader_rows = []
    us_events, us_ok = ([], False) if args.no_us else fetch_us_events()
    us_idx = us_index(us_events)
    us_count = sum(len(e.get("markets") or []) for e in us_events)
    print(f"Polymarket US: {len(us_events)} open events, {us_count} markets (ok={us_ok})")
    recent = []
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
        recent += recent_buys(wallet, name, args.recent_hours, typical)
        wr, wr_n = win_rate(wallet)
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
            g["traders"].append({"name": name, "wallet": wallet, "value": round(value, 2)})
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
    rows.sort(key=lambda r: (-r["trader_count"], -r["total_value"]))
    held = {(r["conditionId"], r["outcome"]): r for r in rows}
    for b in recent:
        g = held.get((b["conditionId"], b["outcome"]))
        b["holders"] = g["trader_count"] if g else None
        b["now"] = g["price"] if g else None
        m = us_match(us_idx, b["slug"], b.get("event_slug"), b["title"]) if us_ok else None
        b["us"] = bool(m) if us_ok else None
        b["us_slug"] = m["slug"] if m else None
        b["us_path"] = m["path"] if m else None
        b["category"] = classify(b["title"])
        b["league"] = (classify_league(b["title"]) or "Other sports") if b["category"] == "Sports" else None
    recent.sort(key=lambda b: -b["ts"])

    out = {"generated_at": datetime.now(timezone.utc).isoformat(),
           "trader_pool": len(leaders), "period": args.period, "category": args.category,
           "traders": trader_rows, "positions": rows, "recent": recent[:400],
           "us": {"ok": bool(us_ok and us_events), "markets": us_count,
                  "matched": sum(1 for r in rows if r.get("us")), "total": len(rows)}}
    with open(args.out, "w") as f:
        json.dump(out, f)
    if not args.no_history:
        update_history(rows)
    print(f"Wrote {args.out} with {len(rows)} position groups")


if __name__ == "__main__":
    main()
