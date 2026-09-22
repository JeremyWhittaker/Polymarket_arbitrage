"""Soccer in-game events (ESPN) joined to our Polymarket 3-way moneyline markets.

Pipeline (each stage caches; re-runs are cheap):

    python -m pmsports.events.soccer build        # everything, in order
    python -m pmsports.events.soccer scoreboards  # 1. ESPN scoreboards for our dates
    python -m pmsports.events.soccer match        # 2. our event_slug  <->  ESPN event id
    python -m pmsports.events.soccer summaries    # 3. ESPN summaries for matched games
    python -m pmsports.events.soccer events       # 4. flatten keyEvents[]
    python -m pmsports.events.soccer panel        # 5. state x price panel
    python -m pmsports.events.soccer validate     # 6. checks + coverage report

Load the results with `games()`, `events()` and `panel()` (see those functions).

Outputs (under data/events/soccer/):
    games.parquet    4,775 matched games: our event_slug, the three market codes and
                     condition ids (home / draw / away legs), payouts, kickoff, ESPN id,
                     ESPN team names and final score, match diagnostics.
    events.parquet   106,787 ESPN key events: wallclock, period, clock_s (elapsed seconds,
                     so minute = clock_s/60), kind, team_side, scoring_side, the running
                     score after the event, lead, and the raw text.
    panel.parquet    106,519 event x game rows: everything in events.parquet plus, for each
                     of the three legs, the price before the event (p_<leg>_pre) and at
                     -10/-30/-60 s (p_<leg>_m10..m60) and +3/+10/+30/+60/+300 s
                     (p_<leg>_h3..h300), staleness, local trade counts, and the payouts.

Raw ESPN JSON is cached gzipped under data/events/soccer/{scoreboard,summary}/; legs too
thin for fills.parquet are backfilled from the Data API into data/events/soccer/extra_fills/.

Column key (panel)
------------------
    p_<leg>_pre      last YES print with ts <= wallclock              age_<leg>_pre  its age
    p_<leg>_m10/30/60  last YES print with ts <= wallclock - offset
    p_<leg>_h3..h300 first YES print with ts >= wallclock + h    lag_<leg>_h*  its true offset
    n_<leg>_5m       fills on that leg in the 5 min before the event (liquidity)
    src_<leg>        0 none, 1 fills.parquet, 2 Data-API backfill
    y_<leg>          payout of that leg (1 win, 0 lose, 0.5 void)
    reg_home/reg_away/reg_result   score and result at 90 minutes (what the market settles on)
    ft_home/ft_away, had_et, had_shootout, home_score/away_score (ESPN header, incl. ET)
    feed_consistent  goal-event count == goals in the final score AND >= 5 events (an
                     outcome-free data-quality flag; `panel(clean=True)` applies it)

Price conventions
-----------------
Every soccer market here is a binary Yes/No market ("Will X win?", "... draw"), with
`o0 == "Yes"`.  A fill records the price `q` the taker paid for side `s`; the implied
price of YES is `q if s == 0 else 1 - q`.  All `p_*` columns are YES prices, i.e. the
market-implied probability of that leg.  The three legs sum to a median 1.001.

Three things that will fake an edge here
----------------------------------------
1.  **ESPN's wallclock is not the moment the goal became public.**  Measured on 11,673
    goals, the scoring team's leg has already moved a median +0.030 by the last print at
    or before the wallclock, +0.138 by +3 s, and +0.180 by +60 s.  The wallclock is when
    ESPN's operator typed the event; the market is seconds ahead of it.  `p_*_pre` is NOT
    an uninformed pre-goal price - use `p_*_m60` if you need one, and never "buy at the
    pre-goal price".
2.  **Polymarket settles on REGULATION time.**  A cup tie won in extra time or on
    penalties pays the DRAW leg.  `espn_result` (ESPN's header) disagrees with the payouts
    on 0.6% of games; `reg_result` agrees on 99.75%.  Always use `reg_*`.
3.  **The draw leg is the thin one.**  Only 58.7% of event rows have a draw price at all
    (85.2% home, 74.0% away), because a leg needs $50k of volume to be in fills.parquet -
    and 1,305 missing legs were backfilled from the Data API precisely to reduce that.
    Never select games on `volume`: it includes in-play trading.

Execution honesty
-----------------
Fill timestamps are on-chain settlement times, a median 2.6 s AFTER the trade, while
`wallclock` is the real-world time of the event.  So a fill with `ts <= wallclock` was
agreed ~2.6 s before the wallclock (that is the `_pre` column), and a fill with
`ts >= wallclock + h` is a print you could only have taken at or after `wallclock + h`
(the `_h3 .. _h300` columns).  `_h3` is the earliest execution-legal entry.
"""
from __future__ import annotations

import gzip
import json
import re
import sys
import unicodedata
from collections import Counter
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import numpy as np
import pandas as pd

from .. import http
from ..research import common as C

ROOT = Path(__file__).resolve().parents[2]
CACHE = ROOT / "data" / "events" / "soccer"
SCOREBOARD = CACHE / "scoreboard"
SUMMARY = CACHE / "summary"
GAMES = CACHE / "games.parquet"
EVENTS = CACHE / "events.parquet"
PANEL = CACHE / "panel.parquet"
ESPN_GAMES = CACHE / "espn_games.parquet"
EXTRA = CACHE / "extra_fills"   # Data-API backfill for legs below the $50k fills.parquet cut

BASE = "https://site.api.espn.com/apis/site/v2/sports/soccer/{path}/{kind}"
http.HOST_RPS.setdefault("site.api.espn.com", 5.0)   # be polite
WORKERS = 8

# Horizons (seconds after the event wallclock) at which we take the next print on each leg.
HORIZONS = (3, 10, 30, 60, 300)
# Offsets (seconds BEFORE the wallclock) at which we take the last print - `p_x_m60` vs
# `p_x_pre` shows whether the market had already moved before ESPN stamped the event.
PRE_OFFSETS = (10, 30, 60)
LEGS = ("home", "draw", "away")

# ---------------------------------------------------------------------- league -> ESPN path
# Verified against the live scoreboard endpoint (2026-09).  Leagues absent from this map
# have no public ESPN scoreboard (k-league, poland, egypt, ukraine, hungary, uae, morocco,
# jpn.2, croatia, slovakia, several cups) or are not 3-way moneylines at all.
LEAGUE_PATH: dict[str, str] = {
    "premier-league-2025": "eng.1",
    "la-liga-2025": "esp.1",
    "serie-a-2025": "ita.1",
    "bundesliga-2025": "ger.1",
    "ucl-2025": "uefa.champions",
    "mls-2025": "usa.1",
    "ligue-1-2025": "fra.1",
    "fifa-friendly": "fifa.friendly",
    "soccer-fifwc": "fifa.world",
    "uel-2025": "uefa.europa",
    "brazil-serie-a": "bra.1",
    "efl-championship": "eng.2",
    "uef-qualifiers": "fifa.worldq.uefa",
    "saudi-professional-league": "ksa.1",
    "mex-2025": "mex.1",
    "primera-divisin-argentina": "arg.1",
    "tur-2025": "tur.1",
    "chinese-super-league": "chn.1",
    "primeira-liga": "por.1",
    "europa-conference-league": "uefa.europa.conf",
    "ere-2025": "ned.1",
    "a-league-soccer": "aus.1",
    "la-liga-2": "esp.2",
    "lib-2025": "conmebol.libertadores",
    "bundesliga-2": "ger.2",
    "sud-2025": "conmebol.sudamericana",
    "soccer-lec": "concacaf.leagues.cup",
    "japan-j-league": "jpn.1",
    "norway-eliteserien": "nor.1",
    "clf-games": "club.friendly",
    "fa-cup": "eng.fa",
    "efl-cup": "eng.league_cup",
    "sweden-allsvenskan-2026": "swe.1",
    "scottish-premiership": "sco.1",
    "denmark-superliga": "den.1",
    "primera-a": "col.1",
    "serie-b": "ita.2",
    "copa-del-rey": "esp.copa_del_rey",
    "brazil-serie-b": "bra.2",
    "itc-2025": "ita.coppa_italia",
    "soccer-brco": "bra.copa_do_brazil",
    "primera-division": "chi.1",
    "dfb-pokal": "ger.dfb_pokal",
    "ligue-2": "fra.2",
    "liga-1": "per.1",
    "romania-1": "rou.1",
    "coupe-de-france": "fra.coupe_de_france",
    "afc-champions-league-elite-2026": "afc.champions",
    "switzerland-super-league": "sui.1",
    "bel1-games": "bel.1",
    "caf": "caf.champions",
    "soccer-el1": "eng.3",
    "czechia-1": "cze.1",
    "ecu1-games": "ecu.1",
    "ecs-games": "ecu.1",
    "asean-games": "aff.championship",
    "bolivia-1": "bol.1",
    "fin1-games": "fin.1",
    "ned2-games": "ned.2",
    "austria-bundesliga-2026": "aut.1",
    "womens-champions-league": "uefa.wchampions",
    "israel-premier-league-2026": "isr.1",
    "russian-premier-league": "rus.1",
    "indian-super-league": "ind.1",
    "spanish-super-cup": "esp.super_cup",
    "uefa-super-cup": "uefa.super_cup",
    "argcopa-games": "arg.copa",
    "concacaf": "concacaf.champions_cup",
    "soccer-ccc": "concacaf.champions_cup",
    "tur2-games": "tur.2",
    "swe2-games": "swe.2",
    "idn1-games": "idn.1",
    "irl1-games": "irl.1",
    "bra3-games": "bra.3",
    "chi2-games": "chi.2",
    "argpn-games": "arg.2",
    "soccer-grc": "gre.1",
    "soccer-enl": "eng.5",
    "soccer-auc": "aus.w.1",
    "gold": "concacaf.gold",
    "uefa": "uefa.nations",
}

# Some Polymarket leagues span two ESPN paths: the group stage and the qualifying rounds
# live under separate slugs, which cost ~300 UCL/UEL/UECL games on the first pass.
EXTRA_PATHS: dict[str, tuple[str, ...]] = {
    "ucl-2025": ("uefa.champions_qual",),
    "uel-2025": ("uefa.europa_qual",),
    "europa-conference-league": ("uefa.europa.conf_qual",),
}


def paths_for(league: str) -> tuple[str, ...]:
    p = LEAGUE_PATH.get(league)
    return () if p is None else (p,) + EXTRA_PATHS.get(league, ())


# ------------------------------------------------------------------------------- utilities

def _jget(url: str, params: dict, path: Path):
    """GET + cache the raw response gzipped.  Returns the parsed JSON (None on 4xx)."""
    if path.exists():
        try:
            with gzip.open(path, "rt", encoding="utf-8") as fh:
                return json.load(fh)
        except (OSError, ValueError):
            path.unlink(missing_ok=True)
    try:
        js = http.get_json(url, params)
    except http.HTTPError as e:
        if 400 <= e.status < 500:
            return None
        raise
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    with gzip.open(tmp, "wt", encoding="utf-8") as fh:
        json.dump(js, fh)
    tmp.replace(path)
    return js


def _norm(s: str | None) -> str:
    """lowercase, de-accent, alphanumeric only."""
    if not s:
        return ""
    s = unicodedata.normalize("NFKD", str(s))
    s = "".join(c for c in s if not unicodedata.combining(c))
    return re.sub(r"[^a-z0-9]", "", s.lower())


_STOP = {"fc", "afc", "cf", "sc", "ac", "cd", "ca", "sv", "vfl", "vfb", "tsg", "fsv", "bsc",
         "club", "de", "do", "da", "del", "la", "el", "the", "sport", "sports", "clube",
         "sporting", "athletic", "atletico", "real", "deportivo", "united", "city", "town"}


def _variants(team: dict) -> tuple[list[str], list[str]]:
    """(full normalised names, word-level keys) an abbreviation could match against."""
    full, keys = [], []
    for k in ("abbreviation", "shortDisplayName", "name", "displayName", "nickname", "location"):
        v = team.get(k)
        if not v:
            continue
        full.append(_norm(v))
        toks = [t for t in re.split(r"[^A-Za-z0-9]+", str(v)) if t]
        sig = [_norm(t) for t in toks if _norm(t) not in _STOP and _norm(t)]
        keys.extend(sig)                                          # every significant word
        if sig:
            keys.append("".join(t[0] for t in sig))               # initials of those words
        if toks:
            keys.append("".join(t[0] for t in toks).lower())      # initials of all words
    return [v for v in dict.fromkeys(full) if v], [v for v in dict.fromkeys(keys) if v]


def _abbr_score(abbr: str, team: dict) -> float:
    """How well our 2-4 letter slug token identifies this ESPN team.  0 .. 1.

    Deliberately conservative: a loose "letters appear in order somewhere in the name"
    rule matched `shp` to *Shanghai Port* and `sgr` to it too, which flipped home/away on
    Chinese Super League games.  Prefixes and word-initials only; `match()` then learns the
    real abbreviation -> team map from games that are unambiguous on kickoff time.
    """
    import difflib
    # Polymarket appends a digit when two teams in a league share a prefix
    # (ucl "ast4", "bas1"); the digit carries no information about the name.
    a = re.sub(r"\d+$", "", _norm(abbr))
    if not a:
        return 0.0
    full, keys = _variants(team)
    best = 0.0
    for v in full + keys:
        if v == a:
            return 1.0
    for v in keys:
        if v.startswith(a) and len(a) >= 2:
            best = max(best, 0.90)
        elif a.startswith(v) and len(v) >= 3:
            best = max(best, 0.82)
        elif len(a) >= 3 and a[0] == v[0]:
            # a consonant-skeleton hit inside ONE word, e.g. bri -> brighton, mnc -> manchester
            i = 0
            for ch in v:
                if i < len(a) and ch == a[i]:
                    i += 1
            if i == len(a):
                best = max(best, 0.70)
    for v in full:
        if v.startswith(a) and len(a) >= 3:
            best = max(best, 0.88)
        best = max(best, 0.55 * difflib.SequenceMatcher(None, a, v[:max(len(a), 3)]).ratio())
    return best


def _ts(iso: str | None) -> float:
    if not iso:
        return np.nan
    return pd.Timestamp(iso).tz_convert("UTC").timestamp() if pd.Timestamp(iso).tzinfo \
        else pd.Timestamp(iso, tz="UTC").timestamp()


# --------------------------------------------------------------------------- our side (PM)

SLUG_RE = re.compile(r"^(?P<prefix>[a-z0-9]+)-(?P<a>[a-z0-9]{2,5})-(?P<b>[a-z0-9]{2,5})-"
                     r"(?P<date>\d{4}-\d{2}-\d{2})$")


_OG_CACHE: pd.DataFrame | None = None


def our_games() -> pd.DataFrame:
    """One row per soccer game, with all three moneyline legs resolved.

    Legs come from `C.universe()` (which holds every resolved leg, so payouts are known
    even for legs too thin to be in `C.markets()`); `m_a/m_b/m_draw` is the fills.parquet
    market code where one exists, and NA where that leg is below the $50k volume cut.
    `a` / `b` are the two team tokens in the event slug, in slug order; the matcher decides
    which is ESPN's home side, so nothing here assumes an orientation.
    """
    global _OG_CACHE
    if _OG_CACHE is not None:
        return _OG_CACHE.copy()
    u = C.universe(columns=["condition_id", "family", "league", "market_type", "event_slug",
                            "market_slug", "outcome_idx", "payout", "volume", "fee_rate",
                            "game_start_ts", "closed_ts"])
    s = u[(u.family == "soccer") & (u.market_type == "moneyline") &
          (u.outcome_idx == 0) & u.event_slug.notna()].copy()
    del u
    parts = s.event_slug.str.extract(SLUG_RE)
    s = s.join(parts)
    s = s[parts.date.notna()].copy()
    # which leg is this market?  market_slug == event_slug + "-" + token
    s["tok"] = [ms[len(es) + 1:] if isinstance(ms, str) and ms.startswith(es + "-") else ""
                for ms, es in zip(s.market_slug, s.event_slug)]
    s["leg"] = np.where(s.tok == "draw", "draw",
                        np.where(s.tok == s.a, "a", np.where(s.tok == s.b, "b", "")))
    s = s[s.leg != ""].drop_duplicates(["event_slug", "leg"])
    s["espn_path"] = s.league.map(LEAGUE_PATH)

    mk = C.markets()
    mk = mk[mk.family == "soccer"]
    code = mk.set_index("condition_id")
    s["m"] = s.condition_id.map(code.m).astype("Int32")
    s["pre_ask0"] = s.condition_id.map(code.pre_ask0)
    s["pre_mid0"] = s.condition_id.map(code.pre_mid0)
    s["pre_usd"] = s.condition_id.map(code.pre_usd)
    s["pre_n"] = s.condition_id.map(code.pre_n_fills)

    key = ["event_slug", "league", "espn_path", "a", "b", "date"]
    out = s.groupby(key, dropna=False, observed=True).agg(
        game_start_ts=("game_start_ts", "min"), closed_ts=("closed_ts", "max"),
        fee_rate=("fee_rate", "max"), n_legs_universe=("condition_id", "size"),
        pre_usd=("pre_usd", "sum"), pre_n_fills=("pre_n", "sum")).reset_index()
    for lab in ("a", "b", "draw"):
        sub = s[s.leg == lab].set_index("event_slug")
        out[f"m_{lab}"] = out.event_slug.map(sub.m).astype("Int32")
        out[f"cid_{lab}"] = out.event_slug.map(sub.condition_id)
        out[f"y_{lab}"] = out.event_slug.map(sub.payout)       # payout of "Yes"
        out[f"ask_{lab}"] = out.event_slug.map(sub.pre_ask0)
        out[f"mid_{lab}"] = out.event_slug.map(sub.pre_mid0)
    out["n_legs"] = out[[f"m_{l}" for l in ("a", "b", "draw")]].notna().sum(axis=1)
    # Games with no local leg have no price history at all, so they can never enter the
    # panel; drop them from every denominator but keep the total for the coverage report.
    n_universe = len(out)
    out = out[out.n_legs >= 1].reset_index(drop=True)
    out.attrs["universe_games"] = n_universe
    _OG_CACHE = out
    return out.copy()


# ------------------------------------------------------------------------ 1. ESPN scoreboards

def _sb_path(path: str, day: str) -> Path:
    return SCOREBOARD / path / f"{day}.json.gz"


def fetch_scoreboards(verbose: bool = True) -> pd.DataFrame:
    """Fetch (and cache) one scoreboard per (ESPN league path, date +/- 1 day) we need."""
    og = our_games()
    og = og[og.espn_path.notna()]
    jobs: set[tuple[str, str]] = set()
    for lg, d in zip(og.league, og.date):
        day = pd.Timestamp(d)
        for p in paths_for(lg):
            for off in (-1, 0, 1):
                jobs.add((p, (day + pd.Timedelta(days=off)).strftime("%Y%m%d")))
    jobs = sorted(jobs)
    todo = [j for j in jobs if not _sb_path(*j).exists()]
    if verbose:
        print(f"scoreboards: {len(jobs):,} (path, date) pairs, {len(todo):,} to fetch")

    def one(job):
        p, day = job
        return _jget(BASE.format(path=p, kind="scoreboard"), {"dates": day, "limit": 400},
                     _sb_path(p, day))

    if todo:
        with ThreadPoolExecutor(WORKERS) as ex:
            for i, _ in enumerate(ex.map(one, todo), 1):
                if verbose and i % 250 == 0:
                    print(f"  {i:,}/{len(todo):,}", flush=True)
    return build_espn_games(jobs, verbose=verbose)


def build_espn_games(jobs=None, verbose: bool = True) -> pd.DataFrame:
    """Flatten every cached scoreboard into one row per ESPN game."""
    if jobs is None:
        jobs = [(p.parent.name, p.stem.split(".")[0]) for p in SCOREBOARD.glob("*/*.json.gz")]
    rows, seen = [], set()
    for p, day in jobs:
        f = _sb_path(p, day)
        if not f.exists():
            continue
        try:
            with gzip.open(f, "rt", encoding="utf-8") as fh:
                js = json.load(fh)
        except (OSError, ValueError):
            continue
        for e in (js.get("events") or []):
            eid = str(e.get("id"))
            if (p, eid) in seen:
                continue
            seen.add((p, eid))
            comp = (e.get("competitions") or [{}])[0]
            cs = {c.get("homeAway"): c for c in (comp.get("competitors") or [])}
            h, aw = cs.get("home"), cs.get("away")
            if not h or not aw:
                continue
            st = (comp.get("status") or {}).get("type") or {}
            rows.append({
                "espn_path": p, "espn_id": eid,
                "espn_ts": _ts(e.get("date")),
                "home_id": str((h.get("team") or {}).get("id")),
                "away_id": str((aw.get("team") or {}).get("id")),
                "home_name": (h.get("team") or {}).get("displayName"),
                "away_name": (aw.get("team") or {}).get("displayName"),
                "home_abbr": (h.get("team") or {}).get("abbreviation"),
                "away_abbr": (aw.get("team") or {}).get("abbreviation"),
                "home_team_json": json.dumps(h.get("team") or {}),
                "away_team_json": json.dumps(aw.get("team") or {}),
                "home_score": pd.to_numeric(h.get("score"), errors="coerce"),
                "away_score": pd.to_numeric(aw.get("score"), errors="coerce"),
                "state": st.get("state"), "completed": bool(st.get("completed")),
                "status": st.get("name"),
                "neutral": bool(comp.get("neutralSite")),
            })
    df = pd.DataFrame(rows)
    ESPN_GAMES.parent.mkdir(parents=True, exist_ok=True)
    df.to_parquet(ESPN_GAMES, index=False)
    if verbose:
        print(f"espn games: {len(df):,} across {df.espn_path.nunique()} league paths")
    return df


# ----------------------------------------------------------------------------- 2. matching

def _learn_teams(ours: pd.DataFrame, cand: pd.DataFrame, abbrs, tjson, tab,
                 tol: float = 600.0, min_games: int = 2, min_share: float = 0.6) -> dict[str, str]:
    """Learn slug abbreviation -> ESPN team id inside one league, without orientation.

    Seed on games that are unambiguous on kickoff time alone (exactly one ESPN game within
    `tol` seconds of our scheduled start, and that ESPN game is the sole candidate of
    exactly one of our games).  Each seed says the unordered pair {a, b} is the unordered
    pair {home, away}; counting which ESPN team shows up in every game an abbreviation
    appears in identifies it.  Resolved 1-to-1 so two abbreviations cannot claim one team.
    """
    from scipy.optimize import linear_sum_assignment

    ets = cand.espn_ts.to_numpy()
    order = np.argsort(ets)
    ets_s, idx_s = ets[order], order
    claims: dict[int, list[int]] = {}
    ost = ours.game_start_ts.to_numpy(float)
    for i, t in enumerate(ost):
        if not np.isfinite(t):
            continue
        lo, hi = np.searchsorted(ets_s, [t - tol, t + tol])
        if hi - lo == 1:
            claims.setdefault(int(idx_s[lo]), []).append(i)
    seeds = [(rows[0], j) for j, rows in claims.items() if len(rows) == 1]
    if not seeds:
        return {}

    oa, ob = ours.a.to_numpy(), ours.b.to_numpy()
    hid, aid = cand.home_id.to_numpy(), cand.away_id.to_numpy()
    ai = {ab: k for k, ab in enumerate(abbrs)}
    tids = sorted(tjson)
    ti = {t: k for k, t in enumerate(tids)}
    cnt = np.zeros((len(abbrs), len(tids)))
    seen = np.zeros(len(abbrs))
    for i, j in seeds:
        for ab in (oa[i], ob[i]):
            seen[ai[ab]] += 1
            cnt[ai[ab], ti[hid[j]]] += 1
            cnt[ai[ab], ti[aid[j]]] += 1
    # prefer the modal partner, with a light prior from the string score to break ties
    prior = np.array([[tab[(ab, t)] for t in tids] for ab in abbrs])
    ri, ci = linear_sum_assignment(-(cnt + 0.01 * prior))
    out = {}
    for i, j in zip(ri, ci):
        if seen[i] >= min_games and cnt[i, j] >= min_share * seen[i] and cnt[i, j] >= min_games:
            out[abbrs[i]] = tids[j]
    return out


def match(verbose: bool = True) -> pd.DataFrame:
    """Assign our games to ESPN games, per (league path, calendar day), 1-to-1.

    Score = 2 * mean(team-abbreviation score, best over the two slug orientations)
            + time proximity of the scheduled kickoff.  Outcomes are never used.
    """
    from scipy.optimize import linear_sum_assignment

    og = our_games()
    eg = pd.read_parquet(ESPN_GAMES)
    dead = {"STATUS_POSTPONED", "STATUS_CANCELED", "STATUS_CANCELLED", "STATUS_ABANDONED",
            "STATUS_FORFEIT", "STATUS_SUSPENDED"}
    eg = eg[(eg.state == "post") & ~eg.status.isin(dead)].reset_index(drop=True)

    out = []
    learned_all: dict[tuple[str, str], str] = {}
    for league, grp in og[og.espn_path.notna()].groupby("league", observed=True):
        paths = paths_for(league)
        cand = eg[eg.espn_path.isin(paths)].reset_index(drop=True)
        if cand.empty:
            continue
        # team score lookup: our abbreviation x ESPN team id (tiny per league, reused a lot)
        tjson: dict[str, dict] = {}
        for r in cand.itertuples():
            tjson.setdefault(r.home_id, json.loads(r.home_team_json))
            tjson.setdefault(r.away_id, json.loads(r.away_team_json))
        abbrs = sorted(set(grp.a) | set(grp.b))
        tab = {(ab, tid): _abbr_score(ab, t) for ab in abbrs for tid, t in tjson.items()}
        hid, aid = cand.home_id.to_numpy(), cand.away_id.to_numpy()
        ets = cand.espn_ts.to_numpy()

        # -- learn abbreviation -> ESPN team id from games that are unambiguous on kickoff
        #    time alone.  Each such game pairs the UNORDERED set {a, b} with {home, away},
        #    and an abbreviation's own team is the one present in every game it appears in,
        #    so this needs no orientation and no outcome.
        learned = _learn_teams(grp, cand, abbrs, tjson, tab)
        learned_all.update({(league, k): v for k, v in learned.items()})
        owner = {tid: ab for ab, tid in learned.items()}

        def sc(ab, tid, _l=learned, _o=owner, _t=tab):
            got = _l.get(ab)
            if got is not None:
                return 1.0 if got == tid else 0.0
            if tid in _o:                 # that team already belongs to another abbreviation
                return 0.0
            return _t[(ab, tid)]

        for day, ours in grp.groupby("date"):
            d0 = pd.Timestamp(day, tz="UTC").timestamp()
            sel = np.flatnonzero((ets >= d0 - 24 * 3600) & (ets <= d0 + 48 * 3600))
            if not len(sel):
                continue
            ours = ours.reset_index(drop=True)
            oa, ob = ours.a.to_numpy(), ours.b.to_numpy()
            ost = ours.game_start_ts.to_numpy(float)
            S = np.zeros((len(ours), len(sel)))
            NAME = np.zeros_like(S)
            MINS = np.zeros_like(S)
            ORI = np.zeros(S.shape, dtype=np.int8)
            for i in range(len(ours)):
                for jj, j in enumerate(sel):
                    ha = (sc(oa[i], hid[j]), sc(ob[i], aid[j]))    # slug = home-away
                    ah = (sc(oa[i], aid[j]), sc(ob[i], hid[j]))    # slug = away-home
                    pick = ha if sum(ha) >= sum(ah) else ah
                    nm = 0.5 * sum(pick)
                    NAME[i, jj] = nm
                    MINS[i, jj] = min(pick)
                    ORI[i, jj] = 0 if sum(ha) >= sum(ah) else 1
                    dt = abs(ost[i] - ets[j])
                    S[i, jj] = 2.0 * nm + (np.exp(-dt / 3600.0) if np.isfinite(dt) else 0.0)
                    if np.isfinite(dt) and dt > 36 * 3600:
                        S[i, jj] = -1e6
            ri, ci = linear_sum_assignment(-S)
            for i, jj in zip(ri, ci):
                if S[i, jj] <= -1e5:
                    continue
                j = sel[jj]
                out.append({
                    "event_slug": ours.event_slug[i],
                    "espn_path": cand.espn_path[j], "espn_id": cand.espn_id[j],
                    "name_score": float(NAME[i, jj]), "min_team_score": float(MINS[i, jj]),
                    "dt_start": float(ost[i] - ets[j]),
                    "slug_home_first": bool(ORI[i, jj] == 0),
                })
    mt = pd.DataFrame(out)
    if mt.empty:
        raise RuntimeError("no matches - run scoreboards first")
    # keep the best claim on each ESPN game (days overlap at the boundary)
    mt = mt.sort_values("name_score", ascending=False).drop_duplicates(["espn_path", "espn_id"])
    mt = mt.sort_values("name_score", ascending=False).drop_duplicates("event_slug")

    good = (mt.min_team_score >= 0.30) & \
           ((mt.name_score >= 0.62) | ((mt.name_score >= 0.55) & (mt.dt_start.abs() <= 900)))
    if verbose:
        print(f"match: {int(good.sum()):,} of {len(og):,} games accepted "
              f"({int((~good).sum()):,} rejected on score)")
    mt = mt[good]

    g = og.drop(columns=["espn_path"]).merge(mt, on="event_slug", how="inner")
    eg2 = eg.drop(columns=["home_team_json", "away_team_json"])
    g = g.merge(eg2, on=["espn_path", "espn_id"], how="left")

    # orient our legs onto ESPN home/away
    hf = g.slug_home_first.to_numpy()
    for pre in ("m_", "y_", "ask_", "mid_", "cid_"):
        a, b = g[f"{pre}a"], g[f"{pre}b"]
        g[f"{pre}home"] = np.where(hf, a, b)
        g[f"{pre}away"] = np.where(hf, b, a)
    for pre in ("m_",):
        g[f"{pre}home"] = g[f"{pre}home"].astype("Int32")
        g[f"{pre}away"] = g[f"{pre}away"].astype("Int32")
    g["abbr_home"] = np.where(hf, g.a, g.b)
    g["abbr_away"] = np.where(hf, g.b, g.a)
    g = g.drop(columns=[c for c in g.columns if c.endswith(("_a", "_b")) and c[:-2] in
                        ("m", "y", "ask", "mid", "cid")] + ["a", "b"])

    # independent validation: did our payouts agree with ESPN's final score?
    espn_res = np.where(g.home_score > g.away_score, "home",
                        np.where(g.home_score < g.away_score, "away", "draw"))
    pm_res = np.where(g.y_home == 1, "home", np.where(g.y_away == 1, "away",
                      np.where(g.y_draw == 1, "draw", "")))
    g["espn_result"] = espn_res
    g["pm_result"] = pm_res
    g["result_agrees"] = (espn_res == pm_res)
    GAMES.parent.mkdir(parents=True, exist_ok=True)
    g.to_parquet(GAMES, index=False)
    if verbose:
        dec = g[g.pm_result != ""]
        print(f"games.parquet: {len(g):,} matched games; result agreement "
              f"{dec.result_agrees.mean():.4%} on {len(dec):,} decided games")
    return g


# ---------------------------------------------------------------------------- 3. summaries

def _sum_path(path: str, eid: str) -> Path:
    return SUMMARY / path / f"{eid}.json.gz"


def backfill_legs(max_markets: int = 2000, verbose: bool = True) -> int:
    """Pull taker fills for matched games' legs that are missing from fills.parquet.

    Those legs traded under $50k total volume, so they are absent from `C.markets()`;
    without them a game has no complete 3-way book.  We only backfill games that already
    have BOTH win legs locally (so the missing leg is almost always the draw), newest
    first, capped at `max_markets` markets per the Data-API budget in the research guide.
    Prices are normalised the same way as fills.parquet: YES price = p for outcome 0,
    1 - p for outcome 1, independent of the taker's BUY/SELL direction.
    """
    from ..polymarket import trades
    http.HOST_RPS["data-api.polymarket.com"] = 5.0

    g = pd.read_parquet(GAMES)
    need = []
    for r in g.itertuples():
        have = [l for l in ("home", "away", "draw") if pd.notna(getattr(r, f"m_{l}"))]
        if len(have) != 2:
            continue
        miss = [l for l in ("home", "away", "draw") if l not in have][0]
        cid = getattr(r, f"cid_{miss}")
        if not isinstance(cid, str) or (EXTRA / f"{cid}.parquet").exists():
            continue
        need.append((cid, float(r.game_start_ts), float(r.closed_ts)))
    need.sort(key=lambda x: -x[1])
    need = need[:max_markets]
    if verbose:
        print(f"backfill: {len(need):,} missing legs to fetch from the Data API")
    EXTRA.mkdir(parents=True, exist_ok=True)

    def one(job):
        cid, st, cl = job
        lo = int(st - 3 * 86400)
        hi = int((cl if np.isfinite(cl) else st + 4 * 3600) + 3600)
        try:
            tr = trades(cid, lo, hi)
        except Exception as e:                       # keep going; report at the end
            print(f"  {cid[:12]} failed: {type(e).__name__} {e}"[:140], flush=True)
            return 0
        if tr:
            d = pd.DataFrame(tr)
            p = pd.to_numeric(d.price, errors="coerce").to_numpy(float)
            oi = pd.to_numeric(d.outcomeIndex, errors="coerce").to_numpy(float)
            out = pd.DataFrame({"ts": pd.to_numeric(d.timestamp, errors="coerce").to_numpy(np.int64),
                                "p_yes": np.where(oi == 0, p, 1.0 - p).astype(np.float32),
                                "size": pd.to_numeric(d["size"], errors="coerce").to_numpy(np.float32)})
            out = out.dropna().sort_values("ts", kind="stable")
        else:
            out = pd.DataFrame({"ts": np.zeros(0, np.int64), "p_yes": np.zeros(0, np.float32),
                                "size": np.zeros(0, np.float32)})
        out.to_parquet(EXTRA / f"{cid}.parquet", index=False)
        return len(out)

    n = 0
    with ThreadPoolExecutor(4) as ex:
        for i, k in enumerate(ex.map(one, need), 1):
            n += k
            if verbose and i % 100 == 0:
                print(f"  {i:,}/{len(need):,}  {n:,} fills", flush=True)
    if verbose:
        print(f"backfill: {n:,} fills into {EXTRA}")
    return n


def fetch_summaries(verbose: bool = True) -> None:
    g = pd.read_parquet(GAMES, columns=["espn_path", "espn_id"])
    jobs = sorted(set(zip(g.espn_path, g.espn_id)))
    todo = [j for j in jobs if not _sum_path(*j).exists()]
    if verbose:
        print(f"summaries: {len(jobs):,} games, {len(todo):,} to fetch")

    def one(job):
        p, eid = job
        return _jget(BASE.format(path=p, kind="summary"), {"event": eid}, _sum_path(p, eid))

    if todo:
        with ThreadPoolExecutor(WORKERS) as ex:
            for i, _ in enumerate(ex.map(one, todo), 1):
                if verbose and i % 250 == 0:
                    print(f"  {i:,}/{len(todo):,}", flush=True)


# ------------------------------------------------------------------- 4. flatten keyEvents[]

# The score sentence in an ESPN key event is always its own sentence, e.g.
#   "Goal! | Southampton 1, Leicester City 0. | Cyle Larin (Southampton) converts ..."
#   "Own Goal by James Bree, Southampton. | Leicester City 1, Southampton 1."
# so split on sentence boundaries first; matching across them picked up player names.
SENT_RE = re.compile(r"(?<=[.!?])\s+")
SCORE_RE = re.compile(r"^\s*([^\d]{2,45}?)\s(\d{1,2}),\s([^\d]{2,45}?)\s(\d{1,2})\s*[.!]?\s*$")

PERIOD_HINTS = ("kickoff", "halftime", "start-", "end-", "full-time", "extra-time-begins")


def _classify(tp: str, text: str, scoring: bool) -> str:
    """Normalised event kind.

    ESPN's soccer type strings are fine-grained ('goal---header', 'goal---volley',
    'penalty---scored', 'goal---own'), so goals are taken from `scoringPlay` rather than
    from a hard-coded list of type names.
    """
    t, x = (tp or "").lower(), (text or "").lower()
    if "own" in t or x.startswith("own goal"):
        return "own_goal"
    if scoring:
        return "penalty_goal" if ("penalt" in t or "penalt" in x) else "goal"
    if "red" in t:
        return "red_card"
    if "yellow" in t:
        return "yellow_card"
    if "substitution" in t:
        return "substitution"
    if any(k in t for k in PERIOD_HINTS):
        return "period"
    if "penalt" in t:
        return "penalty_missed" if any(k in t for k in ("miss", "saved", "post", "bar")) \
            else "penalty_other"
    if "var" in t or "review" in t:
        return "var"
    if t.startswith("goal"):
        return "goal_other"          # disallowed / cancelled / shot-on-goal style entries
    return t or "other"


def _sim(x: str, y: str) -> float:
    import difflib
    if not x or not y:
        return 0.0
    if x in y or y in x:
        return 0.95
    return difflib.SequenceMatcher(None, x, y).ratio()


def _score_from_text(text: str, hn: str, an: str) -> tuple[int, int] | None:
    """Parse '... Burnley 0, Brighton and Hove Albion 1.' -> (home, away)."""
    if not text:
        return None
    h, a = _norm(hn), _norm(an)
    for sent in SENT_RE.split(text):
        m = SCORE_RE.match(sent)
        if not m:
            continue
        n1, s1, n2, s2 = _norm(m.group(1)), int(m.group(2)), _norm(m.group(3)), int(m.group(4))
        fwd = _sim(n1, h) + _sim(n2, a)
        rev = _sim(n1, a) + _sim(n2, h)
        if fwd >= rev:
            if min(_sim(n1, h), _sim(n2, a)) < 0.5:
                continue
            return s1, s2
        if min(_sim(n1, a), _sim(n2, h)) < 0.5:
            continue
        return s2, s1
    return None


def build_events(verbose: bool = True) -> pd.DataFrame:
    g = pd.read_parquet(GAMES)
    rows, kinds, noscore = [], Counter(), 0
    for r in g.itertuples():
        f = _sum_path(r.espn_path, r.espn_id)
        if not f.exists():
            continue
        try:
            with gzip.open(f, "rt", encoding="utf-8") as fh:
                js = json.load(fh)
        except (OSError, ValueError):
            continue
        ke = js.get("keyEvents") or []
        hdr = ((js.get("header") or {}).get("competitions") or [{}])[0]
        hc = {c.get("homeAway"): c for c in (hdr.get("competitors") or [])}
        hn = ((hc.get("home") or {}).get("team") or {}).get("displayName") or r.home_name
        an = ((hc.get("away") or {}).get("team") or {}).get("displayName") or r.away_name
        hid = str(((hc.get("home") or {}).get("team") or {}).get("id") or r.home_id)
        aid = str(((hc.get("away") or {}).get("team") or {}).get("id") or r.away_id)

        sh = sa = 0          # running score carried forward when the text has none
        prev = None
        for k, e in enumerate(ke):
            tp = (e.get("type") or {}).get("type") or ""
            txt = e.get("text") or ""
            kind = _classify(tp, txt, bool(e.get("scoringPlay")))
            kinds[kind] += 1
            tid = str((e.get("team") or {}).get("id") or "")
            side = "home" if tid and tid == hid else ("away" if tid and tid == aid else "")
            parsed = _score_from_text(txt, hn, an)
            shoot = bool(e.get("shootout"))
            psh, psa = sh, sa
            if parsed and not shoot:
                sh, sa = parsed
            elif kind in ("goal", "penalty_goal", "own_goal") and not shoot:
                noscore += 1
                # ESPN tags an own goal with the team that BENEFITS from it, exactly like a
                # normal goal (verified on 321 own goals whose text also carries the score:
                # 98.4% agree with the side that gained, 0% with the side that conceded).
                if side == "home":
                    sh += 1
                elif side == "away":
                    sa += 1
            scoring_side = "home" if sh > psh else ("away" if sa > psa else "")
            wc = _ts(e.get("wallclock"))
            rows.append({
                "event_slug": r.event_slug, "espn_path": r.espn_path, "espn_id": r.espn_id,
                "idx": k, "wallclock": wc,
                "period": int(((e.get("period") or {}).get("number") or 0)),
                "clock_s": float(((e.get("clock") or {}).get("value") or np.nan)),
                "clock_disp": ((e.get("clock") or {}).get("displayValue") or ""),
                "type_id": str((e.get("type") or {}).get("id") or ""),
                "type_raw": tp, "type_text": (e.get("type") or {}).get("text") or "",
                "kind": kind, "scoring": bool(e.get("scoringPlay")), "shootout": shoot,
                "team_side": side, "team_name": (e.get("team") or {}).get("displayName") or "",
                "scoring_side": scoring_side,
                "score_home": sh, "score_away": sa, "lead": sh - sa,
                "score_parsed": parsed is not None,
                "text": txt[:300],
                "dt_prev": np.nan if prev is None or not np.isfinite(wc) else wc - prev,
            })
            if np.isfinite(wc):
                prev = wc
    ev = pd.DataFrame(rows)
    ev["minute"] = (ev.clock_s / 60.0).astype("float32")   # ESPN clock is elapsed seconds
    for c in ("period", "score_home", "score_away", "lead", "idx"):
        ev[c] = ev[c].astype("int16")
    ev.to_parquet(EVENTS, index=False)
    _augment_games(g, ev)
    if verbose:
        print(f"events.parquet: {len(ev):,} rows over {ev.event_slug.nunique():,} games")
        print("  kinds:", dict(kinds.most_common(14)))
        print(f"  goals whose score had to be counted rather than parsed: {noscore:,}")
    return ev


# -------------------------------------------------------------------- 5. state x price panel

def _leg_prices(mcodes: np.ndarray) -> dict[int, tuple[np.ndarray, np.ndarray]]:
    """market code -> (sorted ts, YES price).  YES = q for side 0, 1-q for side 1."""
    codes = np.unique(mcodes[~pd.isna(mcodes)]).astype(np.int32)
    f = C.fills(markets=codes, columns=["m", "ts", "s", "q"])
    f = f.sort_values(["m", "ts"], kind="stable")
    p = np.where(f.s.to_numpy() == 0, f.q.to_numpy(), 1.0 - f.q.to_numpy())
    out = {}
    ts = f.ts.to_numpy()
    m = f.m.to_numpy()
    bounds = np.flatnonzero(np.diff(m)) + 1
    for lo, hi in zip(np.r_[0, bounds], np.r_[bounds, len(m)]):
        out[int(m[lo])] = (ts[lo:hi], p[lo:hi].astype(np.float32))
    return out


def _extra_prices(cids) -> dict[str, tuple[np.ndarray, np.ndarray]]:
    """condition_id -> (ts, YES price) for legs backfilled from the Data API."""
    out = {}
    for cid in cids:
        f = EXTRA / f"{cid}.parquet"
        if not f.exists():
            continue
        d = pd.read_parquet(f)
        if len(d):
            out[cid] = (d.ts.to_numpy(), d.p_yes.to_numpy(np.float32))
    return out


def regulation_scores(ev: pd.DataFrame) -> pd.DataFrame:
    """Per game: the score at the end of 90 minutes, and at the end of extra time.

    Polymarket's 3-way soccer moneylines resolve on REGULATION time - a cup tie won in
    extra time or on penalties pays the draw leg - while ESPN's header score includes
    extra time and the shootout.  Comparing the wrong one makes ~1% of games look like
    mismatches, so both are kept.
    """
    s = ev.sort_values(["event_slug", "idx"])
    reg = s[(s.period <= 2) & ~s.shootout].groupby("event_slug")[["score_home", "score_away"]].last()
    ft = s[~s.shootout].groupby("event_slug")[["score_home", "score_away"]].last()
    out = reg.rename(columns={"score_home": "reg_home", "score_away": "reg_away"}).join(
        ft.rename(columns={"score_home": "ft_home", "score_away": "ft_away"}), how="outer")
    out["had_et"] = s[~s.shootout].groupby("event_slug").period.max() >= 3
    out["had_shootout"] = s.groupby("event_slug").shootout.any()
    out["reg_result"] = np.where(out.reg_home > out.reg_away, "home",
                                 np.where(out.reg_home < out.reg_away, "away", "draw"))
    return out.reset_index()


def _augment_games(g: pd.DataFrame, ev: pd.DataFrame) -> pd.DataFrame:
    """Write the regulation score and the feed-quality flags back into games.parquet."""
    g = g.drop(columns=[c for c in ("reg_home", "reg_away", "ft_home", "ft_away", "had_et",
                                    "had_shootout", "reg_result", "reg_agrees",
                                    "game_n_events", "game_n_goals", "feed_consistent")
                        if c in g.columns])
    g = g.merge(regulation_scores(ev), on="event_slug", how="left")
    g["reg_agrees"] = g.reg_result == g.pm_result
    qual = ev.groupby("event_slug").agg(
        game_n_events=("idx", "size"),
        game_n_goals=("kind", lambda c: int(c.isin(["goal", "penalty_goal", "own_goal"]).sum())))
    g = g.merge(qual, on="event_slug", how="left")
    # internal consistency, free of any outcome information: does the number of goal events
    # equal the goals in the regulation + extra-time score?
    g["feed_consistent"] = (g.game_n_goals.fillna(-1) == (g.ft_home + g.ft_away)) & \
                           (g.game_n_events >= 5)
    g.to_parquet(GAMES, index=False)
    return g


def build_panel(verbose: bool = True) -> pd.DataFrame:
    g = pd.read_parquet(GAMES)
    ev = pd.read_parquet(EVENTS)
    if "reg_result" not in g.columns:
        g = _augment_games(g, ev)
    ev = ev[np.isfinite(ev.wallclock)].copy()

    keep = ["event_slug", "league", "espn_path", "espn_id", "date", "game_start_ts",
            "closed_ts", "fee_rate", "home_name", "away_name", "abbr_home", "abbr_away",
            "m_home", "m_draw", "m_away", "cid_home", "cid_draw", "cid_away",
            "y_home", "y_draw", "y_away",
            "ask_home", "ask_draw", "ask_away", "mid_home", "mid_draw", "mid_away",
            "home_score", "away_score", "espn_result", "pm_result", "result_agrees",
            "reg_home", "reg_away", "ft_home", "ft_away", "had_et", "had_shootout",
            "reg_result", "reg_agrees",
            "pre_usd", "pre_n_fills", "name_score", "min_team_score", "dt_start",
            "neutral", "n_legs", "game_n_events", "game_n_goals", "feed_consistent"]
    pan = ev.merge(g[keep], on=["event_slug", "espn_path", "espn_id"], how="inner")

    all_codes = pd.concat([g.m_home, g.m_draw, g.m_away]).dropna().to_numpy()
    need_cid = set()
    for leg in LEGS:
        need_cid |= set(g.loc[g[f"m_{leg}"].isna(), f"cid_{leg}"].dropna())
    series = _leg_prices(all_codes)
    extra = _extra_prices(need_cid)
    if verbose:
        print(f"panel: {len(series):,} legs from fills.parquet + {len(extra):,} backfilled "
              f"from the Data API")

    n = len(pan)
    wc = pan.wallclock.to_numpy()
    cols: dict[str, np.ndarray] = {}
    for leg in LEGS:
        keys = [int(m) if pd.notna(m) else (c if isinstance(c, str) else None)
                for m, c in zip(pan[f"m_{leg}"], pan[f"cid_{leg}"])]
        by_key: dict = {}
        for i, k in enumerate(keys):
            if k is not None:
                by_key.setdefault(k, []).append(i)
        pre = np.full(n, np.nan, np.float32)
        age = np.full(n, np.nan, np.float32)
        n5 = np.zeros(n, np.int32)
        src = np.zeros(n, np.int8)            # 0 none, 1 fills.parquet, 2 Data-API backfill
        hz = {h: (np.full(n, np.nan, np.float32), np.full(n, np.nan, np.float32))
              for h in HORIZONS}
        mz = {h: np.full(n, np.nan, np.float32) for h in PRE_OFFSETS}
        for k, idx in by_key.items():
            if isinstance(k, int):
                sp, tag = series.get(k), 1
            else:
                sp, tag = extra.get(k), 2
            if sp is None:
                continue
            ts, px = sp
            rows = np.asarray(idx)
            t = wc[rows]
            src[rows] = tag
            kk = np.searchsorted(ts, t, side="right") - 1
            ok = kk >= 0
            pre[rows[ok]] = px[kk[ok]]
            age[rows[ok]] = (t[ok] - ts[kk[ok]]).astype(np.float32)
            k5 = np.searchsorted(ts, t - 300.0, side="left")
            n5[rows] = (np.searchsorted(ts, t, side="right") - k5).astype(np.int32)
            for h in HORIZONS:
                ka = np.searchsorted(ts, t + h, side="left")
                ok2 = ka < len(ts)
                pa, la = hz[h]
                pa[rows[ok2]] = px[ka[ok2]]
                la[rows[ok2]] = (ts[ka[ok2]] - t[ok2]).astype(np.float32)
            for h in PRE_OFFSETS:
                kb = np.searchsorted(ts, t - h, side="right") - 1
                ok3 = kb >= 0
                mz[h][rows[ok3]] = px[kb[ok3]]
        cols[f"p_{leg}_pre"] = pre
        cols[f"age_{leg}_pre"] = age
        cols[f"n_{leg}_5m"] = n5
        cols[f"src_{leg}"] = src
        for h in HORIZONS:
            cols[f"p_{leg}_h{h}"], cols[f"lag_{leg}_h{h}"] = hz[h]
        for h in PRE_OFFSETS:
            cols[f"p_{leg}_m{h}"] = mz[h]
    pan = pd.concat([pan.reset_index(drop=True), pd.DataFrame(cols)], axis=1)
    pan["in_window"] = (pan.wallclock >= pan.game_start_ts - 7200) & \
                       (pan.wallclock <= pan.closed_ts.fillna(np.inf) + 3600)
    pan["book_sum_pre"] = pan[[f"p_{l}_pre" for l in LEGS]].sum(axis=1, min_count=3)
    pan.to_parquet(PANEL, index=False)
    if verbose:
        print(f"panel.parquet: {len(pan):,} rows x {pan.shape[1]} cols -> {PANEL}")
    return pan


# ------------------------------------------------------------------------------- loaders

def games(columns=None) -> pd.DataFrame:
    """One row per matched game (see module docstring)."""
    return pd.read_parquet(GAMES, columns=columns)


def events(columns=None) -> pd.DataFrame:
    """One row per ESPN key event."""
    return pd.read_parquet(EVENTS, columns=columns)


def panel(columns=None, clean: bool = True) -> pd.DataFrame:
    """The state x price panel.

    `clean=True` keeps only rows from games whose ESPN feed is internally consistent
    (`feed_consistent`: the number of goal events equals the goals in the final score, and
    the feed has at least 5 events) and whose wallclock falls inside the trading window.
    That filter uses no outcome information, so it is safe to apply before a backtest.
    """
    d = pd.read_parquet(PANEL, columns=columns)
    if clean:
        d = d[d.feed_consistent & d.in_window]
    return d


# ----------------------------------------------------------------------------- 6. validate

def validate(verbose: bool = True) -> dict:
    og = our_games()
    g = pd.read_parquet(GAMES)
    ev = pd.read_parquet(EVENTS)
    pan = pd.read_parquet(PANEL)
    rep: dict = {}

    print("=" * 78)
    print("COVERAGE")
    print("=" * 78)
    mapped = og[og.espn_path.notna()]
    rep["our_games"] = len(og)
    rep["games_with_espn_path"] = len(mapped)
    rep["matched"] = len(g)
    rep["with_all_three_legs"] = int((g.n_legs >= 3).sum())
    print(f"soccer games in C.markets()            {len(og):,}")
    print(f"  ... in a league mapped to ESPN       {len(mapped):,} "
          f"({len(mapped)/len(og):.1%})")
    print(f"  ... matched to an ESPN game          {len(g):,} ({len(g)/len(og):.1%})")
    print(f"  ... with all three moneyline legs    {int((g.n_legs>=3).sum()):,}")
    miss = og[og.espn_path.isna()].groupby("league").size().sort_values(ascending=False)
    print(f"\nunmapped leagues ({int(miss.sum()):,} games): "
          f"{dict(miss.head(12))}")
    lost = set(mapped.event_slug) - set(g.event_slug)
    if lost:
        ll = mapped[mapped.event_slug.isin(lost)].groupby("league").size().sort_values(ascending=False)
        print(f"mapped but unmatched ({len(lost):,} games): {dict(ll.head(12))}")

    print("\nper-league match rate (top 25 by games):")
    per = mapped.groupby("league").agg(games=("event_slug", "size")).join(
        g.groupby("league").agg(matched=("event_slug", "size"))).fillna(0)
    per["rate"] = per.matched / per.games
    per = per.sort_values("games", ascending=False).head(25)
    for lg, r in per.iterrows():
        print(f"  {lg:32s} {int(r.games):5d} -> {int(r.matched):5d}  {r.rate:6.1%}")

    print("\n" + "=" * 78)
    print("MATCH QUALITY")
    print("=" * 78)
    dec = g[(g.pm_result != "") & g.reg_result.notna()]
    rep["result_agreement_ft"] = float(dec.result_agrees.mean())
    rep["result_agreement_regulation"] = float(dec.reg_agrees.mean())
    print(f"our payouts == ESPN full-time score       {dec.result_agrees.mean():.3%}")
    print(f"our payouts == score at 90 min            {dec.reg_agrees.mean():.3%}   "
          f"(of {len(dec):,} decided games)")
    print("  -> Polymarket resolves soccer moneylines on REGULATION time: a cup tie won in "
          "extra\n     time or on penalties pays the DRAW leg.  Use reg_result, not "
          "espn_result.")
    et = g[g.had_et.fillna(False) | g.had_shootout.fillna(False)]
    print(f"  games that went to extra time / penalties: {len(et):,} "
          f"({len(et)/max(len(g),1):.1%}); of those, payouts match the 90-min score "
          f"{et.reg_agrees.mean():.1%} of the time")
    bad = dec[~dec.reg_agrees]
    if len(bad):
        print(f"  {len(bad):,} games still disagree at 90 min (likely bad matches or ESPN "
              f"gaps), e.g.:")
        for r in bad.head(6).itertuples():
            print(f"    {r.event_slug:34s} name={r.name_score:.2f} espn {r.home_name} "
                  f"{int(r.reg_home)}-{int(r.reg_away)} {r.away_name} | pm {r.pm_result}")
    print(f"|scheduled kickoff - ESPN kickoff|: median {g.dt_start.abs().median():.0f}s, "
          f"p90 {g.dt_start.abs().quantile(.9):.0f}s, >15min {int((g.dt_start.abs()>900).sum()):,}")
    print(f"slug order home-first: {g.slug_home_first.mean():.2%}")

    print("\n" + "=" * 78)
    print("EVENT TABLE")
    print("=" * 78)
    print(f"{len(ev):,} key events over {ev.event_slug.nunique():,} games "
          f"({len(ev)/max(ev.event_slug.nunique(),1):.1f} per game)")
    print("kinds:", dict(ev.kind.value_counts().head(12)))
    # score monotonicity
    s = ev.sort_values(["event_slug", "idx"])
    d = s.groupby("event_slug")[["score_home", "score_away"]].diff()
    nonmono = int(((d < 0).any(axis=1)).sum())
    rep["score_non_monotonic_rows"] = nonmono
    print(f"score monotonic within game: {len(ev)-nonmono:,}/{len(ev):,} rows "
          f"({nonmono:,} decreases)")
    # clock ordering
    dc = s.groupby("event_slug")[["period", "clock_s"]].diff()
    badclock = int(((dc.period == 0) & (dc.clock_s < -1)).sum())
    print(f"clock non-decreasing within period: {badclock:,} violations")
    dw = s.groupby("event_slug")["wallclock"].diff()
    print(f"wallclock non-decreasing: {int((dw < 0).sum()):,} violations")
    # running score from keyEvents vs the scoreboard's own final score
    rs = regulation_scores(ev).set_index("event_slug")
    gg = g.set_index("event_slug")
    j = rs.join(gg[["home_score", "away_score", "had_et"]], rsuffix="_hdr").dropna(
        subset=["home_score", "away_score"])
    plain = j[~j.had_et.fillna(False)]
    ok = (plain.ft_home == plain.home_score) & (plain.ft_away == plain.away_score)
    rep["final_score_agreement"] = float(ok.mean())
    print(f"running score from keyEvents == scoreboard final score: {ok.mean():.2%} of "
          f"{len(plain):,} games without extra time")
    print(f"goals parsed out of the event text rather than counted: "
          f"{ev.loc[ev.kind.isin(['goal','penalty_goal','own_goal']),'score_parsed'].mean():.2%}")

    print("\n" + "=" * 78)
    print("PANEL / PRICES")
    print("=" * 78)
    print(f"{len(pan):,} rows, {pan.event_slug.nunique():,} games")
    print(f"wallclock inside [start-2h, close+1h]: {pan.in_window.mean():.3%}")
    for leg in LEGS:
        has = pan[f"p_{leg}_pre"].notna()
        fresh = has & (pan[f"age_{leg}_pre"] <= 300)
        print(f"  {leg:5s} pre-price present {has.mean():6.2%}   fresh(<=5min) {fresh.mean():6.2%}   "
              f"h3 present {pan[f'p_{leg}_h3'].notna().mean():6.2%}")
    all3 = pan[[f"p_{l}_pre" for l in LEGS]].notna().all(axis=1)
    rep["rows_with_all_three_pre_prices"] = int(all3.sum())
    print(f"all three legs priced before the event: {all3.mean():.2%} ({int(all3.sum()):,} rows)")
    bs = pan.loc[all3 & pan.in_window, "book_sum_pre"]
    print(f"sum of the three YES prices (should be ~1): median {bs.median():.3f}, "
          f"p10 {bs.quantile(.1):.3f}, p90 {bs.quantile(.9):.3f}")

    print("\nGOAL LATENCY LADDER - cumulative median move of the scoring team's leg,"
          "\nmeasured against its price 60 s before ESPN's wallclock:")
    gl0 = pan[pan.kind.isin(["goal", "penalty_goal", "own_goal"]) & pan.in_window &
              pan.scoring_side.isin(["home", "away"])]
    ladder = ["m60", "m30", "m10", "pre", "h3", "h10", "h30", "h60", "h300"]
    sel = np.where(gl0.scoring_side.to_numpy() == "home", 1, 0)
    cols_l = {c: np.where(sel == 1, gl0[f"p_home_{c}"], gl0[f"p_away_{c}"]) for c in ladder}
    okl = np.all([~pd.isna(cols_l[c]) for c in ladder], axis=0)
    base = cols_l["m60"][okl]
    for c in ladder:
        d = cols_l[c][okl] - base
        tag = {"pre": "  <- last print AT OR BEFORE the wallclock",
               "h3": "  <- earliest execution-legal entry"}.get(c, "")
        print(f"  {c:5s} median {np.median(d):+.4f}  mean {d.mean():+.4f}{tag}")
    rep["goal_ladder_rows"] = int(okl.sum())
    print(f"  (n={int(okl.sum()):,} goals with the whole ladder priced)")
    print("  READ THIS: the market is already ~17% of the way through the move by the last "
          "print\n  before ESPN's wallclock, and ~77% of it by +3 s.  ESPN's wallclock is "
          "when its\n  operator typed the event, NOT when the ball crossed the line - do "
          "not treat\n  p_*_pre as an uninformed pre-goal price.")

    print("\nprice reaction to a goal (median change in the scoring team's leg, "
          "pre -> +60s, rows with both prices):")
    gl = pan[pan.kind.isin(["goal", "penalty_goal", "own_goal"]) & pan.in_window &
             pan.scoring_side.isin(["home", "away"])]
    for side in ("home", "away"):
        sub = gl[gl.scoring_side == side]
        a = sub[f"p_{side}_pre"]
        b = sub[f"p_{side}_h60"]
        m = a.notna() & b.notna()
        print(f"  {side} goal -> {side} leg: n={int(m.sum()):,}  median {(b-a)[m].median():+.3f}  "
              f"mean {(b-a)[m].mean():+.3f}  share up {((b-a)[m]>0).mean():.1%}")
    rc = pan[(pan.kind == "red_card") & pan.in_window & pan.team_side.isin(["home", "away"])]
    for side in ("home", "away"):
        sub = rc[rc.team_side == side]
        a, b = sub[f"p_{side}_pre"], sub[f"p_{side}_h60"]
        m = a.notna() & b.notna()
        if m.sum():
            print(f"  {side} red card -> {side} leg: n={int(m.sum()):,}  median {(b-a)[m].median():+.3f}")

    print("  draw leg on any goal at 0-0 -> 1-0: "
          f"{(gl.loc[(gl.lead.abs()==1) & (gl.score_home+gl.score_away==1), 'p_draw_h60'] - gl.loc[(gl.lead.abs()==1) & (gl.score_home+gl.score_away==1), 'p_draw_pre']).median():+.3f}")
    print(f"\nfeed_consistent games: {pan.groupby('event_slug').feed_consistent.first().mean():.2%}"
          f"   rows kept by panel(clean=True): "
          f"{(pan.feed_consistent & pan.in_window).mean():.2%}")

    print("\nspot checks (3 games with the biggest goal-driven move):")
    cand = gl[gl.p_home_pre.notna() & gl.p_home_h60.notna()].copy()
    cand["mv"] = (cand.p_home_h60 - cand.p_home_pre).abs()
    for r in cand.nlargest(3, "mv").itertuples():
        print(f"  {r.event_slug} {r.home_name} v {r.away_name} | {r.clock_disp} "
              f"{r.team_side} goal -> {int(r.score_home)}-{int(r.score_away)} | "
              f"home leg {r.p_home_pre:.3f} -> {r.p_home_h60:.3f}, "
              f"draw {r.p_draw_pre if np.isfinite(r.p_draw_pre) else float('nan'):.3f} -> "
              f"{r.p_draw_h60 if np.isfinite(r.p_draw_h60) else float('nan'):.3f} | final "
              f"{int(r.home_score)}-{int(r.away_score)}")

    dev = pan[pan.wallclock < pd.Timestamp("2026-07-01", tz="UTC").timestamp()]
    hold = pan[pan.wallclock >= pd.Timestamp("2026-07-01", tz="UTC").timestamp()]
    print(f"\ndev window (< 2026-07-01): {len(dev):,} rows / {dev.event_slug.nunique():,} games")
    print(f"holdout (>= 2026-07-01):   {len(hold):,} rows / {hold.event_slug.nunique():,} games")
    rep["dev_games"] = int(dev.event_slug.nunique())
    rep["holdout_games"] = int(hold.event_slug.nunique())
    return rep


# ---------------------------------------------------------------------------------- driver

def build() -> None:
    fetch_scoreboards()
    match()
    backfill_legs()
    fetch_summaries()
    build_events()
    build_panel()
    validate()


if __name__ == "__main__":
    cmd = sys.argv[1] if sys.argv[1:] else "build"
    {"build": build, "scoreboards": fetch_scoreboards, "espn-games": build_espn_games,
     "match": match, "backfill": backfill_legs, "summaries": fetch_summaries,
     "events": build_events,
     "panel": build_panel, "validate": validate}[cmd]()
