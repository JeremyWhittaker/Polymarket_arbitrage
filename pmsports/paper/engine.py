"""Receipt-ordered replay, received state and decision execution for the paper engine.

Every decision is a function of capture records with recv_ms <= its decision time. Pending
entries execute before any record with a later recv_ms is applied. Books come from
ReceivedBook; freshness is checked here (snapshot since the last connection open, a message or
pong within 5 s, no `closed` since), so a quiet but live book is valid. Days recorded without
connection markers are legacy: connection_ok="unknown", token-message age <= 5 s instead, and
their decisions are shakedown-only.

Follow mode never releases a watermark past the last directory scan, so a day or stream file that
appears between scans cannot be overtaken; replaying the same files gives the same ledgers.
"""
from __future__ import annotations

import calendar
import gzip
import hashlib
import heapq
import inspect
import json
import logging
import math
import os
import re
import time
from contextlib import contextmanager
from dataclasses import dataclass, field
from pathlib import Path

from ..book_replay import ReceivedBook
from ..collect import DATA_DIR
from .. import polymarket
from . import spec

log = logging.getLogger("pmsports")
ROOT = Path(__file__).resolve().parents[2]
LIVE, PAPER = DATA_DIR / "live", DATA_DIR / "paper"
ACTIVATION = ROOT / "reports" / "paper" / "ACTIVATION.json"
SNAPSHOT_LOG = ROOT / "reports" / "paper" / "model_snapshots.jsonl"
# Equal recv_ms: metadata first, then books/trades, then game-state signals.
STREAMS = {"mlb_map": 0, "market_meta": 1, "soccer_games": 2, "games": 3, "clob": 4, "sports": 5, "mlb": 6, "espn": 7}
LAG_MS, WARMUP_MS, FRESH_MS, REF_MS = 2000, 36 * 3600 * 1000, 5000, 120_000
PRUNE_MS = 48 * 3600 * 1000  # state idle longer than the restart warmup is dropped (a restart lacks it too)
AUDIT_MS = 24 * 3600 * 1000  # connection audit times older than this are omitted (a restart may not have them)
STALE_OFF = 1e15            # ReceivedBook's own token-age check is disabled; freshness is ours
US_FEE = spec.COMMON["us_fee_coefficient"]
EPS = 1e-9
RULE_KEYS = ("fee_rate", "fee_exponent", "tick", "min_size", "seconds_delay", "accepting_orders", "closed")
LEGACY_RULES = dict(tick=0.01, min_size=5.0, seconds_delay=0)
_DAY = re.compile(r"^\d{4}-\d{2}-\d{2}$")
_PREFIX = re.compile(rb'^\{"recv_ms":\s*(\d+)')


# ----------------------------------------------------------------------------- hashes

# Only code that can change a decision is pinned. Capture, settlement and report code
# (capture_soccer, rotate, settle, report, run) is hashed separately in the manifest so
# universe/coverage fixes stay auditable without relabelling decisions.
DECISION_FILES = ("paper/__init__.py", "paper/engine.py", "paper/mlb.py", "paper/soccer.py",
                  "paper/spec.py", "paper/mlb_model.py", "book_replay.py",
                  "research/h_soccer_continuation.py")
SUPPORT_FILES = ("paper/capture_soccer.py", "paper/rotate.py", "paper/settle.py", "paper/report.py",
                 "paper/run.py", "record.py")


def code_files() -> list[Path]:
    pkg = ROOT / "pmsports"
    return [pkg / f for f in DECISION_FILES]


def support_hashes() -> dict:
    pkg = ROOT / "pmsports"
    return {f"pmsports/{f}": hashlib.sha256((pkg / f).read_bytes()).hexdigest()
            for f in SUPPORT_FILES if (pkg / f).exists()}


def code_hashes() -> dict:
    files = {str(p.relative_to(ROOT)): hashlib.sha256(p.read_bytes()).hexdigest() for p in code_files()}
    fee = hashlib.sha256(inspect.getsource(polymarket.taker_fee).encode()).hexdigest()
    combined = hashlib.sha256(json.dumps({"files": files, "taker_fee": fee}, sort_keys=True).encode()).hexdigest()
    return {"files": files, "taker_fee_sha256": fee, "code_sha256": combined}


def load_json(path: Path, default=None):
    try:
        return json.loads(Path(path).read_text())
    except (FileNotFoundError, ValueError):
        return default


def _clean(x):
    if isinstance(x, dict):
        return {str(k): _clean(v) for k, v in x.items()}
    if isinstance(x, (list, tuple, set)):
        return [_clean(v) for v in x]
    if isinstance(x, float) and not math.isfinite(x):
        return None
    return x


def atomic_write(path: Path, data: str | bytes) -> None:
    """Replace path via a per-process temp file (concurrent writers never share a temp name)."""
    path = Path(path)
    tmp = path.with_name(f"{path.name}.{os.getpid()}.tmp")
    tmp.write_bytes(data) if isinstance(data, bytes) else tmp.write_text(data)
    tmp.replace(path)


@contextmanager
def out_lock(out: Path):
    """Exclusive writer lock on an output directory: one engine or settle process per data/paper."""
    import fcntl
    out = Path(out)
    out.mkdir(parents=True, exist_ok=True)
    fh = open(out / ".lock", "a")
    try:
        fcntl.flock(fh, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except BlockingIOError:
        fh.close()
        raise SystemExit(f"{out} is in use by another paper run/settle process ({out / '.lock'} is locked)")
    try:
        yield
    finally:
        fh.close()


def _num(v):
    try:
        x = float(v)
    except (TypeError, ValueError):
        return None
    return x if math.isfinite(x) else None


def is_pregame(recv_ms: int, start_s) -> bool:
    """A discovery record is pregame metadata only if received at or before its own scheduled start."""
    start = _num(start_s)
    return start is not None and recv_ms <= start * 1000


def mlb_map_start(r: dict):
    """Scheduled first pitch (epoch s) of an mlb_map record: MLB's, else Polymarket's."""
    pm = r.get("pm") or {}
    return _num(r.get("mlb_start_ts")) or _num(pm.get("start_ts"))


def mlb_map_key(r: dict) -> str:
    """One Polymarket game market per key (the engine's and the report's unit of discovery)."""
    pm = r.get("pm") or {}
    return str(pm.get("condition_id") or f"pk:{r.get('game_pk')}")


def pregame_latest(rows, key, start) -> tuple[dict, dict]:
    """(latest pregame record per key, latest record per key that has no pregame record)."""
    pre, late = {}, {}
    for r in rows:
        k = key(r)
        if k is None:
            continue
        if is_pregame(int(r.get("recv_ms") or 0), start(r)):
            pre[k] = r
        else:
            late[k] = r
    return pre, {k: r for k, r in late.items() if k not in pre}


class Watched:
    """A small file re-read whenever its size or mtime changes (manifests edited while running)."""

    def __init__(self, path: Path | None, read, default=None):
        self.path, self.read, self.default, self.sig, self.value = path, read, default, None, default

    def get(self):
        if self.path is None:
            return self.default
        try:
            st = os.stat(self.path)
            sig = (st.st_mtime_ns, st.st_size)
        except FileNotFoundError:
            sig = None
        if sig != self.sig:
            self.sig, self.value = sig, (self.read(self.path) if sig else self.default)
        return self.value


# ----------------------------------------------------------------------------- replay

def _recv(line: bytes):
    m = _PREFIX.match(line)
    return int(m.group(1)) if m else None


def _day_ms(day: str) -> int:
    return calendar.timegm(time.strptime(day, "%Y-%m-%d")) * 1000


def _files(root: Path, start_ms, end_ms, streams) -> list[tuple[str, str, Path]]:
    """(day, stream, path); .jsonl preferred over a rotated .jsonl.gz of the same day/stream."""
    out = []
    if not Path(root).exists():
        return out
    for d in sorted(p for p in Path(root).iterdir() if p.is_dir() and _DAY.match(p.name)):
        lo = _day_ms(d.name)
        if (start_ms is not None and lo + 2 * 86400_000 <= start_ms) or (end_ms is not None and lo - 86400_000 >= end_ms):
            continue
        for s in streams:
            plain, gz = d / f"{s}.jsonl", d / f"{s}.jsonl.gz"
            p = plain if plain.exists() else gz if gz.exists() else None
            if p is not None:
                out.append((d.name, s, p))
    return out


class _Chain:
    """One stream's day files read in order; recv_ms clamped non-decreasing within the stream."""

    def __init__(self, stream: str, start_ms, end_ms, follow: bool):
        self.stream, self.rank = stream, STREAMS.get(stream, 99)
        self.start_ms, self.end_ms, self.follow = start_ms, end_ms, follow
        self.todo, self.known, self.fh, self.gz, self.buf = [], set(), None, False, b""
        self.last, self.done, self.bad, self.clamped = -math.inf, False, 0, 0

    def add(self, key: str, path: Path) -> None:
        if key not in self.known:
            self.known.add(key)
            self.todo.append((key, path))
            self.todo.sort()

    def _open(self, path: Path) -> None:
        self.gz = path.suffix == ".gz"
        self.fh = gzip.open(path, "rb") if self.gz else open(path, "rb")
        if self.start_ms is not None and not self.gz:
            self._seek()

    def _seek(self) -> None:
        """Binary-search the byte offset of start_ms in a sorted plain file."""
        fh, lo = self.fh, 0
        hi = os.fstat(fh.fileno()).st_size
        while hi - lo > 1 << 16:
            mid = (lo + hi) // 2
            fh.seek(mid)
            fh.readline()
            ms = _recv(fh.readline())
            if ms is None or ms >= self.start_ms:
                hi = mid
            else:
                lo = mid
        fh.seek(lo)
        if lo:
            fh.readline()

    def next(self):
        while not self.done:
            if self.fh is None:
                if not self.todo:
                    return None
                self._open(self.todo.pop(0)[1])
            line = self.fh.readline()
            if self.buf:
                line, self.buf = self.buf + line, b""
            if not line or not line.endswith(b"\n"):
                if self.follow and not self.todo and not self.gz:
                    self.buf = line          # partial line of a growing file: wait for the rest
                    return None
                if not line:
                    self.fh.close()
                    self.fh = None
                    continue
            ms = _recv(line)
            if ms is not None and self.start_ms is not None and ms < self.start_ms:
                continue
            try:
                rec = json.loads(line)
                ms = int(rec["recv_ms"]) if ms is None else ms
            except (ValueError, KeyError, TypeError):
                self.bad += 1
                continue
            if self.start_ms is not None and ms < self.start_ms:
                continue
            if self.end_ms is not None and ms >= self.end_ms:
                self.done = True
                return None
            if ms < self.last:
                self.clamped += 1
                ms = self.last
            self.last = ms
            return ms, rec
        return None


class Replayer:
    """Merge capture streams across days in recv_ms order.

    `files` is the live root (day folders) or an explicit list of files. With follow=True the
    root is rescanned for new days/streams, growing files are tailed, and only records older than
    `lag_ms` before the last scan are released; heartbeats `(watermark, None, None)` are yielded
    when caught up. `scan_ms` is the wall time of the last scan: no later record can be in a file
    the replay has not found, so `scan_ms - lag_ms` is the batch-end watermark too.
    """

    def __init__(self, files, start_ms=None, end_ms=None, follow=False, lag_ms=LAG_MS, poll_s=0.5,
                 rescan_s=5.0, streams=tuple(STREAMS), clock=time.time):
        self.root = None if isinstance(files, (list, tuple)) else Path(files)
        self.explicit = None if self.root else [Path(f) for f in files]
        self.start_ms, self.end_ms, self.follow = start_ms, end_ms, follow
        self.lag_ms, self.poll_s, self.rescan_s, self.streams, self.clock = lag_ms, poll_s, rescan_s, streams, clock
        self.chains: dict[str, _Chain] = {}
        self.late, self.scan_ms = 0, None

    def _discover(self) -> None:
        if self.explicit is not None:
            found = []
            for p in self.explicit:
                s = p.name.split(".")[0]
                if s in self.streams:
                    found.append((p.parent.name, s, p))
        else:
            found = _files(self.root, self.start_ms, self.end_ms, self.streams)
        for day, s, p in found:
            c = self.chains.get(s)
            if c is None:
                c = self.chains[s] = _Chain(s, self.start_ms, self.end_ms, self.follow)
            c.add(day, p)

    def __iter__(self):
        rescan = self.clock()
        self._discover()
        self.scan_ms = int(rescan * 1000)
        heap, idle, last = [], list(self.chains.values()), -math.inf

        def refill(c):
            r = c.next()
            if r is None:
                if self.follow and not c.done:
                    idle.append(c)
            else:
                heapq.heappush(heap, (r[0], c.rank, c.stream, r[1], c))

        while True:
            now = self.clock()
            if self.follow and now - rescan >= self.rescan_s:
                known = set(self.chains)
                rescan = now                   # taken before the scan: later files hold later records
                self._discover()
                self.scan_ms = int(rescan * 1000)
                idle.extend(c for s, c in self.chains.items() if s not in known)
            waiting, idle[:] = list(idle), []
            for c in waiting:
                refill(c)
            # never past the last scan: a new day/stream file may hold records received since then
            mark = math.inf if not self.follow else int(min(now, rescan) * 1000) - self.lag_ms
            if self.end_ms is not None:
                mark = min(mark, self.end_ms - 1)
            while heap and heap[0][0] <= mark:
                ms, _, s, rec, c = heapq.heappop(heap)
                if ms < last:
                    self.late += 1
                    ms = last
                last = ms
                yield ms, s, rec
                refill(c)
            if not self.follow:
                return
            last = max(last, mark)             # anything that still shows up behind it is counted late
            yield mark, None, None
            if self.end_ms is not None and mark >= self.end_ms - 1 and not heap:
                return
            time.sleep(self.poll_s)

    def stats(self) -> dict:
        return {s: dict(bad=c.bad, clamped=c.clamped) for s, c in self.chains.items()} | {"late": self.late}


# ----------------------------------------------------------------------------- state

@dataclass
class Trade:
    ms: int
    price: float
    size: float


class State:
    """Received book, connection freshness, last trades and market rules (all as of now)."""

    def __init__(self):
        self.book = ReceivedBook()
        self.markers = False                     # any conn marker seen -> marker freshness mode
        self.open_ms = self.closed_ms = self.activity_ms = None
        self.closed_after = False
        self.snap: set[str] = set()              # book snapshot since last open (or replay start)
        self.token_ms: dict[str, int] = {}       # last message of any kind per token (legacy)
        self.book_hash: dict[str, str] = {}      # venue hash of the last book/price_change per token
        self.trades: dict[str, list] = {}        # token -> [latest, latest with smaller recv_ms]
        self.meta: dict[str, dict] = {}
        self.errors = 0

    def apply_clob(self, ms: int, rec: dict) -> list[str]:
        """Apply one clob record; returns the tokens that received a full book snapshot."""
        books = []
        conn = rec.get("conn")
        if conn is not None:
            self.markers = True
            if conn == "open":
                self.open_ms, self.activity_ms, self.closed_after = ms, ms, False
                self.snap.clear()
            elif conn == "closed":
                self.closed_ms, self.closed_after = ms, True
            elif conn == "pong":
                self.activity_ms, self.closed_after = ms, False
            return books
        msg = rec.get("msg")
        if msg is None:
            return books
        self.activity_ms, self.closed_after = ms, False
        for m in msg if isinstance(msg, list) else (msg,):
            if not isinstance(m, dict):
                continue
            kind = m.get("event_type")
            if kind == "new_market":
                continue
            if kind in ("book", "price_change"):
                try:
                    self.book.apply(ms, m)
                except (ValueError, KeyError, TypeError):
                    self.errors += 1
                    continue
                if kind == "book":
                    tok = str(m.get("asset_id"))
                    self.snap.add(tok)
                    self.token_ms[tok] = ms
                    self.book_hash[tok] = m.get("hash")
                    books.append(tok)
                else:
                    for x in m.get("price_changes") or ():
                        if x.get("asset_id") is not None:
                            self.token_ms[str(x["asset_id"])] = ms
                            self.book_hash[str(x["asset_id"])] = x.get("hash")
                continue
            tok = m.get("asset_id")
            if tok is None:
                continue
            tok = str(tok)
            self.token_ms[tok] = ms
            if kind == "last_trade_price":
                try:
                    p, q = float(m["price"]), float(m.get("size") or 0)
                except (KeyError, TypeError, ValueError):
                    continue
                if 0 < p < 1 and q > 0 and math.isfinite(q):
                    h = self.trades.setdefault(tok, [None, None])
                    if h[0] is not None and ms > h[0].ms:
                        h[1] = h[0]
                    h[0] = Trade(ms, p, q)
            elif kind == "tick_size_change" and m.get("market") and m.get("new_tick_size") is not None:
                tick = _num(m["new_tick_size"])
                if tick is None:
                    self.errors += 1             # malformed venue field: ignored, rules stay as they were
                    continue
                self.update_meta(m["market"], {"tick": tick}, ms, "tick_size_change")
        return books

    def prune(self, now_ms: int, age_ms: int = PRUNE_MS) -> int:
        """Forget tokens with no message for age_ms (bounded memory for a year-long follow)."""
        old = [t for t, ms in self.token_ms.items() if now_ms - ms > age_ms]
        for t in old:
            for d in (self.token_ms, self.trades, self.book_hash, self.book.books, self.book.last_ms):
                d.pop(t, None)
            self.book.initialized.discard(t)
            self.snap.discard(t)
        for cid in [c for c, m in self.meta.items() if now_ms - m.get("recv_ms", now_ms) > age_ms]:
            del self.meta[cid]
        return len(old)

    def update_meta(self, cid, fields: dict, ms: int, source: str) -> None:
        if not cid:
            return
        m = self.meta.setdefault(str(cid), {})
        vals = {k: fields.get(k) for k in RULE_KEYS if fields.get(k) is not None}
        if vals:
            m.update(vals, recv_ms=ms, source=source)

    def market_rules(self, cid, legacy: bool = False):
        m = self.meta.get(str(cid)) or {}
        r = {k: m.get(k) for k in RULE_KEYS}
        for k in ("fee_rate", "fee_exponent", "tick", "min_size", "seconds_delay"):
            try:
                r[k] = None if r[k] is None else float(r[k])
            except (TypeError, ValueError):
                r[k] = None                  # unparseable venue rule = missing rule
            if r[k] is not None and (not math.isfinite(r[k]) or r[k] < 0 or (k == "tick" and not 0 < r[k] < 1)):
                r[k] = None
        source = m.get("source")
        if legacy and any(r[k] is None for k in LEGACY_RULES):
            r.update({k: v for k, v in LEGACY_RULES.items() if r[k] is None})
            source = "legacy_default"
        missing = [k for k in ("fee_rate", "tick", "min_size", "seconds_delay") if r[k] is None]
        return r, missing, source, m.get("recv_ms")

    def reference(self, token: str, t: int, max_age_ms: int = REF_MS):
        """Last valid trade received strictly before t and at most max_age_ms old."""
        h = self.trades.get(str(token))
        if not h:
            return None
        for tr in h:
            if tr is not None and tr.ms < t:
                return tr if t - tr.ms <= max_age_ms else None
        return None

    def freshness(self, token: str, t: int) -> dict:
        token = str(token)
        book_ms = self.book.last_ms.get(token)
        out = dict(book_ms=book_ms, book_age_ms=None if book_ms is None else t - book_ms, book_hash=self.book_hash.get(token),
                   token_msg_ms=self.token_ms.get(token), conn_open_ms=self.open_ms,
                   conn_activity_ms=self.activity_ms, conn_closed_ms=self.closed_ms)
        if not self.markers:
            age = None if token not in self.token_ms else t - self.token_ms[token]
            reason = ("no_snapshot" if token not in self.book.initialized else
                      "stale_token_legacy" if age is None or age > FRESH_MS else "")
            return out | dict(ok=not reason, reason=reason, connection_ok="unknown")
        reason = ("no_snapshot_since_open" if token not in self.snap else
                  "connection_closed" if self.closed_after else
                  "connection_not_current" if self.activity_ms is None or t - self.activity_ms > FRESH_MS else "")
        return out | dict(ok=not reason, reason=reason, connection_ok=not reason)


# ----------------------------------------------------------------------------- output

class JsonlLog:
    """Append-only JSONL keyed by `key`; existing keys are never written again."""

    def __init__(self, path: Path, key: str = "key", fsync: bool = False):
        self.path, self.key, self.fsync, self.fh, self.bad = Path(path), key, fsync, None, 0
        self.keys: set[str] = set()
        if self.path.exists():
            with open(self.path, "rb") as f:
                for line in f:
                    try:
                        self.keys.add(json.loads(line)[key])
                    except (ValueError, KeyError, TypeError):
                        self.bad += 1

    def write(self, rec: dict) -> bool:
        if rec[self.key] in self.keys:
            return False
        if self.fh is None:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            torn = False
            if self.path.exists() and self.path.stat().st_size:
                with open(self.path, "rb") as f:
                    f.seek(-1, 2)
                    torn = f.read(1) != b"\n"
            self.fh = open(self.path, "ab")
            if torn:
                self.fh.write(b"\n")     # never glue a record onto a torn last line
        self.fh.write(json.dumps(_clean(rec), separators=(",", ":"), allow_nan=False).encode() + b"\n")
        self.fh.flush()
        if self.fsync:
            os.fsync(self.fh.fileno())
        self.keys.add(rec[self.key])
        return True

    def close(self) -> None:
        if self.fh:
            self.fh.close()
            self.fh = None


def read_jsonl(path: Path) -> list[dict]:
    out = []
    if Path(path).exists():
        with open(path, "rb") as f:
            for line in f:
                try:
                    out.append(json.loads(line))
                except ValueError:
                    pass
    return out


def capture_rows(live: Path, stream: str) -> list[dict]:
    """Every record of a small capture stream across all days (.jsonl preferred over .gz)."""
    out = []
    for _, _, path in _files(Path(live), None, None, (stream,)):
        with (gzip.open(path, "rb") if path.suffix == ".gz" else open(path, "rb")) as f:
            for line in f:
                try:
                    out.append(json.loads(line))
                except ValueError:
                    pass
    return out


def floor_tick(x: float, tick: float) -> float:
    return round(math.floor(x / tick + EPS) * tick, 10)


# ----------------------------------------------------------------------------- engine

@dataclass(order=True)
class Pending:
    eligible_ms: int
    seq: int
    rec: dict = field(compare=False)
    game: dict | None = field(compare=False, default=None)
    n_at_signal: int = field(compare=False, default=0)


def _snapshot_log(path: Path) -> dict:
    """sha256 -> (max_season, first logged_ms) from model_snapshots.jsonl."""
    out = {}
    for r in read_jsonl(path):
        out.setdefault(r.get("sha256"), (r.get("max_season"), r.get("logged_ms")))
    return out


class Engine:
    """Applies records in order, runs strategies, executes pending entries, writes decisions.

    A record whose content raises while being applied is skipped (counted in `errors` per stream):
    replay and follow skip it identically, so one malformed line cannot stop the service.
    `coverage.jsonl` notes the first receipt of each token's book snapshot and each game's state
    feed, for the report's missing-capture accounting.
    """

    def __init__(self, out_dir: Path = PAPER, live_root: Path = LIVE, activation_path: Path | None = ACTIVATION,
                 snapshot_log: Path | None = SNAPSHOT_LOG, model_dir: Path | None = None, allow_network: bool = False):
        from .mlb import MLBStrategy
        from .soccer import SoccerStrategy
        from .mlb_model import SNAP_DIR
        self.out, self.live_root = Path(out_dir), Path(live_root)
        self.state = State()
        self.decisions = JsonlLog(self.out / "decisions.jsonl", fsync=True)
        self.signals = JsonlLog(self.out / "signals.jsonl")
        self.coverage = JsonlLog(self.out / "coverage.jsonl")
        self.pending: list[Pending] = []
        self.seq, self.last_ms, self.counts, self.pruned_ms, self.errors = 0, None, {}, None, {}
        h = code_hashes()
        self.code_sha, self.spec_sha = h["code_sha256"], spec.spec_hash()
        # re-read when changed: `paper activate` / `paper snapshot` while the service runs
        self._activation = Watched(Path(activation_path) if activation_path else None, load_json)
        self._models_log = Watched(Path(snapshot_log) if snapshot_log else None, _snapshot_log, {})
        self.strategies = [MLBStrategy(self, model_dir or SNAP_DIR, allow_network=allow_network), SoccerStrategy(self)]
        self.routes: dict[str, list] = {}
        for s in self.strategies:
            for name in s.streams:
                self.routes.setdefault(name, []).append(s)

    @property
    def activation(self) -> dict | None:
        return self._activation.get()

    @property
    def logged_models(self) -> dict:
        return self._models_log.get() or {}

    # --- record flow
    def process(self, ms: int, stream: str, rec: dict) -> None:
        self.advance(ms - 1)
        self.last_ms = ms
        self.counts[stream] = self.counts.get(stream, 0) + 1
        if self.pruned_ms is None or ms - self.pruned_ms >= 3600_000:
            self.pruned_ms = ms
            self.state.prune(ms)
            for s in self.strategies:
                s.prune(ms)
        try:
            if stream == "clob":
                for tok in self.state.apply_clob(ms, rec):
                    self.cover(f"book|{tok}", ms)
            elif stream == "market_meta":
                self.state.update_meta(rec.get("condition_id"), rec, ms, "market_meta")
        except OSError:
            raise
        except Exception as exc:
            return self._error(stream, ms, exc)
        for s in self.routes.get(stream, ()):
            try:
                s.on_record(stream, rec, ms)
            except OSError:                      # output failures stop the engine (restart replays)
                raise
            except Exception as exc:
                self._error(stream, ms, exc)

    def _error(self, where: str, ms: int, exc: Exception) -> None:
        n = self.errors[where] = self.errors.get(where, 0) + 1
        if n <= 20 or n % 1000 == 0:
            log.warning("paper: skipped %s record at %s (%d so far): %r", where, ms, n, exc)

    def cover(self, key: str, ms: int) -> None:
        if key not in self.coverage.keys:
            self.coverage.write(dict(key=key, recv_ms=ms))

    def advance(self, watermark: int) -> None:
        """Execute entries eligible at or before watermark (all records <= it are applied)."""
        while self.pending and self.pending[0].eligible_ms <= watermark:
            p = heapq.heappop(self.pending)
            try:
                self._execute(p)
            except OSError:
                raise
            except Exception as exc:
                self._error("execute", p.eligible_ms, exc)
                self.reject(p.rec, "execution_error", p.eligible_ms)

    # --- decisions
    def log_signal(self, rec: dict) -> None:
        rec["key"] = f"{rec['sport']}|{rec['game_id']}|{rec['signal_id']}"
        self.signals.write(rec)

    def reject(self, rec: dict, reason: str, decided_ms: int) -> None:
        rec.update(status="rejected", reason=reason)
        self._write(rec, decided_ms)

    def enter(self, rec: dict, legacy_rules: bool = False, game: dict | None = None) -> None:
        """Common signal-time checks; schedule the entry at receipt + applicable delay."""
        r = spec.RULES[rec["policy"]]
        t = rec["signal_recv_ms"]
        rules, missing, source, rules_ms = self.state.market_rules(rec["condition_id"], legacy_rules)
        rec.update({k: rules[k] for k in RULE_KEYS}, rules_source=source, rules_recv_ms=rules_ms)
        if missing:
            return self.reject(rec, "missing_market_rules:" + ",".join(missing), t)
        if rules["fee_exponent"] is not None and rules["fee_exponent"] != 1.0:
            return self.reject(rec, "unsupported_fee_exponent", t)
        if rules["closed"] is True:
            return self.reject(rec, "market_closed", t)
        if rules["accepting_orders"] is False:
            return self.reject(rec, "not_accepting_orders", t)
        tick = float(rules["tick"])
        rec["limit"] = limit = floor_tick(rec["reference_price"] + r["limit_offset"], tick)
        if not 0 < limit < 1:
            return self.reject(rec, "invalid_limit", t)
        delay_ms = int(round((max(float(r["min_delay_s"]), float(rules["seconds_delay"])) + r["extra_delay_s"]) * 1000))
        rec.update(delay_ms=delay_ms, eligible_ms=t + delay_ms)
        self.seq += 1
        heapq.heappush(self.pending, Pending(t + delay_ms, self.seq, rec, game, game["n"] if game else 0))

    def _execute(self, p: Pending) -> None:
        rec, t = p.rec, p.eligible_ms
        token, policy = rec["token"], rec["policy"]
        if p.game is not None:
            rec["state_changes_during_delay"] = p.game["n"] - p.n_at_signal
        f = self.state.freshness(token, t)
        rec.update(connection_ok=f["connection_ok"], book_ms=f["book_ms"], book_age_ms=f["book_age_ms"], book_hash=f["book_hash"],
                   conn={k: f[k] if f[k] is not None and t - f[k] <= AUDIT_MS else None
                         for k in ("conn_open_ms", "conn_activity_ms", "conn_closed_ms", "token_msg_ms")})
        if not f["ok"]:
            return self.reject(rec, f["reason"], t)
        asks = self.state.book.books.get(str(token), {}).get("SELL", {})
        bids = self.state.book.books.get(str(token), {}).get("BUY", {})
        rec["ask_at_exec"] = min(asks, default=None)
        rec["bid_at_exec"] = max(bids, default=None)
        before = {px: lv.used.get(policy, 0.) for px, lv in asks.items()}
        fill = self.state.book.cross(token, t, policy=policy, buy=True, budget=spec.RULES[policy]["budget_usd"],
                                     limit=rec["limit"], fee_rate=float(rec["fee_rate"]),
                                     min_order_shares=float(rec["min_size"]), stale_ms=STALE_OFF)
        levels = sorted((px, lv.used.get(policy, 0.) - before.get(px, 0.)) for px, lv in asks.items())
        fill["levels"] = [[px, q] for px, q in levels if q > 1e-12]
        rec.update(fill=fill, status=fill["status"], reason=fill["reason"], shares=fill["shares"],
                   price=fill["price"], gross_usd=fill["gross_usd"], fee_usd=fill["fee_usd"], cost_usd=fill["cash_usd"],
                   fee_usd_us=sum(US_FEE * q * px * (1 - px) for px, q in fill["levels"]))
        self._write(rec, t)

    def _model_reason(self, rec: dict) -> str | None:
        """A season pinned in the manifest must use exactly that snapshot; any other season's
        snapshot must be logged (same max_season) before the game's scheduled start."""
        sha, season = rec.get("model_sha256"), rec.get("model_max_season")
        pinned = ((self.activation or {}).get("model_snapshots") or {}).get(str(season))
        if pinned is not None:
            return None if sha == pinned else "model_snapshot_mismatch"
        logged, start = self.logged_models.get(sha), _num(rec.get("scheduled_start_ts"))
        ok = (logged is not None and str(logged[0]) == str(season) and logged[1] is not None and start is not None
              and logged[1] <= start * 1000)
        return None if ok else "model_snapshot_unlogged"

    def shakedown_reasons(self, rec: dict) -> list[str]:
        a, why = self.activation, []
        if not a:
            why.append("not_activated")
        else:
            if a.get("code_sha256") != self.code_sha:
                why.append("code_hash_mismatch")
            if a.get("spec_sha256") != self.spec_sha:
                why.append("spec_hash_mismatch")
            start = rec.get("scheduled_start_ts")
            if start is None or start * 1000 < a.get("activated_ms", math.inf):
                why.append("scheduled_before_activation")
        if rec.get("mapping_source") == "legacy":
            why.append("legacy_mapping")
        if rec.get("connection_ok") == "unknown":
            why.append("connection_markers_missing")
        if rec.get("rules_source") == "legacy_default":
            why.append("legacy_market_rules")
        if a and rec.get("sport") == "mlb" and rec.get("model_sha256") and (m := self._model_reason(rec)):
            why.append(m)
        return why

    def _write(self, rec: dict, decided_ms: int) -> None:
        rec.setdefault("shares", 0.)
        rec.setdefault("cost_usd", 0.)
        why = self.shakedown_reasons(rec)
        rec.update(key=f"{rec['policy']}|{rec['game_id']}", decided_ms=decided_ms, spec_version=spec.VERSION,
                   code_sha256=self.code_sha, spec_sha256=self.spec_sha, shakedown=bool(why), shakedown_reasons=why)
        self.decisions.write(rec)

    def finish(self, end_ms: int | None = None, now_ms: int | None = None) -> None:
        """Batch end: execute entries whose eligibility is past the capture watermark (the
        replay's last directory scan - lag, or the replay end), exactly as follow mode would."""
        mark = (int(time.time() * 1000) if now_ms is None else now_ms) - LAG_MS
        self.advance(mark if end_ms is None else min(mark, end_ms - 1))

    def close(self) -> None:
        self.decisions.close()
        self.signals.close()
        self.coverage.close()
