#!/usr/bin/env python3
"""Find what Polymarket's top earners are holding, and write data.json.

Usage:  python fetch_consensus.py [--top 50] [--period MONTH] [--category OVERALL]
No third-party packages needed. Uses Polymarket's public Data API (no key).
"""
import argparse, json, time, urllib.parse, urllib.request
from collections import defaultdict
from datetime import datetime, timezone

BASE = "https://data-api.polymarket.com"


def get(path, **params):
    url = f"{BASE}{path}?{urllib.parse.urlencode(params)}"
    req = urllib.request.Request(url, headers={"User-Agent": "consensus-dashboard/0.1"})
    for attempt in range(3):
        try:
            with urllib.request.urlopen(req, timeout=30) as r:
                return json.load(r)
        except Exception as e:
            if attempt == 2:
                print(f"  failed {path}: {e}")
                return []
            time.sleep(1.5 * (attempt + 1))


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--top", type=int, default=50, help="how many top earners to track (API max is 50)")
    ap.add_argument("--period", default="MONTH", choices=["DAY", "WEEK", "MONTH", "ALL"])
    ap.add_argument("--category", default="OVERALL", help="OVERALL, POLITICS, SPORTS, CRYPTO, ...")
    ap.add_argument("--min-value", type=float, default=50, help="ignore positions worth less than this ($)")
    args = ap.parse_args()

    traders = get("/v1/leaderboard", category=args.category, timePeriod=args.period,
                  orderBy="PNL", limit=min(args.top, 50))
    print(f"Tracking {len(traders)} traders ({args.category}, {args.period}, by profit)")

    groups = defaultdict(lambda: {"traders": [], "value": 0.0, "cost": 0.0, "shares": 0.0})
    for i, t in enumerate(traders, 1):
        wallet = t["proxyWallet"]
        name = t.get("userName") or wallet[:8]
        print(f"[{i}/{len(traders)}] {name}")
        positions = get("/positions", user=wallet, sizeThreshold=1, limit=500,
                        sortBy="CURRENTVALUE", sortDirection="DESC")
        for p in positions:
            value = float(p.get("currentValue") or 0)
            if value < args.min_value:
                continue
            key = (p["conditionId"], p["outcome"])
            g = groups[key]
            g.update(title=p.get("title"), slug=p.get("slug"), outcome=p["outcome"],
                     price=p.get("curPrice"), end=p.get("endDate"))
            g["traders"].append({"name": name, "wallet": wallet, "value": round(value, 2)})
            g["value"] += value
            g["cost"] += float(p.get("avgPrice") or 0) * float(p.get("size") or 0)
            g["shares"] += float(p.get("size") or 0)
        time.sleep(0.25)  # be polite to the API

    rows = []
    for (cond, _), g in groups.items():
        rows.append({
            "conditionId": cond, "title": g["title"], "slug": g["slug"],
            "outcome": g["outcome"], "price": g["price"], "end": g["end"],
            "trader_count": len(g["traders"]),
            "total_value": round(g["value"], 2),
            "avg_entry": round(g["cost"] / g["shares"], 3) if g["shares"] else None,
            "traders": sorted(g["traders"], key=lambda x: -x["value"]),
        })
    rows.sort(key=lambda r: (-r["trader_count"], -r["total_value"]))

    out = {"generated_at": datetime.now(timezone.utc).isoformat(),
           "trader_pool": len(traders), "period": args.period, "category": args.category,
           "positions": rows}
    with open("data.json", "w") as f:
        json.dump(out, f)
    print(f"Wrote data.json with {len(rows)} position groups")


if __name__ == "__main__":
    main()
