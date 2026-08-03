# FPL Decision Foundation Design

## Scope

This increment turns the existing daily FPL snapshots into three decision surfaces: transparent differentials, transfer-market movement, and valid team-template/price-slot suggestions. It is deliberately source-aware: the application will calculate only from current and historical FPL snapshots and will label fields unavailable from those snapshots.

## Architecture

`app/services/player_intelligence.py` will convert existing query rows into one normalized, presentation-safe derived object. It will compute freshness, confidence, differential scoring, score components, and transfer movement without changing the raw FPL ingestion contract. Routes consume this service, while templates render its returned data without embedding business logic.

The existing `team_recommender.py` remains the sole owner of FPL squad constraints and optimization. A narrow template adapter will request its established strategies, expose their projected totals, and group selected players into price slots. Slot alternatives come from the shared intelligence rows and use a deterministic affordability rule.

## Data and uncertainty

- Transfers-in/out are current totals provided by the public bootstrap response; velocity and acceleration are computed only when at least two local snapshots exist.
- All insight objects carry observed/calculated status, captured time, freshness, and confidence.
- Differential score uses existing forward value, expected minutes, ownership, rotation risk, availability, form, and price. It is an explainable heuristic, not a forecast.
- No elite-manager, injury-provider, or intraday-price claims will be shown until those sources exist.

## Validation

Tests first cover score direction/category, transfer delta/velocity behavior, stale-state metadata, and price-slot selection. Route tests cover successful rendering with a populated database and empty-state safety. The full test suite and Docker/web health smoke test provide regression evidence.
