"""Coverage / quality summary of what has been collected."""
from __future__ import annotations

import glob

import pandas as pd

from .collect import sport_dir


def audit(sport: str = "mlb") -> None:
    d = sport_dir(sport)
    g = pd.read_parquet(d / "games.parquet")
    g["season"] = g.event_date.str[:4]
    g["month"] = g.event_date.str[:7]
    have = {w: {int(p.split("/")[-1][:-8]) for p in glob.glob(str(d / w / "*.parquet"))}
            for w in ("plays", "prices", "trades")}
    ok = g[g.game_pk.notna()].copy()
    ok["game_pk"] = ok.game_pk.astype(int)
    for w, s in have.items():
        ok[w] = ok.game_pk.isin(s)
    print(f"polymarket events: {len(g)}  matched: {len(ok)}  resolved: {int(g.home_won.notna().sum())}  "
          f"orientation-fixed: {int(g.get('pm_orientation_swapped', pd.Series(dtype=bool)).sum())}  "
          f"resolution mismatches: {int(g.resolution_mismatch.sum())}")
    t = ok.groupby("month").agg(games=("game_pk", "size"), plays=("plays", "sum"), prices=("prices", "sum"),
                                trades=("trades", "sum"), median_volume=("volume", "median"),
                                fee_rate=("fee_rate", "max"))
    print(t.to_string())
    if have["trades"]:
        n = pd.Series({pk: len(pd.read_parquet(d / "trades" / f"{pk}.parquet", columns=["timestamp"]))
                       for pk in list(have["trades"])[:400]})
        print(f"\ntrades per game (sample of {len(n)}): median {n.median():.0f}, p10 {n.quantile(.1):.0f}, "
              f"p90 {n.quantile(.9):.0f}")
