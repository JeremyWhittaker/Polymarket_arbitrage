"""Engine driver, activation manifest and model snapshot commands (`python -m pmsports paper ...`).

`run` resumes from 36 h before the last processed recv_ms (checkpoint.json) and relies on the
decision log's keys to skip decisions already written, so a restart is deterministic. `run` and
`settle` hold an exclusive lock on the output directory, so two writers never share one ledger.
"""
from __future__ import annotations

import hashlib
import json
import logging
import subprocess
import time
from datetime import datetime, timezone
from pathlib import Path

import pandas as pd

from . import spec
from .engine import (ACTIVATION, LIVE, PAPER, ROOT, SNAPSHOT_LOG, WARMUP_MS, Engine, Replayer, atomic_write,
                     code_files, code_hashes, load_json, out_lock, read_jsonl, support_hashes)
from .mlb_model import SNAP_DIR, build_snapshot, save_snapshot, sha256_file, snapshot_bytes, snapshot_path

log = logging.getLogger("pmsports")
PERIODIC_S = 900            # follow mode: settle + report every 15 minutes
PROTOCOLS = ["reports/PROSPECTIVE_PROTOCOL.md", "reports/PROSPECTIVE_SOCCER_PROTOCOL.md", "pmsports/paper/DESIGN.md"]


def to_ms(iso: str | None) -> int | None:
    if not iso:
        return None
    t = pd.Timestamp(iso)
    return int((t.tz_localize("UTC") if t.tzinfo is None else t).value // 1_000_000)


def _checkpoint(out: Path, last_ms: int | None) -> None:
    path = out / "checkpoint.json"
    old = (load_json(path, {}) or {}).get("last_recv_ms")
    if last_ms is None or (old is not None and old >= last_ms):
        return
    atomic_write(path, json.dumps({"last_recv_ms": int(last_ms)}))


def run(follow: bool = False, since: str | None = None, until: str | None = None, live: Path = LIVE,
        out: Path = PAPER, activation: Path | None = ACTIVATION, snapshot_log: Path | None = SNAPSHOT_LOG,
        model_dir: Path | None = None, allow_network: bool = True, periodic=None) -> dict:
    """Replay (and optionally follow) the capture; returns counters."""
    with out_lock(out):
        return _run(follow, since, until, live, Path(out), activation, snapshot_log, model_dir, allow_network, periodic)


def _run(follow, since, until, live, out, activation, snapshot_log, model_dir, allow_network, periodic) -> dict:
    start = to_ms(since)
    if start is None:
        last = (load_json(out / "checkpoint.json", {}) or {}).get("last_recv_ms")
        start = None if last is None else int(last) - WARMUP_MS
    end = to_ms(until)
    eng = Engine(out, live, activation, snapshot_log, model_dir, allow_network=allow_network)
    rp = Replayer(live, start, end, follow=follow)
    n, t0, tick = 0, time.time(), time.time()
    log.info("paper run: start=%s end=%s follow=%s code=%s", start, end, follow, eng.code_sha[:12])
    try:
        for ms, stream, rec in rp:
            if stream is None:
                eng.advance(ms)
                if time.time() - tick >= 60:
                    _checkpoint(out, eng.last_ms)
                    if periodic and time.time() - t0 >= PERIODIC_S:
                        try:
                            periodic()
                        except Exception as exc:      # never stop the engine for reporting
                            log.warning("periodic settle/report failed: %s", exc)
                        t0 = time.time()
                    tick = time.time()
                continue
            eng.process(ms, stream, rec)
            n += 1
            if n % 1_000_000 == 0:
                log.info("paper run: %d records, at %s, %d pending, %d decisions", n,
                         datetime.fromtimestamp(ms / 1000, timezone.utc).isoformat(), len(eng.pending), len(eng.decisions.keys))
        eng.finish(end, now_ms=rp.scan_ms)     # no later record can be in a file the replay has not seen
        _checkpoint(out, eng.last_ms)
    finally:
        eng.close()
    return dict(records=n, counts=eng.counts, pending=len(eng.pending), decisions=len(eng.decisions.keys),
                signals=len(eng.signals.keys), replay=rp.stats(), book_errors=eng.state.errors, record_errors=eng.errors,
                last_ms=eng.last_ms)


def snapshot(max_season: int, snap_dir: Path = SNAP_DIR, log_path: Path = SNAPSHOT_LOG, force: bool = False) -> dict:
    """Build/log the frozen model snapshot; never silently replace a different existing one."""
    path = snapshot_path(max_season, snap_dir)
    snap = build_snapshot(max_season, snap_dir=None)
    if path.exists() and not force and hashlib.sha256(snapshot_bytes(snap)).hexdigest() != sha256_file(path):
        raise SystemExit(f"{path} exists with a different hash; pass --force to replace it (new version)")
    snap = save_snapshot(snap, snap_dir)
    if snap["sha256"] not in {r.get("sha256") for r in read_jsonl(log_path)}:
        Path(log_path).parent.mkdir(parents=True, exist_ok=True)
        rel = str(path.relative_to(ROOT)) if path.is_relative_to(ROOT) else str(path)
        with open(log_path, "a") as f:
            f.write(json.dumps(dict(max_season=int(max_season), sha256=snap["sha256"], path=rel,
                                    baseline_sha256=snap.get("baseline_sha256"), logged_ms=int(time.time() * 1000))) + "\n")
    log.info("MLB snapshot %s sha256 %s", path, snap["sha256"])
    return snap


def _git(*args) -> str:
    return subprocess.run(["git", *args], cwd=ROOT, capture_output=True, text=True, check=True).stdout.strip()


def activate(path: Path = ACTIVATION, snap_dir: Path = SNAP_DIR, force: bool = False, allow_dirty: bool = False) -> dict:
    """Write the activation manifest (one-time; a rule/code change needs a new version)."""
    path = Path(path)
    if path.exists() and not force:
        raise SystemExit(f"{path} already exists; activation is one-time (use a new version)")
    tracked = [str(p.relative_to(ROOT)) for p in code_files()] + PROTOCOLS
    dirty = _git("status", "--porcelain", "--", *tracked)
    if dirty and not allow_dirty:
        raise SystemExit(f"commit the engine first; uncommitted:\n{dirty}")
    snaps = {p.stem.rsplit("_", 1)[-1]: sha256_file(p) for p in sorted(Path(snap_dir).glob("mlb_fair_*.json"))}
    if not snaps:
        raise SystemExit("no MLB model snapshot; run `python -m pmsports paper snapshot --max-season 2025` first")
    h, now = code_hashes(), time.time()
    manifest = dict(version=spec.VERSION, activated_utc=datetime.fromtimestamp(now, timezone.utc).isoformat(),
                    activated_ms=int(now * 1000), git_commit=_git("rev-parse", "HEAD"), git_dirty=dirty.splitlines(),
                    code_files=h["files"], taker_fee_sha256=h["taker_fee_sha256"], code_sha256=h["code_sha256"],
                    spec_sha256=spec.spec_hash(), rules={p: spec.rule_hash(p) for p in spec.POLICIES},
                    rules_json={p: spec.plain(spec.RULES[p]) for p in spec.POLICIES}, model_snapshots=snaps,
                    protocols={p: sha256_file(ROOT / p) for p in PROTOCOLS if (ROOT / p).exists()},
                    support_files=support_hashes())
    path.parent.mkdir(parents=True, exist_ok=True)
    atomic_write(path, json.dumps(manifest, indent=1, sort_keys=True))
    log.info("activated %s at %s", manifest["code_sha256"][:12], manifest["activated_utc"])
    return manifest


def main(a) -> None:
    """Dispatch `python -m pmsports paper <action>`."""
    from . import report, settle
    out, live = Path(a.out), Path(a.live)
    if a.action == "run":
        def periodic():
            settle.settle(out, live)
            report.build(out, live)
        summary = run(a.follow, a.since, a.until, live, out, model_dir=Path(a.model_dir) if a.model_dir else None,
                      periodic=periodic if a.follow else None)
        print(json.dumps({k: v for k, v in summary.items()}, default=str))
    elif a.action == "settle":
        with out_lock(out):              # the follow service settles under its own lock
            print(json.dumps(settle.settle(out, live)))
    elif a.action == "report":
        ledgers = Path(a.ledgers) if a.ledgers else report.LEDGERS
        report.build(out, live, Path(a.report) if a.report else report.REPORT, ledgers)
    elif a.action == "snapshot":
        s = snapshot(a.max_season, Path(a.model_dir) if a.model_dir else SNAP_DIR, force=a.force)
        print(s["path"], s["sha256"])
    elif a.action == "activate":
        print(json.dumps(activate(force=a.force), indent=1))
