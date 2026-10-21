#!/usr/bin/env python3
"""Find what Polymarket's top earners hold, and write data.json.
Usage: python fetch_consensus.py [--top 50] [--period MONTH] [--category OVERALL]
Standard library only. Uses Polymarket's public Data API (no key needed).
"""
import argparse, json, time, urllib.parse, urllib.request
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


def classify(title):
    t = f" {(title or '').lower()} "
    for name, words in CATEGORIES:
        if any(w in t for w in words):
            return name
    return "Other"


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


def win_rate(wallet):
    """Share of a trader's 50 most recently closed positions that made money."""
    closed = get("/closed-positions", user=wallet, limit=50,
                 sortBy="TIMESTAMP", sortDirection="DESC")
    if not isinstance(closed, list) or not closed:
        return None, 0
    wins = sum(1 for c in closed if float(c.get("realizedPnl") or 0) > 0)
    return round(wins / len(closed), 3), len(closed)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--top", type=int, default=50)
    ap.add_argument("--period", default="MONTH", choices=["DAY", "WEEK", "MONTH", "ALL"])
    ap.add_argument("--category", default="OVERALL")
    ap.add_argument("--min-value", type=float, default=50)
    args = ap.parse_args()

    leaders = get("/v1/leaderboard", category=args.category, timePeriod=args.period,
                  orderBy="PNL", limit=min(args.top, 50))
    print(f"Tracking {len(leaders)} traders ({args.category}, {args.period}, by profit)")

    groups = defaultdict(lambda: {"traders": [], "value": 0.0, "cost": 0.0, "shares": 0.0})
    trader_rows = []
    for i, t in enumerate(leaders, 1):
        wallet = t["proxyWallet"]
        name = t.get("userName") or wallet[:8]
        print(f"[{i}/{len(leaders)}] {name}")
        positions = get("/positions", user=wallet, sizeThreshold=1, limit=500,
                        sortBy="CURRENT", sortDirection="DESC")
        if not isinstance(positions, list):
            positions = []
        print(f"  {len(positions)} positions")
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
            g.update(title=p.get("title"), slug=p.get("slug"), outcome=p["outcome"],
                     price=p.get("curPrice"), end=p.get("endDate"))
            g["traders"].append({"name": name, "wallet": wallet, "value": round(value, 2)})
            g["value"] += value
            g["cost"] += float(p.get("avgPrice") or 0) * float(p.get("size") or 0)
            g["shares"] += float(p.get("size") or 0)
        time.sleep(0.25)

    rows = []
    for (cond, _), g in groups.items():
        rows.append({
            "conditionId": cond, "title": g["title"], "slug": g["slug"],
            "outcome": g["outcome"], "price": g["price"], "end": g["end"],
            "category": classify(g["title"]),
            "trader_count": len(g["traders"]),
            "total_value": round(g["value"], 2),
            "avg_entry": round(g["cost"] / g["shares"], 3) if g["shares"] else None,
            "traders": sorted(g["traders"], key=lambda x: -x["value"]),
        })
    rows.sort(key=lambda r: (-r["trader_count"], -r["total_value"]))

    out = {"generated_at": datetime.now(timezone.utc).isoformat(),
           "trader_pool": len(leaders), "period": args.period, "category": args.category,
           "traders": trader_rows, "positions": rows}
    with open("data.json", "w") as f:
        json.dump(out, f)
    print(f"Wrote data.json with {len(rows)} position groups")


if __name__ == "__main__":
    main()
