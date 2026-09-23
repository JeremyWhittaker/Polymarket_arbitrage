"""Are some sports bettors genuinely skilled, and can you copy them?

Every taker fill is normalized to "wallet bought side s at price q, side paid y" (see
tapes.load_trades). Then:

positions   per (wallet, market): cost, pnl = sum size*(y-q) (hold to resolution), and the
            luck variance under "prices are fair": (A-B)^2 p(1-p), A/B = shares on each side
stats       per wallet over a period: markets, $ staked, pnl, ROI, win rate, z = pnl/sqrt(var)
copy        replay a wallet's later trades at the first *other* taker print on the same side
            strictly later than signal+d, size bounded, minus the taker fee, held to
            resolution. These prints are proxies, not executable asks; d=0 remains strictly later.

Selection always uses data strictly before the evaluation window (no look-ahead), and every
selection rule is compared with random wallets of similar activity (placebo).
"""
from __future__ import annotations

import logging
import os
import resource
from pathlib import Path
import numpy as np
import pandas as pd

from ..polymarket import taker_fee
from ..execution import TapeReplay


def log_progress(label: str, **counts):
    """Phase evidence survives a failed long run; RSS is diagnostic, not a limit."""
    peak = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss / 1024
    try:
        rss = int(Path('/proc/self/statm').read_text().split()[1]) * os.sysconf('SC_PAGE_SIZE') / 2**20
    except (OSError, ValueError, IndexError):
        rss = float('nan')
    logging.getLogger('pmsports').info('[wallet] %s %s rss_MiB=%.1f peak_MiB=%.1f',
        label, ' '.join(f'{k}={v}' for k,v in counts.items()), rss, peak)


# ----------------------------------------------------------------------------- positions / stats

def valid_trade_mask(t: pd.DataFrame) -> pd.Series:
    """Reject corrupted source economics before ranking or allocating copied orders."""
    if t.empty:
        return pd.Series(False, index=t.index, dtype=bool)
    return (t.q.between(0,1,inclusive="neither") & t["size"].gt(0) & np.isfinite(t["size"])
            & np.isfinite(t.timestamp) & t.side_idx.isin([0,1]) & t.y.isin([0,.5,1])
            & np.isfinite(t.fee_rate) & t.fee_rate.ge(0))


def valid_trades(t: pd.DataFrame) -> pd.DataFrame:
    valid = valid_trade_mask(t)
    return t if valid.all() else t.loc[valid]


POSITION_BATCH_ROWS = 250_000


def _market_row_batches(t: pd.DataFrame, batch_rows: int):
    """Complete markets, stable within each market; never split a wallet-market group.

    The loader and its time-filtered subsets are already market ordered. Unsorted
    callers need one stable row index, not copies of every economic column. A
    single market larger than the target is processed intact.
    """
    codes = t.condition_id.cat.codes.to_numpy()
    ordered = all(np.all(codes[max(0, a-1):b-1] <= codes[max(0, a-1)+1:b])
                  for a in range(0, len(codes), batch_rows)
                  for b in [min(a + batch_rows, len(codes))])
    order = None if ordered else np.argsort(codes, kind="stable")
    sorted_codes = codes if order is None else codes[order]
    boundaries = []
    for a in range(0, len(codes), batch_rows):
        start = max(1, a)
        stop = min(a + batch_rows, len(codes))
        changes = np.flatnonzero(sorted_codes[start:stop] != sorted_codes[start-1:stop-1]) + start
        boundaries.extend(changes.tolist())
    boundaries.append(len(codes))
    del sorted_codes
    begin = previous = 0
    for end in boundaries:
        if end - begin > batch_rows and previous > begin:
            yield slice(begin, previous) if order is None else order[begin:previous]
            begin = previous
        previous = end
    if previous > begin:
        yield slice(begin, previous) if order is None else order[begin:previous]


def _market_batches(t: pd.DataFrame, batch_rows: int):
    for rows in _market_row_batches(t, batch_rows):
        yield t.iloc[rows]


def positions(t: pd.DataFrame, batch_rows: int = POSITION_BATCH_ROWS) -> pd.DataFrame:
    """Exact full-data wallet-market aggregation with bounded reduction temporaries.

    Each market occurs in one batch, so the original stable within-group floating
    accumulation is unchanged. Only reduced positions are concatenated and sorted
    into the original wallet/market categorical order. No rows are sampled.
    """
    if not isinstance(batch_rows, (int, np.integer)) or batch_rows < 1:
        raise ValueError("batch_rows must be a positive integer")
    t = valid_trades(t)
    if t.empty:
        return _positions_batch(t)
    parts = [_positions_batch(batch) for batch in _market_batches(t, batch_rows)]
    if len(parts) == 1:
        return parts[0]
    # Sort only reduced categorical codes, then assemble one column at a time.
    # A DataFrame concat/sort/reset would retain several full position-table
    # copies. Pop each independent batch column as it is consumed instead.
    wallet_codes = np.concatenate([p.proxyWallet.cat.codes for p in parts])
    market_codes = np.concatenate([p.condition_id.cat.codes for p in parts])
    order = np.lexsort((market_codes, wallet_codes))
    del wallet_codes, market_codes
    columns = {}
    for name in list(parts[0].columns):
        chunks = [p.pop(name) for p in parts]
        dtype = chunks[0].dtype
        if isinstance(dtype, pd.CategoricalDtype):
            values = np.concatenate([c.cat.codes.to_numpy() for c in chunks])
            del chunks
            columns[name] = pd.Categorical.from_codes(values[order], dtype=dtype)
        else:
            values = np.concatenate([c.to_numpy() for c in chunks])
            del chunks
            columns[name] = values[order]
        del values
    return pd.DataFrame(columns, copy=False)


def _positions_batch(t: pd.DataFrame) -> pd.DataFrame:
    """Original stable sort/reduceat computation, applied to complete markets."""
    if t.empty:
        out = pd.DataFrame({c: pd.Series(dtype=float) for c in ("cost", "pnl", "fee", "a", "b", "shares", "in_play", "n", "ts", "var", "pnl_net")})
        for target, source in (("proxyWallet", "proxyWallet"), ("condition_id", "condition_id"), ("family", "family"), ("event", "event_slug")):
            out[target] = t[source].iloc[:0].reset_index(drop=True)
        return out

    w = t.proxyWallet.cat.codes.to_numpy().astype(np.int64)
    m = t.condition_id.cat.codes.to_numpy().astype(np.int64)
    nm = int(m.max()) + 1 if len(m) else 1
    order = np.argsort(w * nm + m, kind="stable")
    key = (w * nm + m)[order]
    starts = np.flatnonzero(np.r_[True, key[1:] != key[:-1]])
    del w, m

    def red(x):
        return np.add.reduceat(np.asarray(x)[order], starts) if len(starts) else np.array([])

    size = t["size"].to_numpy(np.float64)
    q = t.q.to_numpy(np.float64)
    side0 = t.side_idx.to_numpy() == 0
    out = {"cost": red(size * q), "pnl": red(size * (t.y.to_numpy(np.float64) - q)),
           "fee": red(taker_fee(size, q, t.fee_rate.to_numpy(np.float64))),
           "a": red(np.where(side0, size, 0.0)), "b": red(np.where(side0, 0.0, size)),
           "p0w": red(size * np.where(side0, q, 1 - q)), "shares": red(size),
           "in_play": red(t.in_play.to_numpy(np.float64))}
    n = np.diff(np.r_[starts, len(key)])
    out["in_play"] = out["in_play"] / n
    out["n"] = n
    out["ts"] = np.minimum.reduceat(t.timestamp.to_numpy()[order], starts) if len(starts) else np.array([])
    del size, q, side0, order
    # Keep columns independent so the combiner can release each consumed array.
    pos = pd.DataFrame(out, copy=False)
    k0 = key[starts]
    wcode, mcode = (k0 // nm).astype(np.int32), (k0 % nm).astype(np.int32)
    p0 = (pos.p0w / pos.shares).clip(0.001, 0.999)
    pos["var"] = (pos.a - pos.b) ** 2 * p0 * (1 - p0)
    pos["pnl_net"] = pos.pnl - pos.fee
    pos["proxyWallet"] = pd.Categorical.from_codes(wcode, categories=t.proxyWallet.cat.categories)
    pos["condition_id"] = pd.Categorical.from_codes(mcode, categories=t.condition_id.cat.categories)
    # per-market attributes via first row of each market code
    mc = t.condition_id.cat.codes.to_numpy()
    first = np.full(len(t.condition_id.cat.categories), -1, dtype=np.int64)
    first[mc[::-1]] = np.arange(len(mc))[::-1]
    for c, src in (("family", "family"), ("event", "event_slug")):
        codes = t[src].cat.codes.to_numpy()[first[mcode]]
        pos[c] = pd.Categorical.from_codes(codes, categories=t[src].cat.categories)
    del pos["p0w"]
    return pos


def wallet_stats(pos: pd.DataFrame) -> pd.DataFrame:
    g = pos.groupby("proxyWallet", observed=True)
    s = g.agg(markets=("cost", "size"), staked=("cost", "sum"), pnl=("pnl", "sum"), pnl_net=("pnl_net", "sum"),
              var=("var", "sum"), in_play=("in_play", "mean"))
    s["wins"] = (pos.pnl > 0).groupby(pos.proxyWallet, observed=True).sum()
    fam = pos.groupby(["proxyWallet", "family"], observed=True).cost.sum().reset_index()
    fam = fam.sort_values("cost", ascending=False).drop_duplicates("proxyWallet").set_index("proxyWallet")
    s["top_family"] = fam.family
    s["family_share"] = fam.cost / s.staked
    s["roi"] = s.pnl / s.staked
    s["win_rate"] = s.wins / s.markets
    s["z"] = s.pnl / np.sqrt(s["var"].clip(lower=1e-9))
    return s


def fdr_survivors(z: pd.Series, alpha: float = 0.05) -> pd.Series:
    """Benjamini-Hochberg on one-sided p-values (skill > 0)."""
    from scipy.stats import norm
    p = pd.Series(norm.sf(z.to_numpy()), index=z.index).sort_values()
    m = len(p)
    thresh = alpha * np.arange(1, m + 1) / m
    passed = p.to_numpy() <= thresh
    k = np.max(np.where(passed)[0]) + 1 if passed.any() else 0
    return pd.Series(p.index[:k])


# ----------------------------------------------------------------------------- copy execution

class _WalletTapeReplay(TapeReplay):
    """Compact cached index, bounded constructor and independent order batches.

    The shared engine still validates/sorts each complete market and executes every
    order. Its numeric index arrays are populated directly, avoiding full-corpus
    DataFrame conversions/deduplication/sorts. Replay batches keep all orders linked
    by a market OR an event together, including inconsistent event metadata.
    """

    def __init__(self, t: pd.DataFrame, batch_rows: int = POSITION_BATCH_ROWS):
        if not isinstance(batch_rows, (int, np.integer)) or batch_rows < 1:
            raise ValueError("batch_rows must be a positive integer")
        arrays = {name: np.empty(len(t), dtype=dtype) for name, dtype in (
            ("m", t.condition_id.cat.codes.dtype), ("s", t.side_idx.dtype),
            ("ts", float), ("q", float), ("size", float),
            ("w", t.proxyWallet.cat.codes.dtype), ("fee_rate", float), ("print_id", np.int64))}
        self.groups = {}
        offset = 0
        for rows in _market_row_batches(t, batch_rows):
            batch = t.iloc[rows]
            ids = np.arange(rows.start, rows.stop) if isinstance(rows, slice) else rows
            tape = pd.DataFrame({"m": batch.condition_id.cat.codes.to_numpy(),
                "s": batch.side_idx.to_numpy(), "ts": batch.timestamp.to_numpy(float),
                "q": batch.q.to_numpy(float), "size": batch["size"].to_numpy(float),
                "fee_rate": batch.fee_rate.to_numpy(float),
                "w": batch.proxyWallet.cat.codes.to_numpy(), "print_id": ids}, copy=False)
            replay = TapeReplay(tape)
            end = offset + len(replay.tape)
            for name, values in arrays.items():
                values[offset:end] = replay.tape[name].to_numpy()
            self.groups.update({key: (a + offset, b + offset) for key, (a, b) in replay.groups.items()})
            offset = end
            del replay, tape, batch
        self.tape = pd.DataFrame({name: values[:offset] for name, values in arrays.items()}, copy=False)
        self.ts, self.q = self.tape.ts.to_numpy(), self.tape.q.to_numpy()
        self.size, self.rate = self.tape["size"].to_numpy(), self.tape.fee_rate.to_numpy()
        self.w, self.ids = self.tape.w.to_numpy(), self.tape.print_id.to_numpy()

    @staticmethod
    def order_batches(orders: pd.DataFrame, batch_orders: int = 25_000):
        """Stable original order inside complete market/event connected components."""
        parent, by_event = {}, {}

        def root(m):
            parent.setdefault(m, m)
            while parent[m] != m:
                parent[m] = parent[parent[m]]
                m = parent[m]
            return m

        for start in range(0, len(orders), batch_orders):
            block = orders.iloc[start:start + batch_orders]
            pairs = (block[["m", "event"]] if "event" in block else block[["m"]].assign(event=block.m)).drop_duplicates()
            for m, event in pairs.itertuples(index=False, name=None):
                event = m if pd.isna(event) else event
                a, b = root(m), root(by_event.setdefault(event, m))
                parent[a] = b
        roots = {m: root(m) for m in parent}
        labels = {r: i for i, r in enumerate(dict.fromkeys(roots.values()))}
        codes = np.fromiter((labels[roots[m]] for m in orders.m.to_numpy()), dtype=np.int32, count=len(orders))
        order = np.argsort(codes, kind="stable")
        counts = np.bincount(codes, minlength=len(labels))
        result, start, end = [], 0, 0
        for count in counts:
            if end > start and end + count - start > batch_orders:
                result.append(order[start:end])
                start = end
            end += int(count)
        if end > start:
            result.append(order[start:end])
        return result

    def replay(self, orders: pd.DataFrame, *, batches=None, **kwargs) -> pd.DataFrame:
        batches = self.order_batches(orders) if batches is None else batches
        if not batches:
            return TapeReplay.replay(self, orders, **kwargs)
        columns = None
        for rows in batches:
            result = TapeReplay.replay(self, orders.iloc[rows], **kwargs)
            if columns is None:
                dtypes = result.dtypes
                columns = {name: np.empty(len(orders), dtype=values.to_numpy().dtype)
                           for name, values in result.items()}
            for name, values in result.items():
                columns[name][rows] = values.to_numpy()
        return pd.DataFrame({name: pd.Series(values, dtype=dtypes[name], copy=False)
                             for name, values in columns.items()}, copy=False)


def build_groups(t: pd.DataFrame, batch_rows: int = POSITION_BATCH_ROWS,
                 *, meta: pd.DataFrame | None = None) -> dict:
    """Cached tape and small analytical-closure lookup; raw prints remain unchanged.

    Gamma closure bounds new simulated entries. It is not an independently
    observed resolution announcement or public receipt timestamp.
    """
    if meta is None or "closed_ts" not in meta:
        raise ValueError("market closure metadata is required for wallet copy execution")
    if not meta.index.is_unique:
        raise ValueError("market closure metadata must have unique condition IDs")
    closed = pd.to_numeric(meta.closed_ts.reindex(t.condition_id.cat.categories), errors="coerce").to_numpy(float, copy=True)
    closed[~np.isfinite(closed) | (closed <= 0)] = np.nan
    log_progress('tape index start', fills=len(t), markets=len(t.condition_id.cat.categories))
    replay = _WalletTapeReplay(t, batch_rows=batch_rows)
    log_progress('tape index done', fills=len(replay.tape), market_sides=len(replay.groups))
    return {"replay": replay, "wcat": t.proxyWallet.cat.categories,
            "mcat": t.condition_id.cat.categories, "closed_ts": closed}


def copy_prices(t: pd.DataFrame, rows: pd.DataFrame, delays=(0, 5, 30, 60), horizon: float = 300.0,
                groups: dict | None = None, *, meta: pd.DataFrame | None = None,
                policies=("equal", "proportional")) -> pd.DataFrame:
    """Later other-wallet print proxies, with one-use shares and explicit no-fill metadata.

    Each delay and sizing policy is an independent strategy replay. Zero delay still
    requires a strictly later print: it is a latency sensitivity, not a same-print buy.
    Proportional policy is1% of observable leader notional, capped at$100/order/event.
    Expiry is capped at known Gamma closure before allocation; unknown closure is
    ineligible. This analytical cutoff does not measure public outcome receipt.
    """
    policies = tuple(policies)
    if not policies or len(set(policies)) != len(policies) or not set(policies) <= {"equal", "proportional"}:
        raise ValueError("policies must contain unique equal and/or proportional names")
    out = rows.copy()
    extra = {}
    gb = groups or build_groups(t, meta=meta)
    if "closed_ts" not in gb:
        raise ValueError("replay groups lack market closure metadata; rebuild the index")
    orders = pd.DataFrame({"m": gb["mcat"].get_indexer(rows.condition_id), "s": rows.side_idx.to_numpy(),
        "signal_ts": rows.timestamp.to_numpy(float), "leader_w": gb["wcat"].get_indexer(rows.proxyWallet),
        "event": rows.event_slug.to_numpy(), "y": rows.y.to_numpy(float)})
    closed = np.full(len(rows), np.nan)
    mapped = orders.m.to_numpy() >= 0
    closed[mapped] = gb["closed_ts"][orders.m.to_numpy()[mapped]]
    valid = valid_trade_mask(rows).to_numpy() & np.isfinite(closed) & (closed > 0)
    batches = {"batches": gb["replay"].order_batches(orders)} if isinstance(gb["replay"], _WalletTapeReplay) else {}
    for d in delays:
        expiry = np.minimum(orders.signal_ts.to_numpy() + d + horizon, closed)
        for policy in policies:
            name = "" if policy == "equal" else "prop_"
            budget = np.full(len(rows), 100.) if policy == "equal" else np.minimum(100., .01 * rows["size"].to_numpy() * rows.q.to_numpy())
            result = gb["replay"].replay(orders.assign(budget_usd=np.where(valid,budget,0.), expiry_ts=expiry), delay_s=d,
                                        horizon_s=horizon, event_cap_usd=100., **batches)
            extra[f"{name}q_d{d}"] = result.entry_price.to_numpy()
            for col in ("signal_ts", "receipt_ts", "eligible_ts", "expiry_ts", "fill_ts", "print_id", "shares",
                        "stake_usd", "fee_usd", "cost_usd", "payout", "pnl_usd", "roi", "status"):
                extra[f"{name}{col}_d{d}"] = result[col].to_numpy()
    return pd.concat([out, pd.DataFrame(extra, index=out.index)], axis=1)


def copy_returns(rows: pd.DataFrame, delay: int, stake: str = "equal", *, keep_audit: bool = True) -> pd.DataFrame:
    """Follower results weighted by actually allocated capital, not imaginary$100 bets."""
    prefix = "" if stake == "equal" else "prop_"
    cost_col = f"{prefix}cost_usd_d{delay}"
    if cost_col not in rows:
        raise ValueError("legacy copy-price cache lacks consumed-share audit; rerun copy_prices")
    needed = ["event_slug", "in_play"] + [f"{prefix}{c}_d{delay}" for c in
                ("roi", "q", "shares", "print_id", "cost_usd")]
    source = rows if keep_audit else rows[needed]
    r = source.loc[rows[cost_col] > 0].copy()
    r["copy_roi"] = r[f"{prefix}roi_d{delay}"]
    r["copy_q"] = r[f"{prefix}q_d{delay}"]
    r["copy_shares"] = r[f"{prefix}shares_d{delay}"]
    r["copy_print_id"] = r[f"{prefix}print_id_d{delay}"]
    r["w"] = r[cost_col]
    return r


def _copy_event_totals(r: pd.DataFrame) -> pd.DataFrame:
    # Retain the original per-event Series.sum (numpy reduction), not groupby.sum's
    # different compensated accumulation. Never hand the wide audit to groupby.
    if r.empty:
        return pd.DataFrame(columns=["pnl", "w"], index=r.event_slug.iloc[:0])
    narrow = pd.DataFrame({"event_slug": r.event_slug, "pnl": r.copy_roi * r.w, "w": r.w})
    return narrow.groupby("event_slug", observed=True).apply(
        lambda x: pd.Series({"pnl": x.pnl.sum(), "w": x.w.sum()}), include_groups=False)


def _summarize_events(e: pd.DataFrame, trades: int, n_boot: int = 1000, seed: int = 11) -> dict:
    if e.empty:
        return {"trades": trades, "events": 0, "roi": np.nan, "ci_lo": np.nan, "ci_hi": np.nan}
    roi = e.pnl.sum() / e.w.sum()
    rng = np.random.default_rng(seed)
    boots = np.empty(n_boot)
    # Identical flat RNG sequence and per-draw summation, bounded index temporaries.
    step = max(1, 250_000 // len(e))
    pnl, cash = e.pnl.to_numpy(), e.w.to_numpy()
    for start in range(0, n_boot, step):
        end = min(start + step, n_boot)
        idx = rng.integers(0, len(e), size=(end-start, len(e)))
        boots[start:end] = pnl[idx].sum(1) / cash[idx].sum(1)
    return {"trades": trades, "events": len(e), "roi": float(roi),
            "ci_lo": float(np.percentile(boots, 2.5)), "ci_hi": float(np.percentile(boots, 97.5))}


def summarize_copy(r: pd.DataFrame, n_boot: int = 1000, seed: int = 11) -> dict:
    """Original stake-weighted event bootstrap, with narrow bounded temporaries."""
    if r.empty:
        return {"trades": 0, "events": 0, "roi": np.nan, "ci_lo": np.nan, "ci_hi": np.nan}
    return _summarize_events(_copy_event_totals(r), len(r), n_boot, seed)


def copy_summary(t: pd.DataFrame, rows: pd.DataFrame, delay: int, *, stake: str = "equal",
                 groups: dict | None = None, meta: pd.DataFrame | None = None,
                 phases=("all",), cash_details: bool = False, diagnostic_move: bool = False,
                 batch_orders: int = 25_000, label: str = "copy") -> dict:
    """Replay every signal, retaining event summaries instead of a full audit table.

    Complete market/event connected components share one allocation pool. Phase
    filters are applied AFTER allocation. The canonical ledger still uses the full
    copy_prices audit. One oversize connected component is necessarily kept intact.
    """
    if stake not in ("equal", "proportional") or not set(phases) <= {"all", "pregame", "in_play"}:
        raise ValueError("unknown copy sizing policy or phase")
    if not isinstance(batch_orders, (int, np.integer)) or batch_orders < 1:
        raise ValueError("batch_orders must be positive")
    gb = groups or build_groups(t, meta=meta)
    # Categorical event values avoid a full-row object-string conversion here.
    links = pd.DataFrame({"m": gb["mcat"].get_indexer(rows.condition_id),
                          "event": rows.event_slug.reset_index(drop=True)}, copy=False)
    batches = _WalletTapeReplay.order_batches(links, batch_orders=batch_orders)
    del links
    log_progress(f'{label} delay={delay} policy={stake} start', signals=len(rows),
                 batches=len(batches), max_batch=max(map(len,batches),default=0))
    totals = {p: [] for p in phases}
    counts = dict.fromkeys(phases, 0)
    cash_parts = []
    moves = np.full(len(rows), np.nan) if diagnostic_move else None
    for i, positions in enumerate(batches):
        audit = copy_prices(t, rows.iloc[positions], delays=(delay,), groups=gb, policies=(stake,))
        funded = copy_returns(audit, delay, stake=stake, keep_audit=False)
        if diagnostic_move:
            prefix = '' if stake == 'equal' else 'prop_'
            moves[positions] = audit[f'{prefix}q_d{delay}'].to_numpy() - audit.q.to_numpy()
        if cash_details:
            prefix = '' if stake == 'equal' else 'prop_'
            funded_positions = positions[audit[f'{prefix}cost_usd_d{delay}'].to_numpy() > 0]
            # Only monthly cash totals need original global-row summation order.
            cash_parts.append(pd.DataFrame({'position': funded_positions,
                'copy_roi': funded.copy_roi.to_numpy(), 'w': funded.w.to_numpy(),
                'in_play': funded.in_play.to_numpy()}))
        del audit
        for phase in phases:
            part = funded if phase == 'all' else funded.loc[funded.in_play.eq(phase == 'in_play')]
            counts[phase] += len(part)
            if len(part):
                totals[phase].append(_copy_event_totals(part))
        del funded
        if (i+1) % 20 == 0 or i+1 == len(batches):
            log_progress(f'{label} delay={delay} policy={stake} replay', batches_done=i+1,
                         batches=len(batches), signals_done=sum(map(len,batches[:i+1])))
    cash = (pd.concat(cash_parts, ignore_index=True).sort_values('position', kind='stable')
            if cash_parts else pd.DataFrame(columns=['position','copy_roi','w','in_play']))
    result = {}
    for phase in phases:
        e = pd.concat(totals[phase]).sort_index() if totals[phase] else pd.DataFrame(columns=['pnl','w'])
        if e.index.duplicated().any():
            raise AssertionError('copy summary split an event across independent allocation pools')
        summary = _summarize_events(e, counts[phase])
        if cash_details:
            part = cash if phase == 'all' else cash.loc[cash.in_play.eq(phase == 'in_play')]
            summary.update(pnl=float((part.copy_roi * part.w).sum()), capital=float(part.w.sum()),
                           in_play_share=float(part.in_play.mean()))
        if diagnostic_move:
            values = moves if phase == 'all' else moves[rows.in_play.to_numpy() == (phase == 'in_play')]
            summary.update(funded_move_5_10min=float(pd.Series(values).mean()),
                           move_funded_signals=int(np.isfinite(values).sum()))
        result[phase] = summary
    log_progress(f'{label} delay={delay} policy={stake} done', signals=len(rows), funded=counts)
    return result
