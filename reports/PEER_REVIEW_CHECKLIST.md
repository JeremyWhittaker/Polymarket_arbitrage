# Independent peer review checklist — 2026-09-22

Scope: review Claude's work at `4fe2138`, including the nine pre-existing untracked football study/report files. Preserve application code and cached research data. Goal: establish prompt alignment and whether any claimed edge survives independent scrutiny.

Status vocabulary: `implemented` means the review task is completed with evidence; it does not mean application defects were repaired. `deferred` means pending review or a proposed follow-up outside this review. `blocked` requires a concrete missing input.

| Review item | Status | Evidence / next action |
|---|---|---|
| Recover original prompt and later changes | implemented | PEER_REVIEW.md intent matrix; original transcript line 8 and subsequent human messages identified. |
| Data collection, market identity, timing and completeness | implemented | Review findings 1/4/9/10; live API probes confirmed pre-window trades. |
| Original MLB state, favorites and timing hypotheses | implemented | Source review plus phantom-state reproduction and observed live-latency crash. |
| Wallet selection and copy execution | implemented | PEER_REVIEW_EVIDENCE.json monthly_selection and delayed_other_wallet_prints. |
| Every-price calibration and 74–75c whale anomaly | implemented | Reproduced original signal means, event-capped and delayed-entry sensitivities. |
| Structural strategy studies and surviving positive claims | implemented | Independent strategy review; esports calibration and football capacity examined. |
| Holdouts, multiple testing and statistical uncertainty | implemented | Explored-holdout qualification, clustered intervals, bootstrap-label issue documented. |
| Per-trade research desk, reproducibility and dependencies | implemented | HTTP 25/22 mismatch, sampling, filtered/global metrics, missing dependencies confirmed. |
| Existing tests and targeted reproductions | implemented | 11 tests passed; bounded peer_review_checks.py completed under 3 GB cap. |
| Current venue documentation | implemented | Primary fee, order-lifecycle and Trade API documentation linked in review. |
| Ranked findings and next edge experiment | implemented | PEER_REVIEW.md and machine-readable PEER_REVIEW_FINDINGS.json. |
| Repair application code, recollect history or trade | deferred | Outside the user's request for analysis and peer review. |

Every material repair recommendation remains explicitly deferred to an implementation task; review completion is not application acceptance.

| Material feedback item | Status | Concrete evidence / repair target |
|---|---|---|
| Remove future-volume and completed-pregame selection | deferred | Finding 1: CLI source floor 50k versus downstream 25k; favorites.py / calibration.py. |
| Make wallet selection causal and outcome-aware | deferred | Finding 2: synthetic 900.50 versus 0.50 cost leak; study.py:170. |
| Separate signal and execution; enforce depth, size and hedge exits | deferred | Finding 3: signal-print entries, repeated follower prints, ladder sizes; calibration.py / whale_prices.py / football studies. |
| Repair inning transitions and temporal price alignment | deferred | Finding 4: phantom-state reproduction and 33.1% window overlap; panel.py. |
| Reset holdout discipline and correct weighting/statistical claims | deferred | Finding 5: reused holdout, weighting assertion and non-null bootstrap label. |
| Version and invalidate study caches | deferred | Finding 6: existence-only named pickle cache; wallets/report.py:42. |
| Refresh desk index and export every row | deferred | Finding 7: HTTP 25 versus22; four truncated ledgers. |
| Correct sport-filter headline scope | deferred | Finding 7: app.js:86 uses all-sport metadata. |
| Declare dependencies and verify clean startup | deferred | Finding 8: missing FastAPI/Uvicorn; requirements.txt. |
| Backfill or accurately disclose tape windows | deferred | Finding 9: three successful pre-window API probes; tapes.py:37. |
| Recover UTC rollover metadata | deferred | Finding 10: September19 live-latency FileNotFoundError; record.py / analysis/live.py. |
| Complete final soccer/baseball studies and synthesis | deferred | Original workflow13 quota failures; SPORTS.md absent. |
| Integrate pre-existing untracked football work | deferred | Nine entry-baseline paths retained unchanged; review did not own their implementation. |
| Desktop/mobile visual review | deferred | HTTP and source behavior reviewed; no browser-rendering verification or UI change in this task. |
