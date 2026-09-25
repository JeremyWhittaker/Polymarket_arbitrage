"""Frozen prior-season MLB home win expectancy, identical to hypotheses._baseline_we.

Levels [inn_k, half, outs, bases, diff_k] -> [inn_k, half, diff_k] -> [diff_k]; a cell needs
>= 30 observations and is shrunk as (wins + 5) / (n + 10); otherwise 0.5. inn_k = min(inning, 10),
diff_k = clip(diff, -7, 7), seasons <= max_season. The JSON is written with sorted keys so a
rebuild from the same baseline yields the same bytes and SHA-256.
"""
from __future__ import annotations

import hashlib
import json
from functools import lru_cache
from pathlib import Path

import numpy as np
import pandas as pd

from ..collect import DATA_DIR

BASELINE = DATA_DIR / "mlb" / "baseline.parquet"
SNAP_DIR = DATA_DIR / "paper"
COLS = ["season", "inning", "half", "outs", "bases", "diff", "home_won_final"]
LEVELS = [["inn_k", "half", "outs", "bases", "diff_k"], ["inn_k", "half", "diff_k"], ["diff_k"]]
MIN_N, PRIOR_WINS, PRIOR_N, FALLBACK = 30, 5, 10, 0.5


def _key(values) -> str:
    return "|".join(str(v) for v in values)


def snapshot_path(max_season: int, snap_dir: Path = SNAP_DIR) -> Path:
    return Path(snap_dir) / f"mlb_fair_{int(max_season)}.json"


def sha256_file(path: Path) -> str:
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def build_snapshot(max_season: int, baseline: Path | pd.DataFrame = BASELINE, snap_dir: Path | None = SNAP_DIR) -> dict:
    """Tabulate every cell exactly as _baseline_we does; save JSON unless snap_dir is None."""
    b = baseline if isinstance(baseline, pd.DataFrame) else pd.read_parquet(baseline, columns=COLS)
    b = b[b.season <= max_season].copy()
    b["inn_k"] = np.minimum(b.inning, 10)
    b["diff_k"] = np.clip(b["diff"], -7, 7)
    b["y"] = b.home_won_final.astype(float)
    levels = []
    for keys in LEVELS:
        t = b.groupby(keys).y.agg(["sum", "size"])
        t = t[t["size"] >= MIN_N]
        rate = (t["sum"] + PRIOR_WINS) / (t["size"] + PRIOR_N)
        idx = t.index if len(keys) > 1 else [(k,) for k in t.index]
        cells = {_key(k): float(r) for k, r in zip(idx, rate.to_numpy())}
        levels.append({"keys": keys, "cells": dict(sorted(cells.items()))})
    snap = {"model": "baseline_we_v1", "max_season": int(max_season), "min_n": MIN_N,
            "shrinkage": [PRIOR_WINS, PRIOR_N], "fallback": FALLBACK, "rows": int(len(b)),
            "seasons": sorted(int(s) for s in b.season.unique()), "levels": levels}
    if not isinstance(baseline, pd.DataFrame):
        snap["baseline_sha256"] = sha256_file(baseline)
    return save_snapshot(snap, snap_dir) if snap_dir is not None else snap


def snapshot_bytes(snap: dict) -> bytes:
    return json.dumps({k: v for k, v in snap.items() if k not in ("path", "sha256")},
                      sort_keys=True, separators=(",", ":")).encode()


def save_snapshot(snap: dict, snap_dir: Path = SNAP_DIR) -> dict:
    path = snapshot_path(snap["max_season"], snap_dir)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".json.tmp")
    tmp.write_bytes(snapshot_bytes(snap))
    tmp.replace(path)
    return snap | {"path": str(path), "sha256": sha256_file(path)}


class Snapshot:
    def __init__(self, data: dict, sha256: str | None = None, path: str | None = None):
        self.max_season, self.sha256, self.path = int(data["max_season"]), sha256, path
        self.levels = [(lv["keys"], lv["cells"]) for lv in data["levels"]]
        self.fallback = float(data.get("fallback", FALLBACK))

    @classmethod
    def load(cls, max_season: int, snap_dir: Path = SNAP_DIR) -> "Snapshot":
        path = snapshot_path(max_season, snap_dir)
        raw = path.read_bytes()
        return cls(json.loads(raw), hashlib.sha256(raw).hexdigest(), str(path))

    def fair_home(self, inning: int, half: str, outs: int, bases: int, diff: int) -> float:
        row = {"inn_k": min(int(inning), 10), "half": str(half).lower(), "outs": int(outs),
               "bases": int(bases), "diff_k": int(np.clip(int(diff), -7, 7))}
        for keys, cells in self.levels:
            v = cells.get(_key(row[k] for k in keys))
            if v is not None:
                return v
        return self.fallback


@lru_cache(maxsize=8)
def _cached(max_season: int, snap_dir: str) -> Snapshot:
    return Snapshot.load(max_season, Path(snap_dir))


def fair_home(inning, half, outs, bases, diff, max_season: int, snap_dir: Path = SNAP_DIR) -> float:
    return _cached(int(max_season), str(snap_dir)).fair_home(inning, half, outs, bases, diff)
