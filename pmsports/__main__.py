"""python -m pmsports <command>

  games        discover Polymarket MLB events, pull MLB schedule, join -> data/mlb/games.parquet
  fetch        per-game plays + 1-min prices + tick trades (resumable; rerun to fill gaps)
  mlb-history  plays for extra MLB seasons (baseline win-expectancy only, no market data)
  panel        build the analysis panel (game state x market price) -> data/mlb/panel.parquet
  report       run every hypothesis test -> reports/
  audit        data coverage / quality summary
  record       live capture: order books + Polymarket score feed + MLB linescore
  live-latency per-scoring-play latency from a recorded day (ms clocks)
"""
from __future__ import annotations

import argparse
import logging


def main() -> None:
    ap = argparse.ArgumentParser(prog="pmsports")
    sub = ap.add_subparsers(dest="cmd", required=True)
    sub.add_parser("games")
    f = sub.add_parser("fetch")
    f.add_argument("--what", default="plays,prices,trades")
    f.add_argument("--workers", type=int, default=6)
    f.add_argument("--since")
    f.add_argument("--until")
    f.add_argument("--limit", type=int)
    h = sub.add_parser("mlb-history")
    h.add_argument("--start", required=True)
    h.add_argument("--end", required=True)
    sub.add_parser("panel")
    r = sub.add_parser("report")
    r.add_argument("--split-date", default=None,
                   help="train on games before this date, test on/after (default: 2026-01-01)")
    sub.add_parser("audit")
    rc = sub.add_parser("record")
    rc.add_argument("--hours", type=float, default=6.0)
    rc.add_argument("--window", type=float, default=4.0, help="subscribe to games starting within N hours")
    ll = sub.add_parser("live-latency")
    ll.add_argument("--day", required=True, help="UTC date folder under data/live/")
    a = ap.parse_args()

    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")

    if a.cmd == "games":
        from .collect import build_games
        build_games()
    elif a.cmd == "fetch":
        from .collect import fetch_games
        fetch_games(what=a.what.split(","), workers=a.workers, since=a.since, until=a.until, limit=a.limit)
    elif a.cmd == "mlb-history":
        from .collect import fetch_mlb_history
        fetch_mlb_history(a.start, a.end)
    elif a.cmd == "panel":
        from .panel import build_panel
        build_panel()
    elif a.cmd == "report":
        from .analysis.report import run_all
        run_all(split_date=a.split_date or "2026-01-01")
    elif a.cmd == "audit":
        from .audit import audit
        audit()
    elif a.cmd == "record":
        from .record import record
        record(a.hours, a.window)
    elif a.cmd == "live-latency":
        from .analysis.live import live_latency
        df = live_latency(a.day)
        if df.empty:
            print("no scoring plays with book data yet")
        else:
            print(df.round(2).to_string(index=False))
            print(df[["book_t50", "sports_t", "mlb_t", "spread"]].describe().round(2).to_string())


if __name__ == "__main__":
    main()
