from datetime import datetime, timezone
from types import SimpleNamespace

from app.services.template_teams import price_slot_suggestions, template_summaries


def _rows():
    rows = []
    player_id = 1
    for position, count in [("GKP", 2), ("DEF", 5), ("MID", 5), ("FWD", 3)]:
        for index in range(count):
            rows.append({
                "player": SimpleNamespace(id=player_id, full_name=f"Player {player_id}", web_name=f"P{player_id}", position_short=position),
                "team": SimpleNamespace(id=(player_id % 6) + 1, short_name=f"T{player_id % 6}"),
                "snapshot": SimpleNamespace(price=5.0 + (index * 0.2), projected_points_5=25.0 - index, total_points=100 - index, reliable_value=10.0 - index / 10, forward_value=8.0 - index / 10, availability_factor=1.0, rotation_risk=10.0, captured_at=datetime.now(timezone.utc)),
            })
            player_id += 1
    return rows


def test_template_summaries_reuse_legal_recommender_squads():
    templates = template_summaries(_rows(), 100.0)

    assert templates
    assert all(len(item["recommendation"]["starting"]) + len(item["recommendation"]["bench"]) == 15 for item in templates)
    assert all(item["recommendation"]["spent"] <= 100.0 for item in templates)


def test_price_slot_suggestions_are_same_position_affordable_and_ordered():
    rows = _rows()
    selected = rows[11]
    suggestions = price_slot_suggestions(selected, rows)

    assert len(suggestions) == 3
    assert all(row["player"].position_short == selected["player"].position_short for row in suggestions)
    assert all(row["snapshot"].price <= selected["snapshot"].price for row in suggestions)
    assert [row["snapshot"].projected_points_5 for row in suggestions] == sorted(
        [row["snapshot"].projected_points_5 for row in suggestions], reverse=True
    )
