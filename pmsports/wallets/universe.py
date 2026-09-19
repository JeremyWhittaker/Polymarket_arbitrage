"""Every resolved sports *game* market on Polymarket (all leagues), one row per outcome token.

Source: Gamma keyset over tag 100639 ("Games"; every league's game events carry it).
Output: data/wallets/universe.parquet with token_id, condition_id, outcome, payout (1/0,
0.5 for void/push), sport family, league, market type, game start, event slug.
"""
from __future__ import annotations

import json
import logging

import pandas as pd

from ..collect import DATA_DIR, _write
from ..http import get_json
from ..polymarket import GAMMA, parse_ts

log = logging.getLogger("pmsports")
GAMES_TAG = 100639
OUT = DATA_DIR / "wallets"

# family by Gamma tag label / league code (first match wins; order matters)
FAMILY_RULES = [
    ("esports", {"esports", "league of legends", "counter-strike", "cs2", "dota", "valorant", "lol", "call of duty"}),
    ("baseball", {"mlb", "baseball", "kbo", "npb", "world baseball classic", "wbc"}),
    ("basketball", {"nba", "wnba", "basketball", "ncaab", "cbb", "march madness", "euroleague"}),
    ("american_football", {"nfl", "nfl (all)", "cfb", "cfb (all)", "ncaaf", "college football", "football"}),
    ("hockey", {"nhl", "hockey", "khl", "ahl", "shl"}),
    ("tennis", {"tennis", "atp", "wta", "itf", "challenger", "wimbledon", "us open tennis"}),
    ("soccer", {"soccer", "epl", "premier league", "champions league", "europa league", "la liga", "serie a",
                "bundesliga", "ligue 1", "mls", "uefa", "fifa", "world cup", "intl. soccer", "conmebol", "copa"}),
    ("cricket", {"cricket", "ipl", "t20"}),
    ("mma_boxing", {"ufc", "mma", "boxing"}),
    ("golf", {"golf", "pga", "liv"}),
    ("motorsport", {"f1", "formula 1", "nascar", "motogp"}),
]
PREFIX_FAMILY = {"mlb": "baseball", "kbo": "baseball", "npb": "baseball", "nba": "basketball",
                 "wnba": "basketball", "cbb": "basketball", "ncaab": "basketball", "nfl": "american_football",
                 "cfb": "american_football", "nhl": "hockey", "atp": "tennis", "wta": "tennis", "itf": "tennis",
                 "epl": "soccer", "ucl": "soccer", "uel": "soccer", "lal": "soccer", "bun": "soccer",
                 "sea": "soccer", "fl1": "soccer", "mls": "soccer", "fifwc": "soccer", "ufc": "mma_boxing",
                 "lol": "esports", "cs2": "esports", "dota2": "esports", "val": "esports", "cricipl": "cricket"}


def family_of(labels: list[str], slug: str) -> str:
    low = {x.lower() for x in labels if x}
    for fam, keys in FAMILY_RULES:
        if low & keys:
            return fam
    prefix = (slug or "").split("-")[0].lower()
    if prefix in PREFIX_FAMILY:
        return PREFIX_FAMILY[prefix]
    if prefix.startswith("bk"):
        return "basketball"
    return "other"


def _rows(e: dict) -> list[dict]:
    labels = [t.get("label", "") for t in (e.get("tags") or [])]
    fam = family_of(labels, e.get("slug", ""))
    league = e.get("seriesSlug") or (e.get("slug") or "").split("-")[0]
    out = []
    for m in e.get("markets") or []:
        try:
            outcomes = json.loads(m.get("outcomes") or "[]")
            tokens = json.loads(m.get("clobTokenIds") or "[]")
            prices = [float(p) for p in json.loads(m.get("outcomePrices") or "[]")]
        except (ValueError, TypeError):
            continue
        if len(outcomes) != len(tokens) or len(prices) != len(tokens) or not tokens:
            continue
        resolved = m.get("umaResolutionStatus") == "resolved" or (
            m.get("closed") and all(p in (0.0, 0.5, 1.0) for p in prices) and abs(sum(prices) - 1) < 1e-6)
        if not resolved:
            continue
        mtype = m.get("sportsMarketType") or ("moneyline" if m.get("slug") == e.get("slug") else "other")
        start = parse_ts(m.get("gameStartTime")) or parse_ts(e.get("startTime"))
        fee_rate = (m.get("feeSchedule") or {}).get("rate") if m.get("feesEnabled") else 0.0
        for i, (o, t, p) in enumerate(zip(outcomes, tokens, prices)):
            out.append({"token_id": t, "condition_id": m.get("conditionId"), "outcome": o, "outcome_idx": i,
                        "payout": p, "family": fam, "league": league, "market_type": mtype,
                        "game_start_ts": start, "closed_ts": parse_ts(m.get("closedTime")),
                        "event_slug": e.get("slug"), "market_slug": m.get("slug"),
                        "volume": float(m.get("volumeNum") or 0), "neg_risk": bool(m.get("negRisk")),
                        "fee_rate": float(fee_rate or 0.0)})
    return out


def build_universe() -> pd.DataFrame:
    rows, cursor, pages = [], None, 0
    while True:
        params = {"tag_id": GAMES_TAG, "limit": 100, "closed": "true"}
        if cursor:
            params["after_cursor"] = cursor
        j = get_json(f"{GAMMA}/events/keyset", params)
        for e in j.get("events", []):
            rows += _rows(e)
        pages += 1
        if pages % 100 == 0:
            log.info("universe: %d pages, %d outcome tokens", pages, len(rows))
        cursor = j.get("next_cursor")
        if not cursor or not j.get("events"):
            break
    df = pd.DataFrame(rows).drop_duplicates("token_id")
    _write(df, OUT / "universe.parquet")
    log.info("universe: %d tokens, %d markets, families %s", len(df), df.condition_id.nunique(),
             df.drop_duplicates("condition_id").family.value_counts().to_dict())
    return df
