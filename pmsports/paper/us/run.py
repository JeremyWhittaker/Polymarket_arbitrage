"""US engine driver and activation (`python -m pmsports paper-us run [--follow] | settle | report | activate`).

`run` resumes from 36 h before data/paper_us/checkpoint.json and skips decision keys already
written, so a restart is deterministic. `run` and `settle` hold the output directory's exclusive
lock. Follow mode attaches the preview auditor (unless --no-preview), settles + reports every 15
minutes on a side thread into data/paper_us/PAPER_TEST.md (and the desk ledgers), and exits
cleanly on SIGTERM (logs closed, queued previews drained). Batch runs never preview.
"""
from __future__ import annotations

import json
import logging
import threading
import time
from datetime import datetime, timezone
from pathlib import Path

from ..engine import LIVE, ROOT, SNAPSHOT_LOG, WARMUP_MS, atomic_write, load_json, out_lock
from ..mlb_model import SNAP_DIR, sha256_file
from ..run import _checkpoint, _git, to_ms
from ..spec import plain
from . import spec
from .engine import ACTIVATION_US, PAPER_US, USEngine, USReplayer, code_files, code_hashes, support_hashes

log = logging.getLogger("pmsports")
PERIODIC_S = 900
CHECKPOINT_S = 60
PROTOCOLS = ["reports/PROSPECTIVE_US_VENUE_PROTOCOL.md", "reports/PROSPECTIVE_PROTOCOL.md",
             "reports/PROSPECTIVE_SOCCER_PROTOCOL.md", "pmsports/paper/DESIGN.md", "pmsports/paper/us/DESIGN.md"]


def run(follow: bool = False, since: str | None = None, until: str | None = None, live: Path = LIVE,
        out: Path = PAPER_US, activation: Path | None = ACTIVATION_US, snapshot_log: Path | None = SNAPSHOT_LOG,
        model_dir: Path | None = None, allow_network: bool = False, periodic=None, preview=None, replayer=USReplayer) -> dict:
    """Replay (and optionally follow) the capture; returns counters."""
    with out_lock(out):
        return _run(follow, since, until, Path(live), Path(out), activation, snapshot_log, model_dir, allow_network,
                    periodic, preview, replayer)


def _run(follow, since, until, live, out, activation, snapshot_log, model_dir, allow_network, periodic, preview, replayer):
    start = to_ms(since)
    if start is None:
        last = (load_json(out / "checkpoint.json", {}) or {}).get("last_recv_ms")
        start = None if last is None else int(last) - WARMUP_MS
    end = to_ms(until)
    eng = USEngine(out, live, activation, snapshot_log, model_dir, allow_network=allow_network, preview=preview)
    rp = replayer(live, start, end, follow=follow)
    n, t0, tick, worker = 0, time.time(), time.time(), None
    log.info("paper-us run: start=%s end=%s follow=%s code=%s", start, end, follow, eng.code_sha[:12])
    try:
        for ms, stream, rec in rp:
            if stream is None:
                eng.advance(ms)
                if time.time() - tick >= CHECKPOINT_S:
                    _checkpoint(out, eng.last_ms)
                    if periodic and time.time() - t0 >= PERIODIC_S and (worker is None or not worker.is_alive()):
                        # settle + report on a side thread: the engine (and preview timing) never waits for them
                        worker = threading.Thread(target=_guarded, args=(periodic,), name="us-periodic", daemon=True)
                        worker.start()
                        t0 = time.time()
                    tick = time.time()
                continue
            eng.process(ms, stream, rec)
            n += 1
            if n % 1_000_000 == 0:
                log.info("paper-us run: %d records, at %s, %d pending, %d decisions", n,
                         datetime.fromtimestamp(ms / 1000, timezone.utc).isoformat(), len(eng.pending), len(eng.decisions.keys))
        eng.finish(end, now_ms=rp.scan_ms)
    finally:
        _checkpoint(out, eng.last_ms)          # also on SIGTERM: a restart replays 36 h before it anyway
        eng.close()
    return dict(records=n, counts=eng.counts, pending=len(eng.pending), decisions=len(eng.decisions.keys),
                signals=len(eng.signals.keys), replay=rp.stats(), record_errors=eng.errors, last_ms=eng.last_ms)


def _guarded(fn) -> None:
    try:
        fn()
    except Exception as exc:                          # never stop the engine for reporting
        log.warning("paper-us periodic settle/report failed: %s", exc)


def activate(path: Path = ACTIVATION_US, snap_dir: Path = SNAP_DIR, force: bool = False, allow_dirty: bool = False) -> dict:
    """Write reports/paper/ACTIVATION_US.json (one-time; a rule/code change needs a new version)."""
    path = Path(path)
    if path.exists() and not force:
        raise SystemExit(f"{path} already exists; activation is one-time (use a new version)")
    tracked = [str(p.relative_to(ROOT)) for p in code_files()] + PROTOCOLS
    dirty = _git("status", "--porcelain", "--", *tracked)
    if dirty and not allow_dirty:
        raise SystemExit(f"commit the US engine first; uncommitted:\n{dirty}")
    snaps = {p.stem.rsplit("_", 1)[-1]: sha256_file(p) for p in sorted(Path(snap_dir).glob("mlb_fair_*.json"))}
    if not snaps:
        raise SystemExit("no MLB model snapshot; run `python -m pmsports paper snapshot --max-season 2025` first")
    h, now = code_hashes(), time.time()
    manifest = dict(version=spec.VERSION, venue=spec.VENUE, activated_utc=datetime.fromtimestamp(now, timezone.utc).isoformat(),
                    activated_ms=int(now * 1000), git_commit=_git("rev-parse", "HEAD"), git_dirty=dirty.splitlines(),
                    code_files=h["files"], taker_fee_sha256=h["taker_fee_sha256"], code_sha256=h["code_sha256"],
                    spec_sha256=spec.spec_hash(), rules={p: spec.rule_hash(p) for p in spec.POLICIES},
                    rules_json={p: plain(spec.RULES[p]) for p in spec.POLICIES},
                    model_snapshots=snaps, protocols={p: sha256_file(ROOT / p) for p in PROTOCOLS if (ROOT / p).exists()},
                    support_files=support_hashes())
    path.parent.mkdir(parents=True, exist_ok=True)
    atomic_write(path, json.dumps(manifest, indent=1, sort_keys=True))
    log.info("US paper test activated %s at %s", manifest["code_sha256"][:12], manifest["activated_utc"])
    return manifest


def main(a) -> None:
    """Dispatch `python -m pmsports paper-us <action>`."""
    from . import report, settle
    out, live = Path(a.out), Path(a.live)
    if a.action == "run":
        preview = None
        if a.follow:
            import signal
            import sys
            signal.signal(signal.SIGTERM, lambda *_: sys.exit(0))   # service stop: close logs, drain previews
            if not a.no_preview:
                from .preview import PreviewAuditor
                preview = PreviewAuditor(out / "previews.jsonl")

        def periodic():
            settle.settle(out, live)
            report.build(out, live, out / "PAPER_TEST.md")      # live copy; reports/ holds committed snapshots

        try:
            summary = run(a.follow, a.since, a.until, live, out, model_dir=Path(a.model_dir) if a.model_dir else None,
                          periodic=periodic if a.follow else None, preview=preview)
        finally:
            if preview is not None:
                preview.close()
        print(json.dumps(summary, default=str))
    elif a.action == "settle":
        from ..engine import PAPER
        with out_lock(out):
            print(json.dumps(settle.settle(out, live, intl=Path(a.intl_settlements) if a.intl_settlements else PAPER / "settlements.json")))
    elif a.action == "report":
        ledgers = Path(a.ledgers) if a.ledgers else report.LEDGERS
        report.build(out, live, Path(a.report) if a.report else report.REPORT, ledgers)
    elif a.action == "activate":
        print(json.dumps(activate(force=a.force), indent=1))
