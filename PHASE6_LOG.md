# Phase 6 — Deliverability Score Engine — Implementation Log

Companion to `migration_plan.md`, `PROJECT_LOG.md`, and the prior phase logs.

## Objective

Implement a weighted deliverability score engine that works before and during campaign send, supports SMTP and non-SMTP paths, surfaces clear warnings/suggestions, blocks risky sends below a configurable threshold, and gives the Electron operator a dedicated score/prediction panel.

## Score model and weights

`backend/models/deliverability.py` adds:

- `ScoreComponent` — component name, 0–100 score, weight, and detail string.
- `DeliverabilityScore` — overall 0–100 score, component breakdown, warnings, suggestions, threshold, pass/fail flag, and `to_dict()` for API/UI payloads.

The Phase 6 weights sum to 100:

| Component | Weight |
|---|---:|
| Domain reputation | 25 |
| Historical inbox placement | 20 |
| Spam/junk complaint proxies | 15 |
| Content quality | 25 |
| Engagement | 15 |

Default threshold is 70. Clean short content from a verified domain remains above the default threshold, preserving existing send behavior.

## Service methods

`backend/services/deliverability.py` adds `DeliverabilityService` with dependency injection for `LedgerRepository`, optional `DomainService`, and threshold.

- `domain_reputation(sender)` — extracts the sender domain, scores managed domains from `DomainService.health_score()`, reports missing DKIM/SPF/DMARC, and gives unmanaged/unknown domains a neutral-but-penalized baseline with verification guidance.
- `historical_inbox_placement(campaign_id)` — uses recipient status rollups to compare sent/delivered/opened/clicked outcomes against failed/bounced outcomes. Empty history returns a neutral baseline.
- `spam_complaints(campaign_id)` — uses bounces, failures, and unsubscribes as complaint proxy signals. Empty history returns a neutral baseline.
- `content_quality(content, html=False)` — calls `analyze_compose()` on the raw template content and maps spam words, HTML ratio, Jinja errors, and unknown placeholders into a score. Known autograb placeholders such as `[firstname]` and `{{ greetings }}` are not penalized.
- `engagement(campaign_id)` — scores OPEN/CLICK events when present. Missing tracking is neutral, not punitive.
- `score_campaign(campaign_id, *, content=None, html=False, sender=None)` — scores a persisted campaign, falling back to the latest persisted message content when available and otherwise warning that content was not analyzed.
- `predict(content, recipients, *, sender=None, html=False)` — simulates a prospective send without requiring persisted history; history and engagement are neutral while domain/content/recipient signals are still evaluated.

## API endpoints and send gate

`backend/api/app.py` now wires a singleton `DeliverabilityService` through `create_app(..., deliverability_service=None, min_deliverability_score=70)`.

New endpoints:

- `GET /campaigns/{campaign_id}/score` — returns `DeliverabilityScore.to_dict()`; accepts optional `content`, `sender`, and `html` query params and returns 404 for missing campaigns.
- `POST /campaigns/{campaign_id}/predict` — accepts recipients, subject, content, sender, and html; returns the predicted score without sending and returns 404 for missing campaigns.

Send integration:

- `SendRequest` accepts `non_smtp_delivery: bool = False` for the UI payload.
- `POST /campaigns/{campaign_id}/send` still performs existing domain verification first.
- The endpoint then predicts deliverability for the outgoing subject/content/sender/recipients and rejects HTTP 400 when the score is below the configured threshold.
- SMTP and non-SMTP sends use the same score gate.

## Electron UI panel

`electron/renderer/pages/Deliverability.jsx` adds a dedicated panel registered in `App.jsx` and `Sidebar.jsx`.

The panel includes:

- Tracked campaign selection using the existing `paris_sender_campaigns` localStorage pattern, plus a manual campaign-id input.
- Per-campaign score loading with pass/block badge, weighted breakdown table, health bars, warnings, and suggestions.
- A "Predict Before Send" form for sender, recipients, subject, content, and HTML mode that calls `/campaigns/{id}/predict` and renders the simulated result.

Screenshots are unavailable in this headless CI/session environment because the Electron GUI cannot be launched with a display. No screenshots were fabricated.

## Tests

Added:

- `tests/test_deliverability.py` — service bounds, high clean score, spam/unverified lower score, empty history, autograb placeholder neutrality, prediction without persisted history, non-SMTP score validity, and history/spam proxy effects.
- `tests/test_deliverability_api.py` — score/predict happy paths and 404s, send blocked below threshold, and send allowed above threshold.

Final validation:

```bash
python -m pytest tests/ -q
```

Result:

```text
48 passed, 1 warning in 5.48s
```

```bash
python -m unittest test_fixes.py
```

Result:

```text
Ran 216 tests in 0.657s

OK
```

```bash
cd electron && npm install --quiet && npx vite build
```

Result:

```text
✓ built in 134ms
```

## Phase 6 Quality Gate

| Gate item | Status |
|---|---|
| Deliverability score model with weighted components | ✅ |
| Domain reputation, history, spam proxy, content, and engagement components | ✅ |
| Autograb placeholders do not reduce score | ✅ |
| Predict-before-send simulation works without persisted history | ✅ |
| Score API and predict API return JSON-friendly breakdowns | ✅ |
| Send endpoint gates SMTP and non-SMTP paths by score | ✅ |
| Existing domain enforcement and endpoints preserved | ✅ |
| Electron Deliverability panel registered and Vite build passes | ✅ |
| Backend pytest suite remains green | ✅ |
| Legacy unittest suite remains green | ✅ |
