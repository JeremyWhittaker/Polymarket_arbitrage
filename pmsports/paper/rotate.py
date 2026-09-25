"""Compress finished capture days: data/live/<day>/*.jsonl -> *.jsonl.gz (run from a timer).

Only days at least two UTC days old are touched, never today or yesterday. Each file is
compressed to <name>.jsonl.gz.tmp and fsynced; the temporary file is read back and must hold
exactly the original's lines and bytes; only then is it renamed into place and the original
deleted. A crash at any point leaves the original or a verified .gz (or both, resolved on
the next run). Days that frozen research modules read as plain files are left alone.
Readers of a capture day use open_capture(), which falls back to the rotated .gz.

    python -m pmsports.paper.rotate [--root data/live] [--dry-run]
"""
from __future__ import annotations

import argparse
import gzip
import logging
import os
import re
import time
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

from ..collect import DATA_DIR

log = logging.getLogger("pmsports")
MIN_AGE_DAYS = 2
QUIET_S = 3600            # skip a file modified within the last hour (a writer may still hold it)
KEEP_DAYS = frozenset({"2026-09-18", "2026-09-19"})   # read as plain .jsonl by frozen research code
DAY_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")


def open_capture(day_dir: Path, name: str):
    """Text handle of <day_dir>/<name>.jsonl, or of its rotated <name>.jsonl.gz."""
    p = Path(day_dir) / f"{name}.jsonl"
    return open(p) if p.exists() else gzip.open(p.with_name(p.name + ".gz"), "rt")


def _fsync_dir(d: Path) -> None:
    fd = os.open(d, os.O_RDONLY)
    try:
        os.fsync(fd)
    finally:
        os.close(fd)


def _count(path: Path, gz: bool = False) -> tuple[int, int]:
    """(lines, bytes) of the (decompressed) content."""
    lines = size = 0
    with (gzip.open(path, "rb") if gz else open(path, "rb")) as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            lines += chunk.count(b"\n")
            size += len(chunk)
    return lines, size


def eligible_days(root: Path, today: date) -> list[Path]:
    cut = today - timedelta(days=MIN_AGE_DAYS)
    out = []
    for d in sorted(root.iterdir()) if root.is_dir() else []:
        if d.is_dir() and DAY_RE.match(d.name) and d.name not in KEEP_DAYS \
                and date.fromisoformat(d.name) <= cut:
            out.append(d)
    return out


def compress(path: Path, now: float | None = None) -> bool:
    """Replace one .jsonl by a verified .jsonl.gz. True when the original was removed."""
    now = time.time() if now is None else now
    gz = path.with_name(path.name + ".gz")
    tmp = path.with_name(path.name + ".gz.tmp")
    st0 = path.stat()
    if now - st0.st_mtime < QUIET_S:
        log.info("rotate: %s modified recently, skipped", path)
        return False
    want = _count(path)
    if gz.exists():                       # a previous run renamed but did not delete
        try:
            ok = _count(gz, gz=True) == want
        except (OSError, EOFError, gzip.BadGzipFile):
            ok = False
        if ok:
            path.unlink()
            _fsync_dir(path.parent)
            return True
    try:
        with open(path, "rb") as src, open(tmp, "wb") as raw:
            with gzip.GzipFile(filename=path.name, mode="wb", fileobj=raw, mtime=0) as z:
                for chunk in iter(lambda: src.read(1 << 20), b""):
                    z.write(chunk)
            raw.flush()
            os.fsync(raw.fileno())
        st1 = path.stat()
        if (st1.st_size, st1.st_mtime_ns) != (st0.st_size, st0.st_mtime_ns):
            raise RuntimeError("original changed while compressing")
        got = _count(tmp, gz=True)
        if got != want:
            raise RuntimeError(f"verification failed: {got} != {want} (lines, bytes)")
        os.replace(tmp, gz)
        _fsync_dir(path.parent)
        path.unlink()
        _fsync_dir(path.parent)
        return True
    except Exception as exc:
        log.error("rotate: %s kept: %s", path, exc)
        tmp.unlink(missing_ok=True)
        return False


def rotate(root: Path | None = None, today: date | None = None, dry_run: bool = False) -> list[Path]:
    """Compress every eligible file; returns the originals that were replaced."""
    root = root or DATA_DIR / "live"
    today = today or datetime.now(timezone.utc).date()
    done = []
    for d in eligible_days(root, today):
        for t in d.glob("*.jsonl.gz.tmp"):
            if not t.with_name(t.name[:-len(".gz.tmp")]).exists():
                t.unlink()                # orphan of a crash after its original was already replaced
        for f in sorted(d.glob("*.jsonl")):
            if dry_run:
                log.info("rotate: would compress %s", f)
                done.append(f)
            elif compress(f):
                log.info("rotate: %s -> %s.gz", f, f.name)
                done.append(f)
    return done


def main(argv: list[str] | None = None) -> None:
    ap = argparse.ArgumentParser(prog="python -m pmsports.paper.rotate", description=__doc__.splitlines()[0])
    ap.add_argument("--root", type=Path, default=DATA_DIR / "live")
    ap.add_argument("--dry-run", action="store_true")
    a = ap.parse_args(argv)
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    done = rotate(a.root, dry_run=a.dry_run)
    log.info("rotate: %d file(s) %s", len(done), "eligible" if a.dry_run else "compressed")


if __name__ == "__main__":
    main()
