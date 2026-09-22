"""Football (NFL + CFB) in-game state joined to Polymarket prices.

Source: ESPN's public site API (no key). Everything fetched is cached gzipped under
`data/events/nfl/raw/` so re-runs are free and reproducible.

  scoreboard  https://site.api.espn.com/apis/site/v2/sports/<path>/scoreboard?dates=YYYYMMDD
  summary     https://site.api.espn.com/apis/site/v2/sports/<path>/summary?event=<id>

Pipeline (`python -m pmsports.events.nfl all`):

  1. fetch_scoreboards()  every date our football markets touch (+/- 1 day), both leagues
  2. build_espn_games()   -> data/events/nfl/espn_games.parquet   one row per ESPN game
  3. build_games()        -> data/events/nfl/games.parquet        ESPN game <-> our moneyline
                             market (condition_id, market code `m`, which outcome is HOME),
                             plus data/events/nfl/markets.parquet mapping EVERY football
                             market in C.universe() (spreads/totals/props) to its ESPN game
                             through the shared event_slug.
  4. fetch_summaries() / build_plays() -> data/events/nfl/plays.parquet
                             drives.previous[].plays[] flattened: wallclock (UTC s), period,
                             clock remaining in period and in regulation, offense/defense,
                             down, distance, yards to end zone, score AFTER the play,
                             scoring flag, play type/text, timeouts left (parsed best-effort).
  5. build_panel()        -> data/events/nfl/panel.parquet
                             one row per play boundary: post-play state + the price actually
                             traded for the HOME side in the window after the play, an
                             executable next print on each side (decision time + 3 s), the
                             pregame price and the final result.

Conventions that matter (see pmsports/research/RESEARCH_GUIDE.md):
  * Prices come from real taker fills only. A 1-minute bar can sit frozen for a whole
    quarter and fabricate an edge nobody could trade, so there is no bar fallback.
  * `p_home` is the state price (median fill in the window AFTER the play). It is NOT
    tradable - you cannot trade at a print that already happened. Use `ex_home_p` /
    `ex_away_p`, the first print on that side at least ENTRY_LAG_S after the play.
  * Every fill in `C.fills()` is the taker side. A taker who acquired the away side at q
    implies a home price of 1-q, which is a home BID; a taker who acquired home at q is a
    home ASK. Both are kept separately so a strategy can pay the real spread.

panel.parquet columns
  identity   espn_id, play_idx, drive_idx, play_id, m (fills market code), condition_id,
             lg (nfl|cfb), league, season, season_type, home_abbr, away_abbr, neutral
  time       wallclock (UTC s, ESPN's stamp for the play), next_wc, espn_date (kickoff),
             game_start_ts (Polymarket's SCHEDULED start - wrong by hours for some 2025
             markets, prefer espn_date), closed_ts, since_kick_s, in_play, dev
             (True = before the 2026-07-01 holdout boundary)
  state      period, clock_s (left in the period), reg_s (left in regulation), ot,
             elapsed_s, down/distance/yard_line/yards_to_ez (as the play STARTED),
             end_down/end_yards_to_ez, up_down/up_ytez/up_off_home (the situation facing
             the next snap, filled forward through timeouts and period breaks),
             off_home/drive_home/end_home (1 home, 0 away, -1 unknown),
             home_score/away_score (AFTER the play, running max - see build_panel),
             home_score_raw/away_score_raw, margin, d_margin, home_to/away_to (timeouts
             left this half, parsed from the play text; -1 when unparsed),
             scoring, turnover, penalty, yards, type, text, is_break, is_final
  price      p_home (median traded home price in the post-play window), p_home_ask /
             p_home_bid (the two sides of that window), n_fills / n_ask / n_bid, usd
             (taker notional in the window = capacity), spread, has_price
  execution  ex_home_p / ex_home_ts / ex_home_dt and ex_away_p / ex_away_ts / ex_away_dt:
             the first print on that side at >= wallclock + ENTRY_LAG_S (raw on-chain
             timestamps). `ex_*_dt` is how long you waited - a print 200 s later has seen
             more football than your signal did, so BOUND IT in any rule.
  outcome    home_won, final_home, final_away, pre_mid_home, pre_ask_home, pre_ask_away,
             pre_usd, pre_n_fills, volume (TOTAL incl. in-play - never select on it)
  quality    game_ok (ESPN's feed for this game is structurally sound), q_score_back,
             q_period_back, q_clock_fwd, q_wc_back, q_final_ok, q_plays

Coverage actually achieved (see `validate()` / `print_validation()`):
  1,113 / 1,113 fills-bearing football moneyline markets matched to an ESPN game, 0 payout
  mismatches (all 6 markets that resolved 0.5/0.5 are exactly tied on ESPN's scoreboard).
  190,808 play rows over 1,102 games; 51% carry a traded price (NFL 73%, CFB 39%).
  Holdout (>= 2026-07-01, the 5% fee regime) is only 133 games - thin.
"""
from __future__ import annotations

import gzip
import json
import logging
import re
import sys
import unicodedata
from datetime import datetime, timedelta, timezone
from pathlib import Path

import numpy as np
import pandas as pd

from ..http import HOST_RPS, get_json

log = logging.getLogger("pmsports")

HOST_RPS.setdefault("site.api.espn.com", 4.0)   # be polite: public, unauthenticated

ROOT = Path(__file__).resolve().parents[2]
DIR = ROOT / "data" / "events" / "nfl"
RAW = DIR / "raw"

SITE = "https://site.api.espn.com/apis/site/v2/sports"
LEAGUES = {"nfl": "football/nfl", "cfb": "football/college-football"}

# ---- panel tuning -----------------------------------------------------------------
TRADE_LAG_S = 2.6     # on-chain fill timestamps are settlement, ~2.6 s after the match
PRICE_LO_S = 5.0      # state price = median home-side fill in [wc + LO, ...]
PRICE_HI_S = 90.0     # ... capped at wc + HI and at the next play's wallclock
LEAK_GUARD_S = 8.0    # ... minus this: ESPN stamps a play when the scorer ENTERS it, so
                      # the seconds before the next stamp are the next play happening
ENTRY_LAG_S = 3.0     # protocol: decide, then enter at the next print >= +3 s
ENTRY_MAX_S = 300.0   # give up looking for an executable print after this long
MIN_FILLS = 1         # a single real print IS a traded price; n_fills is kept so a
                      # hypothesis can demand more. There is no bar fallback.
PERIOD_S = 900        # a quarter (both codes)


# ------------------------------------------------------------------- caching / fetching

def _write(df: pd.DataFrame, path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".tmp")
    df.to_parquet(tmp, index=False)
    tmp.replace(path)


def _cached(path: Path, url: str, params: dict, force: bool = False):
    """GET `url` once; the raw JSON body is kept gzipped at `path` forever."""
    if path.exists() and not force:
        try:
            with gzip.open(path, "rt") as fh:
                return json.load(fh)
        except (OSError, EOFError, json.JSONDecodeError):
            path.unlink(missing_ok=True)
    j = get_json(url, params)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".tmp")
    with gzip.open(tmp, "wt") as fh:
        json.dump(j, fh)
    tmp.replace(path)
    return j


def scoreboard(league: str, day: str, force: bool = False) -> dict:
    """ESPN scoreboard for one YYYYMMDD (all games that calendar day, US/Eastern)."""
    return _cached(RAW / "scoreboard" / league / f"{day}.json.gz",
                   f"{SITE}/{LEAGUES[league]}/scoreboard",
                   {"dates": day, "limit": 400}, force)


def summary(league: str, event_id: str, force: bool = False) -> dict:
    """ESPN game summary (drives, header, scoring plays)."""
    return _cached(RAW / "summary" / league / f"{event_id}.json.gz",
                   f"{SITE}/{LEAGUES[league]}/summary",
                   {"event": str(event_id)}, force)


def _parse_ts(s: str | None) -> float | None:
    if not s:
        return None
    s = s.strip().replace(" ", "T")
    if s.endswith("Z"):
        s = s[:-1] + "+00:00"
    try:
        dt = datetime.fromisoformat(s)
    except ValueError:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.timestamp()


# ------------------------------------------------------------------------ our markets

def football_markets() -> pd.DataFrame:
    """The 1,113 football moneyline markets that have local wallet-attributed fills."""
    from ..research import common as C
    mk = C.markets()
    return mk[mk.family == "american_football"].copy()


def universe_moneylines() -> pd.DataFrame:
    """Every football EVENT in C.universe(), shaped like `markets()` so the same matcher
    runs on it. Most of these have no local fills - their value is the event_slug ->
    ESPN game mapping that every spreads / totals / prop market on that event inherits."""
    from ..research import common as C
    u = C.universe(columns=["condition_id", "outcome", "outcome_idx", "payout", "family",
                            "league", "market_type", "event_slug", "market_slug",
                            "game_start_ts", "closed_ts", "volume", "fee_rate"])
    u = u[(u.family == "american_football") & (u.market_type == "moneyline")]
    meta = u.drop_duplicates("condition_id").set_index("condition_id")
    outs = u.pivot_table(index="condition_id", columns="outcome_idx", values="outcome",
                         aggfunc="first")
    pays = u.pivot_table(index="condition_id", columns="outcome_idx", values="payout",
                         aggfunc="first")
    mk = meta[["family", "league", "market_type", "event_slug", "market_slug", "game_start_ts",
               "closed_ts", "fee_rate", "volume"]].copy()
    mk["o0"], mk["o1"] = outs.get(0), outs.get(1)
    mk["y0"], mk["y1"] = pays.get(0), pays.get(1)
    for c in ("pre_mid0", "pre_ask0", "pre_ask1", "pre_n_fills", "pre_usd"):
        mk[c] = np.nan
    mk["m"] = np.int32(-1)
    return mk.reset_index()


def _league_of(slug: str, league: str) -> str:
    s = str(league or "")
    if s.startswith("cfb") or str(slug).startswith("cfb"):
        return "cfb"
    return "nfl"


def market_dates(mk: pd.DataFrame) -> dict[str, set[str]]:
    """ESPN scoreboard dates to fetch per league: the kickoff day and the day before
    (ESPN buckets by US/Eastern, our `game_start_ts` is UTC, so a 00:15Z Monday
    kickoff is a Sunday game on the scoreboard)."""
    out: dict[str, set[str]] = {k: set() for k in LEAGUES}
    t = pd.to_datetime(mk.game_start_ts, unit="s")
    for slug, lg, ts in zip(mk.event_slug, mk.league, t):
        if pd.isna(ts):
            continue
        k = _league_of(slug, lg)
        for off in (-1, 0, 1):
            out[k].add((ts + timedelta(days=off)).strftime("%Y%m%d"))
    return out


def fetch_scoreboards(mk: pd.DataFrame | None = None, force: bool = False) -> None:
    """Every scoreboard day our football markets touch - both the fills-bearing moneylines
    and the wider universe (2024 games and sub-$50k games carry spreads/totals)."""
    mk = pd.concat([football_markets(), universe_moneylines()]) if mk is None else mk
    for lg, days in market_dates(mk).items():
        for i, d in enumerate(sorted(days)):
            scoreboard(lg, d, force)
            if i % 25 == 0:
                log.info("scoreboard %s %s (%d/%d)", lg, d, i + 1, len(days))


# --------------------------------------------------------------------- ESPN game table

def _team_row(c: dict, side: str) -> dict:
    t = c.get("team") or {}
    score = c.get("score")
    try:
        score = int(score)
    except (TypeError, ValueError):
        score = None
    return {
        f"{side}_id": str(t.get("id") or ""),
        f"{side}_name": t.get("displayName"),
        f"{side}_short": t.get("shortDisplayName"),
        f"{side}_nick": t.get("name"),
        f"{side}_loc": t.get("location"),
        f"{side}_abbr": t.get("abbreviation"),
        f"{side}_score": score,
    }


def build_espn_games() -> pd.DataFrame:
    """One row per ESPN game found in the cached scoreboards."""
    rows = []
    for lg in LEAGUES:
        d = RAW / "scoreboard" / lg
        for f in sorted(d.glob("*.json.gz")):
            with gzip.open(f, "rt") as fh:
                j = json.load(fh)
            for e in j.get("events") or []:
                comps = e.get("competitions") or []
                if not comps:
                    continue
                c = comps[0]
                cs = c.get("competitors") or []
                home = next((x for x in cs if x.get("homeAway") == "home"), None)
                away = next((x for x in cs if x.get("homeAway") == "away"), None)
                if not (home and away):
                    continue
                st = (c.get("status") or {}).get("type") or {}
                r = {
                    "league": lg,
                    "espn_id": str(e["id"]),
                    "espn_date": _parse_ts(c.get("date") or e.get("date")),
                    "espn_name": e.get("shortName"),
                    "season": (e.get("season") or {}).get("year"),
                    "season_type": (e.get("season") or {}).get("type"),
                    "status": st.get("name"),
                    "completed": bool(st.get("completed")),
                    "neutral": bool(c.get("neutralSite")),
                    "pbp": bool(c.get("playByPlayAvailable", True)),
                }
                r.update(_team_row(home, "home"))
                r.update(_team_row(away, "away"))
                rows.append(r)
    g = pd.DataFrame(rows).drop_duplicates("espn_id", keep="last").reset_index(drop=True)
    _write(g, DIR / "espn_games.parquet")
    log.info("espn games %d (%s)", len(g), g.league.value_counts().to_dict())
    return g


# ----------------------------------------------------------------------------- matching

def _norm(s) -> str:
    """Lowercase alphanumerics with accents stripped: ESPN writes "San Jose State" with an
    acute accent, Polymarket does not."""
    s = unicodedata.normalize("NFKD", str(s or ""))
    return "".join(ch for ch in s.lower() if ch.isalnum() and not unicodedata.combining(ch))


# Polymarket spellings that no ESPN field contains.
ALIASES = {
    "uconn": "connecticut", "usc": "southerncalifornia", "utsa": "texassanantonio",
    "utep": "texasepaso", "smu": "southernmethodist", "tcu": "christian",
    "ucf": "centralflorida", "fau": "floridaatlantic", "fiu": "floridainternational",
    "byu": "brighamyoung", "lsu": "louisianastate", "olemiss": "mississippi",
    "pitt": "pittsburgh", "umass": "massachusetts", "unlv": "nevadalasvegas",
    "northcarolinastate": "ncstate", "appalachianstate": "appstate",
}


def _keys(r, side: str) -> set[str]:
    ks = set()
    for f in ("name", "short", "nick", "loc", "abbr"):
        v = _norm(getattr(r, f"{side}_{f}"))
        if v:
            ks.add(v)
            ks.add(ALIASES.get(v, v))
    return {k for k in ks if k}


def _score(outcome: str, keys: set[str]) -> int:
    o = _norm(outcome)
    o = ALIASES.get(o, o)
    if not o:
        return 0
    if o in keys:
        return 3
    if len(o) >= 4 and any(o in k or (len(k) >= 4 and k in o) for k in keys):
        return 2
    return 0


_SLUG = re.compile(r"^(nfl|cfb)-(.+?)-(\d{4}-\d{2}-\d{2})(-\d+)?$")


def _slug_abbrs(slug: str) -> tuple[str, str] | None:
    m = _SLUG.match(str(slug))
    if not m:
        return None
    parts = m.group(2).split("-")
    return (parts[0], parts[1]) if len(parts) == 2 else None


def match_markets(mk: pd.DataFrame, espn: pd.DataFrame, max_hours: float = 30.0
                  ) -> tuple[pd.DataFrame, pd.DataFrame]:
    """Match moneyline markets to ESPN games by date + team names.

    Returns (matched, unmatched). Each matched row carries `home_idx` (which of o0/o1 is
    the HOME team) and `payout_ok`, which cross-checks our resolution against ESPN's
    winner - the strongest available proof that the match is the right game.
    """
    espn = espn[espn.espn_date.notna()].copy()
    by_lg = {lg: g.sort_values("espn_date").reset_index(drop=True) for lg, g in espn.groupby("league")}
    arr = {lg: g.espn_date.to_numpy(float) for lg, g in by_lg.items()}

    out, unmatched = [], []
    for m in mk.itertuples(index=False):
        lg = _league_of(m.event_slug, m.league)
        g, ts = by_lg.get(lg), arr.get(lg)
        if g is None or not np.isfinite(m.game_start_ts):
            unmatched.append((m.condition_id, m.event_slug, "no candidates"))
            continue
        lo, hi = np.searchsorted(ts, [m.game_start_ts - max_hours * 3600,
                                      m.game_start_ts + max_hours * 3600])
        best, best_key = None, None
        sab = _slug_abbrs(m.event_slug)
        for i in range(lo, hi):
            r = g.iloc[i]
            hk, ak = _keys(r, "home"), _keys(r, "away")
            for home_idx, (oh, oa) in ((1, (m.o1, m.o0)), (0, (m.o0, m.o1))):
                sh, sa = _score(oh, hk), _score(oa, ak)
                if sh < 2 or sa < 2:
                    continue
                bonus = 0
                if sab:
                    aab, hab = (ALIASES.get(_norm(sab[0]), _norm(sab[0])),
                                ALIASES.get(_norm(sab[1]), _norm(sab[1])))
                    # slug is "<away>-<home>"; reward the orientation it agrees with
                    if _score(hab, hk) and _score(aab, ak):
                        bonus += 2
                key = (sh + sa + bonus, -abs(r.espn_date - m.game_start_ts))
                if best_key is None or key > best_key:
                    best_key, best = key, (r, home_idx, sh + sa, bonus)
        if best is None:
            unmatched.append((m.condition_id, m.event_slug, "no team match"))
            continue
        r, home_idx, sc, bonus = best
        y_home = m.y1 if home_idx == 1 else m.y0
        espn_home_won = (None if r.home_score is None or r.away_score is None
                         or r.home_score == r.away_score else r.home_score > r.away_score)
        payout_ok = (np.isnan(y_home) or y_home == 0.5 or espn_home_won is None
                     or bool(y_home == 1.0) == bool(espn_home_won))
        out.append({
            "m": m.m, "condition_id": m.condition_id, "event_slug": m.event_slug,
            "league": m.league, "lg": lg, "espn_id": r.espn_id, "espn_name": r["espn_name"],
            "espn_date": r.espn_date, "game_start_ts": m.game_start_ts,
            "closed_ts": m.closed_ts, "fee_rate": m.fee_rate,
            "season": r.season, "season_type": r.season_type, "neutral": r.neutral,
            "home_idx": home_idx, "home_team": m.o1 if home_idx == 1 else m.o0,
            "away_team": m.o0 if home_idx == 1 else m.o1,
            "espn_home": r.home_name, "espn_away": r.away_name,
            "home_id": r.home_id, "away_id": r.away_id,
            "home_abbr": r.home_abbr, "away_abbr": r.away_abbr,
            "final_home": r.home_score, "final_away": r.away_score,
            "home_won": (None if (not np.isfinite(y_home) or y_home == 0.5)
                         else y_home == 1.0),
            "void": bool(np.isfinite(y_home) and y_home == 0.5),
            "completed": r.completed, "match_score": sc, "slug_bonus": bonus,
            "payout_ok": payout_ok,
            "pre_mid_home": (m.pre_mid0 if home_idx == 0 else 1 - m.pre_mid0),
            "pre_ask_home": (m.pre_ask0 if home_idx == 0 else m.pre_ask1),
            "pre_ask_away": (m.pre_ask1 if home_idx == 0 else m.pre_ask0),
            "pre_n_fills": m.pre_n_fills, "pre_usd": m.pre_usd, "volume": m.volume,
        })
    return (pd.DataFrame(out),
            pd.DataFrame(unmatched, columns=["condition_id", "event_slug", "why"]))


def build_games(espn: pd.DataFrame | None = None) -> pd.DataFrame:
    """games.parquet   the 1,113 fills-bearing moneyline markets <-> their ESPN game.
       markets.parquet every football market in C.universe() (spreads, totals, props ...)
                       tagged with the ESPN game of its event, so a study of any market
                       type can join game state through `event_slug`."""
    espn = (pd.read_parquet(DIR / "espn_games.parquet") if espn is None else espn)
    mk = football_markets()
    games, unmatched = match_markets(mk, espn)
    dup = games.espn_id.duplicated(keep=False)
    if dup.any():
        log.warning("%d markets share an ESPN game (kept, inspect): %s",
                    int(dup.sum()), games.loc[dup, "event_slug"].head(6).tolist())
    _write(games, DIR / "games.parquet")
    _write(unmatched, DIR / "unmatched.parquet")
    log.info("matched %d/%d fills-bearing markets (%d payout mismatches, %d unmatched)",
             len(games), len(mk), int((~games.payout_ok).sum()), len(unmatched))

    # second pass over EVERY football event in the universe (incl. 2024 and sub-$50k games,
    # which have no local fills but do have spreads/totals worth studying)
    uml = universe_moneylines()
    uml = uml[~uml.event_slug.isin(set(games.event_slug))]
    ug, uunm = match_markets(uml, espn)
    log.info("extra universe events matched %d/%d", len(ug), len(uml))
    key = pd.concat([games.assign(has_fills=True), ug.assign(has_fills=False)]
                    ).drop_duplicates("event_slug").set_index("event_slug")
    from ..research import common as C
    u = C.universe(columns=["condition_id", "family", "league", "market_type", "event_slug",
                            "game_start_ts", "volume", "fee_rate", "neg_risk"])
    u = u[u.family == "american_football"].drop_duplicates("condition_id")
    mm = u.merge(key[["espn_id", "espn_name", "espn_date", "home_id", "away_id", "espn_home",
                      "espn_away", "final_home", "final_away", "lg", "has_fills"]],
                 left_on="event_slug", right_index=True, how="left")
    _write(mm, DIR / "markets.parquet")
    log.info("universe football markets %d, ESPN-mapped %d (%d events; %d events unmatched)",
             len(mm), int(mm.espn_id.notna().sum()),
             mm.loc[mm.espn_id.notna(), "espn_id"].nunique(), len(uunm))
    return games


# --------------------------------------------------------------------------------- plays

_TO = re.compile(r"[Tt]imeout\s*#?(\d)?\s*by\s+([A-Za-z .&'-]+?)\s+at\b")


def _clock_s(dv) -> float:
    if not dv:
        return np.nan
    p = str(dv).split(":")
    try:
        return float(p[0]) * 60 + float(p[1]) if len(p) == 2 else float(p[0])
    except ValueError:
        return np.nan


def _abbr_side(tok: str, home: dict, away: dict) -> int | None:
    """Map a play-text team token ('CLV', 'CHI', 'Ohio State') to 0=away / 1=home.
    ESPN's play text sometimes uses a different abbreviation than the team object
    (CLV vs CLE), so fall back to a character-overlap score."""
    t = _norm(tok)
    if not t:
        return None
    hs, as_ = _score(t, home["keys"]), _score(t, away["keys"])
    if hs != as_:
        return 1 if hs > as_ else 0
    def ov(keys):
        return max((len(set(t) & set(k)) / max(len(set(t) | set(k)), 1) for k in keys), default=0)
    oh, oa = ov(home["keys"]), ov(away["keys"])
    if abs(oh - oa) < 0.15:
        return None
    return 1 if oh > oa else 0


def plays(league: str, espn_id: str, home_id: str, away_id: str,
          home: dict | None = None, away: dict | None = None) -> pd.DataFrame:
    """Flatten drives.previous[].plays[] into one row per play, in ESPN order.

    Scores are the score AFTER the play. `off_home` is 1 when the home team has the ball
    (from the drive's team, falling back to the play's start.team)."""
    j = summary(league, espn_id)
    drives = (j.get("drives") or {}).get("previous") or []
    rows = []
    k = 0
    for di, d in enumerate(drives):
        dteam = str(((d.get("team") or {}).get("id")) or "")
        dres = d.get("result")
        for p in d.get("plays") or []:
            st, en = p.get("start") or {}, p.get("end") or {}
            oid = str((st.get("team") or {}).get("id") or "") or dteam
            typ = (p.get("type") or {}).get("text")
            rows.append({
                "espn_id": espn_id, "play_idx": k, "drive_idx": di,
                "drive_team": dteam, "drive_result": dres,
                "play_id": str(p.get("id") or ""),
                "wallclock": _parse_ts(p.get("wallclock")),
                "period": ((p.get("period") or {}).get("number")),
                "clock_s": _clock_s((p.get("clock") or {}).get("displayValue")),
                "off_id": oid, "end_id": str((en.get("team") or {}).get("id") or ""),
                "down": st.get("down"), "distance": st.get("distance"),
                "yard_line": st.get("yardLine"), "yards_to_ez": st.get("yardsToEndzone"),
                "end_down": en.get("down"), "end_yards_to_ez": en.get("yardsToEndzone"),
                "home_score": p.get("homeScore"), "away_score": p.get("awayScore"),
                "scoring": bool(p.get("scoringPlay")),
                "turnover": bool(p.get("isTurnover")),
                "penalty": bool(p.get("isPenalty")),
                "yards": p.get("statYardage"),
                "type": typ, "text": p.get("text"),
            })
            k += 1
    if not rows:
        return pd.DataFrame()
    df = pd.DataFrame(rows)
    df["period"] = pd.to_numeric(df.period, errors="coerce")
    df["off_home"] = np.where(df.off_id == str(home_id), 1,
                              np.where(df.off_id == str(away_id), 0, -1)).astype(np.int8)
    df["drive_home"] = np.where(df.drive_team == str(home_id), 1,
                                np.where(df.drive_team == str(away_id), 0, -1)).astype(np.int8)
    # who will have the ball NEXT (known the instant the play ends - not look-ahead)
    df["end_home"] = np.where(df.end_id == str(home_id), 1,
                              np.where(df.end_id == str(away_id), 0, -1)).astype(np.int8)
    # clock: remaining in period, and remaining in regulation (0 once in OT)
    df["reg_s"] = np.where(df.period <= 4, (4 - df.period) * PERIOD_S + df.clock_s.fillna(0), 0.0)
    df["ot"] = df.period > 4
    # timeouts: "Timeout #2 by CLV at 00:03." -> that team has used 2 this half
    if home is not None and away is not None:
        used_h = np.zeros(len(df)); used_a = np.zeros(len(df))
        ch = ca = 0
        half = (df.period.fillna(1) > 2).to_numpy()
        prev_half = None
        for i, (txt, typ, hf) in enumerate(zip(df.text, df["type"], half)):
            if prev_half is not None and hf != prev_half:
                ch = ca = 0
            prev_half = hf
            if typ == "Timeout" and isinstance(txt, str):
                mm = _TO.search(txt)
                if mm:
                    n = int(mm.group(1)) if mm.group(1) else None
                    side = _abbr_side(mm.group(2), home, away)
                    if side == 1:
                        ch = max(ch, n) if n else ch + 1
                    elif side == 0:
                        ca = max(ca, n) if n else ca + 1
            used_h[i], used_a[i] = ch, ca
        df["home_to"] = np.clip(3 - used_h, 0, 3).astype(np.int8)
        df["away_to"] = np.clip(3 - used_a, 0, 3).astype(np.int8)
    else:
        df["home_to"] = df["away_to"] = np.int8(-1)
    return df


def build_plays(games: pd.DataFrame | None = None, limit: int | None = None) -> pd.DataFrame:
    games = (pd.read_parquet(DIR / "games.parquet") if games is None else games)
    g = games.drop_duplicates("espn_id")
    if limit:
        g = g.head(limit)
    espn = pd.read_parquet(DIR / "espn_games.parquet").set_index("espn_id")
    parts, missing = [], []
    for i, r in enumerate(g.itertuples(index=False)):
        er = espn.loc[r.espn_id]
        home = {"keys": _keys(er, "home")}
        away = {"keys": _keys(er, "away")}
        try:
            df = plays(r.lg, r.espn_id, r.home_id, r.away_id, home, away)
        except Exception as exc:                                    # noqa: BLE001
            missing.append((r.espn_id, f"{type(exc).__name__}: {exc}"))
            continue
        if df.empty:
            missing.append((r.espn_id, "no drives"))
            continue
        parts.append(df)
        if i % 100 == 0:
            log.info("plays %d/%d", i + 1, len(g))
    p = pd.concat(parts, ignore_index=True)
    for c in ("down", "distance", "yard_line", "yards_to_ez", "end_down", "end_yards_to_ez",
              "home_score", "away_score", "yards"):
        p[c] = pd.to_numeric(p[c], errors="coerce").astype("float32")
    _write(p, DIR / "plays.parquet")
    pd.DataFrame(missing, columns=["espn_id", "why"]).to_parquet(DIR / "no_plays.parquet",
                                                                 index=False)
    log.info("plays %d rows, %d games (%d without drives)", len(p), p.espn_id.nunique(),
             len(missing))
    return p


def fetch_summaries(games: pd.DataFrame | None = None, force: bool = False) -> None:
    games = (pd.read_parquet(DIR / "games.parquet") if games is None else games)
    g = games.drop_duplicates("espn_id")
    for i, r in enumerate(g.itertuples(index=False)):
        try:
            summary(r.lg, r.espn_id, force)
        except Exception as exc:                                     # noqa: BLE001
            log.warning("summary %s %s: %s", r.lg, r.espn_id, exc)
        if i % 100 == 0:
            log.info("summary %d/%d", i + 1, len(g))


# ---------------------------------------------------------------------------- the panel

BREAK_TYPES = {"End Period", "End of Half", "End of Game", "Timeout", "Official Timeout",
               "Two-minute warning", "End of Regulation"}
WC_AGREE = 0.90       # a play's wallclock must agree with the play order this often
WC_WINDOW = (-3600, 8 * 3600)   # plausible wallclock offset from scheduled kickoff


def _wallclock_ok(wc: np.ndarray, kickoff: float) -> np.ndarray:
    """Which wallclocks to trust.

    ESPN wallclocks are entered by a human scorer: a few percent of plays (mostly CFB)
    carry an obviously wrong stamp - a play a whole day off, or a couple of plays typed
    out of order. A wrong stamp is worse than a missing one here, because it would price
    a state from a window in which the game was in a different situation.

    A stamp is trusted when it agrees with ESPN's play ORDER for at least WC_AGREE of the
    game's other plays (pairwise: earlier play => earlier or equal stamp). That kills
    isolated bad stamps without letting one of them cascade through the rest of the game,
    which a running-max rule would do.
    """
    ok = np.isfinite(wc) & (wc >= kickoff + WC_WINDOW[0]) & (wc <= kickoff + WC_WINDOW[1])
    n = len(wc)
    if n < 5 or ok.sum() < 5:
        return ok
    idx = np.flatnonzero(ok)
    v = wc[idx]
    later = v[None, :] >= v[:, None]          # [i, j] stamp j at or after stamp i
    after = idx[None, :] >= idx[:, None]      # [i, j] play j at or after play i
    agree = (later == after).mean(axis=1)
    ok[idx] = agree >= WC_AGREE
    return ok


def build_panel(games: pd.DataFrame | None = None, plays_df: pd.DataFrame | None = None
                ) -> pd.DataFrame:
    """State x price: one row per play boundary with the price actually traded after it.

    Columns added on top of the play state:
      p_home       median traded price of the HOME side over [wc+PRICE_LO_S, min(wc+PRICE_HI_S,
                   next play)] - the market's opinion of THIS state. Not tradable.
      p_home_ask   median price a taker PAID to acquire home in that window (the ask side)
      p_home_bid   median 1-q over takers who acquired away (the bid side for home)
      n_fills/usd  prints and taker notional in the window -> coverage and capacity
      ex_home_p/ts first print acquiring HOME at >= wc + ENTRY_LAG_S (what you could pay),
      ex_away_p/ts same for the AWAY side. ts are raw on-chain timestamps.
    """
    from ..research import common as C
    games = (pd.read_parquet(DIR / "games.parquet") if games is None else games)
    g = games[games.payout_ok & ~games.void & games.home_won.notna()].drop_duplicates("espn_id").copy()
    p = (pd.read_parquet(DIR / "plays.parquet") if plays_df is None else plays_df)
    p = p[p.espn_id.isin(set(g.espn_id))].copy()

    f = C.fills(markets=g.m.to_numpy(np.int32), columns=["m", "ts", "size", "s", "q"])
    f = f.sort_values(["m", "ts"], kind="stable")
    log.info("fills %d over %d markets", len(f), f.m.nunique())

    meta = g.set_index("espn_id")
    p = p.merge(meta[["m", "condition_id", "home_idx", "lg", "league", "season", "season_type",
                      "fee_rate", "espn_date", "game_start_ts", "closed_ts", "home_won", "final_home",
                      "final_away", "pre_mid_home", "pre_ask_home", "pre_ask_away", "pre_usd",
                      "pre_n_fills", "neutral", "home_abbr", "away_abbr", "volume"]],
                left_on="espn_id", right_index=True, how="left")
    p = (p[p.wallclock.notna()].sort_values(["espn_id", "play_idx"], kind="stable")
         .reset_index(drop=True))

    # ---- timing hygiene: drop plays whose wallclock cannot be trusted (see _wallclock_ok)
    keep = np.zeros(len(p), bool)
    for _, grp in p.groupby("espn_id", sort=False):
        keep[grp.index.to_numpy()] = _wallclock_ok(grp.wallclock.to_numpy(float),
                                                   float(grp.espn_date.iloc[0]))
    n_drop = int((~keep).sum())
    p = p[keep].reset_index(drop=True)
    log.info("dropped %d plays (%.2f%%) with an untrustworthy wallclock", n_drop,
             100 * n_drop / max(len(p) + n_drop, 1))

    by_game = p.groupby("espn_id", sort=False)
    # ---- score hygiene: a score never falls. ESPN splits an extra point across rows, so
    # the raw column dips (7 -> 6 -> 7) for a play or two; a running max is what a trader
    # actually saw. Raw values are kept, and the RAW anomaly counts drive `game_ok`.
    p["home_score_raw"], p["away_score_raw"] = p.home_score, p.away_score
    p["home_score"] = by_game.home_score.cummax()
    p["away_score"] = by_game.away_score.cummax()
    by_game = p.groupby("espn_id", sort=False)
    p["next_wc"] = by_game.wallclock.shift(-1)
    p["margin"] = p.home_score - p.away_score
    p["prev_margin"] = by_game.margin.shift(1).fillna(0)
    p["d_margin"] = p.margin - p.prev_margin
    p["elapsed_s"] = 3600.0 - p.reg_s
    p["since_kick_s"] = p.wallclock - by_game.wallclock.transform("min")
    p["is_break"] = p["type"].isin(BREAK_TYPES)
    # the situation a trader faces at this boundary: the last scrimmage play's END state
    # (timeout / end-of-period rows carry junk down-distance, so they are filled forward)
    sc = ~p.is_break
    for src, dst in (("end_down", "up_down"), ("end_yards_to_ez", "up_ytez")):
        p[dst] = p[src].where(sc).groupby(p.espn_id).ffill()
    p["up_off_home"] = (p.end_home.where(sc & p.end_home.ge(0)).groupby(p.espn_id).ffill())
    p["is_final"] = p["type"].eq("End of Game") | (p.play_idx == by_game.play_idx.transform("max"))
    p["in_play"] = p.wallclock >= p.espn_date

    out = {c: np.full(len(p), np.nan) for c in
           ("p_home", "n_fills", "usd", "p_home_ask", "n_ask", "p_home_bid", "n_bid",
            "ex_home_p", "ex_home_ts", "ex_away_p", "ex_away_ts")}
    by_m = {int(k): v for k, v in f.groupby("m")}
    for _, grp in p.groupby("espn_id", sort=False):
        rows = grp.index.to_numpy()
        n = len(rows)
        ff = by_m.get(int(grp.m.iloc[0]))
        if ff is None or not len(ff):
            continue
        hidx = int(grp.home_idx.iloc[0])
        ts_raw = ff.ts.to_numpy(float)
        fts = ts_raw - TRADE_LAG_S      # align settlement time back to match time
        fq = ff.q.to_numpy(float)
        fsz = ff["size"].to_numpy(float)
        home_buy = ff.s.to_numpy(np.int8) == hidx
        p_home_all = np.where(home_buy, fq, 1.0 - fq)

        wc = grp.wallclock.to_numpy(float)
        nxt = grp.next_wc.to_numpy(float)
        hi = np.minimum(wc + PRICE_HI_S,
                        np.where(np.isfinite(nxt), nxt - LEAK_GUARD_S, np.inf))
        hi = np.maximum(hi, wc + PRICE_LO_S)
        a = np.searchsorted(fts, wc + PRICE_LO_S, "left")
        b = np.searchsorted(fts, hi, "right")

        ph = np.full(n, np.nan); nf = (b - a).astype(float); us = np.zeros(n)
        pa = np.full(n, np.nan); na = np.zeros(n)
        pb = np.full(n, np.nan); nb = np.zeros(n)
        for i in np.flatnonzero(b > a):
            lo, hh = a[i], b[i]
            ph[i] = np.median(p_home_all[lo:hh])
            us[i] = float((fsz[lo:hh] * fq[lo:hh]).sum())
            hm = home_buy[lo:hh]
            if hm.any():
                pa[i] = np.median(fq[lo:hh][hm]); na[i] = int(hm.sum())
            if (~hm).any():
                pb[i] = np.median(1.0 - fq[lo:hh][~hm]); nb[i] = int((~hm).sum())

        # executable entry: the NEXT print on that side, at least ENTRY_LAG_S after the play
        ent = np.searchsorted(fts, wc + ENTRY_LAG_S, "left")
        cap = np.searchsorted(fts, wc + ENTRY_MAX_S, "right")
        ex = {}
        for side, mask in (("home", home_buy), ("away", ~home_buy)):
            idx = np.flatnonzero(mask)
            j = np.searchsorted(idx, ent, "left")
            ok = j < len(idx)
            k = idx[np.clip(j, 0, len(idx) - 1)] if len(idx) else np.zeros(n, int)
            ok &= len(idx) > 0
            ok &= k < cap
            ex[side] = (np.where(ok, fq[k] if len(idx) else np.nan, np.nan),
                        np.where(ok, ts_raw[k] if len(idx) else np.nan, np.nan))

        for name, vals in (("p_home", ph), ("n_fills", nf), ("usd", us), ("p_home_ask", pa),
                           ("n_ask", na), ("p_home_bid", pb), ("n_bid", nb),
                           ("ex_home_p", ex["home"][0]), ("ex_home_ts", ex["home"][1]),
                           ("ex_away_p", ex["away"][0]), ("ex_away_ts", ex["away"][1])):
            out[name][rows] = vals
    for k, v in out.items():
        p[k] = v
    p["has_price"] = p.n_fills >= MIN_FILLS
    p["p_home"] = p.p_home.where(p.has_price)
    p["spread"] = p.p_home_ask - p.p_home_bid
    # how long you waited for that executable print: a fill 3 minutes later has seen more
    # football than your signal did, so a hypothesis must bound this itself
    p["ex_home_dt"] = p.ex_home_ts - p.wallclock
    p["ex_away_dt"] = p.ex_away_ts - p.wallclock
    p["dev"] = p.wallclock < pd.Timestamp("2026-07-01", tz="UTC").timestamp()
    p = p.drop(columns=["prev_margin"])

    # ---- per-game quality, from the RAW (unrepaired) columns
    bg = p.groupby("espn_id", sort=False)
    q = pd.DataFrame({
        "q_score_back": ((bg.home_score_raw.diff() < 0) | (bg.away_score_raw.diff() < 0)
                         ).groupby(p.espn_id).sum(),
        "q_period_back": (bg.period.diff() < 0).groupby(p.espn_id).sum(),
        "q_clock_fwd": (bg.period.diff().eq(0) & (bg.clock_s.diff() > 0)).groupby(p.espn_id).sum(),
        "q_wc_back": (bg.wallclock.diff() < 0).groupby(p.espn_id).sum(),
        "q_plays": bg.size(),
    })
    fin = p.sort_values("play_idx").groupby("espn_id").last()
    q["q_final_ok"] = ((fin.home_score == fin.final_home) & (fin.away_score == fin.final_away))
    # a game is usable unless ESPN's feed is structurally broken (interleaved / duplicated
    # drives show up as many score reversals and a scrambled clock)
    q["game_ok"] = (q.q_score_back <= 2) & (q.q_period_back == 0) & (q.q_clock_fwd <= 4) & \
                   (q.q_plays >= 40)
    p = p.merge(q, left_on="espn_id", right_index=True, how="left")
    log.info("game quality: %d/%d games ok, %d with a final-score mismatch",
             int(q.game_ok.sum()), len(q), int((~q.q_final_ok).sum()))
    _write(p, DIR / "panel.parquet")
    log.info("panel %d rows, %d games, %.1f%% with a usable price (%.1f%% in play)",
             len(p), p.espn_id.nunique(), 100 * p.has_price.mean(),
             100 * p.loc[p.in_play, "has_price"].mean())
    return p


# ------------------------------------------------------------------------- validation

def validate(panel: pd.DataFrame | None = None) -> dict:
    """Integrity checks. Anything that fails here would fake an edge downstream."""
    p = (pd.read_parquet(DIR / "panel.parquet") if panel is None else panel)
    g = pd.read_parquet(DIR / "games.parquet")
    mk = football_markets()
    r: dict = {}
    by = p.groupby("espn_id", sort=False)

    # ---- coverage
    r["markets_total"] = len(mk)
    r["markets_matched"] = len(g)
    r["payout_mismatch"] = int((~g.payout_ok).sum())
    r["games_in_panel"] = int(p.espn_id.nunique())
    r["games_ok"] = int(p.loc[p.game_ok, "espn_id"].nunique())
    r["rows"] = len(p)
    r["rows_game_ok"] = int(p.game_ok.sum())

    # ---- 1. scores: repaired column never falls; raw anomalies are counted per game
    r["score_backwards_repaired"] = int(((by.home_score.diff() < 0) |
                                         (by.away_score.diff() < 0)).sum())
    r["score_backwards_raw"] = int(((by.home_score_raw.diff() < 0) |
                                    (by.away_score_raw.diff() < 0)).sum())
    moved = by.home_score.diff().fillna(0).ne(0) | by.away_score.diff().fillna(0).ne(0)
    r["scoring_plays_that_moved_score"] = float(moved[p.scoring].mean())
    r["score_moves_off_a_scoring_play"] = int((moved & ~p.scoring).sum())
    mx = p.groupby("espn_id").agg(h=("home_score", "max"), a=("away_score", "max"),
                                  fh=("final_home", "first"), fa=("final_away", "first"))
    r["max_score_matches_espn_final"] = float(((mx.h == mx.fh) & (mx.a == mx.fa)).mean())
    r["max_score_matches_winner"] = float((np.sign(mx.h - mx.a) == np.sign(mx.fh - mx.fa)).mean())

    # ---- 2. clock / wallclock ordering
    r["period_backwards"] = int((by.period.diff() < 0).sum())
    r["clock_forwards_in_period"] = int((by.period.diff().eq(0) & (by.clock_s.diff() > 0)).sum())
    r["wallclock_backwards"] = int((by.wallclock.diff() < 0).sum())
    d = by.wallclock.diff()
    r["play_gap_s_p50"] = float(d.median())
    r["play_gap_s_p95"] = float(d.quantile(0.95))
    r["kick_offset_min_p50"] = float(((p.wallclock - p.espn_date) / 60).groupby(p.espn_id).min().median())
    r["game_len_min_p50"] = float((by.wallclock.max() - by.wallclock.min()).median() / 60)
    r["before_kickoff_frac"] = float((p.wallclock < p.espn_date).mean())
    r["after_market_close_frac"] = float((p.closed_ts.notna() & (p.wallclock > p.closed_ts)).mean())

    # ---- 3. price coverage / execution
    r["has_price_frac"] = float(p.has_price.mean())
    for lg, s_ in p[p.in_play].groupby("lg"):
        r[f"has_price_frac_{lg}"] = float(s_.has_price.mean())
    r["exec_home_frac"] = float(p.ex_home_p.notna().mean())
    r["exec_away_frac"] = float(p.ex_away_p.notna().mean())
    r["median_spread_c"] = float((100 * p.spread).median())
    r["median_window_usd"] = float(p.loc[p.has_price, "usd"].median())

    # ---- 4. does the price track the game?
    q = p[p.has_price & p.in_play & p.game_ok]
    r["price_brier"] = float(((q.p_home - q.home_won.astype(float)) ** 2).mean())
    r["price_mean"] = float(q.p_home.mean())
    r["home_won_mean"] = float(q.home_won.astype(float).mean())
    p_prev = p.p_home.groupby(p.espn_id).ffill().groupby(p.espn_id).shift(1)
    td = p[p.scoring & p.d_margin.abs().ge(6) & p.has_price & p.game_ok].copy()
    td["p_prev"] = p_prev.reindex(td.index)
    mv = td.p_home - td.p_prev
    r["td_n"] = int(mv.notna().sum())
    r["home_td_price_move_c"] = float((100 * mv[td.d_margin > 0]).median())
    r["away_td_price_move_c"] = float((100 * mv[td.d_margin < 0]).median())
    r["td_moves_right_way_frac"] = float((np.sign(mv) == np.sign(td.d_margin)).mean())
    # calibration: the traded price should predict the result
    b = pd.cut(q.p_home, [0, .1, .2, .3, .4, .5, .6, .7, .8, .9, 1.0])
    cal = q.groupby(b, observed=True).agg(n=("p_home", "size"), p=("p_home", "mean"),
                                          won=("home_won", "mean"))
    r["calibration_max_abs_gap"] = float((cal.p - cal.won.astype(float)).abs().max())
    r["_calibration"] = cal
    return r


def print_validation(panel: pd.DataFrame | None = None) -> None:
    r = validate(panel)
    cal = r.pop("_calibration", None)
    for k, v in r.items():
        print(f"  {k:36s} {v:,.4f}" if isinstance(v, float) else f"  {k:36s} {v:,}")
    if cal is not None:
        print("\n  traded price vs. realised result (in-play, game_ok):")
        print("    " + cal.to_string().replace("\n", "\n    "))


# ------------------------------------------------------------------------------- CLI

def main(argv: list[str]) -> None:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(message)s")
    cmd = argv[0] if argv else "all"
    if cmd in ("all", "scoreboards"):
        fetch_scoreboards()
        build_espn_games()
    if cmd in ("all", "games"):
        build_games()
    if cmd in ("all", "summaries"):
        fetch_summaries()
    if cmd in ("all", "plays"):
        build_plays()
    if cmd in ("all", "panel"):
        build_panel()
    if cmd in ("all", "validate"):
        print_validation()


if __name__ == "__main__":
    main(sys.argv[1:])
