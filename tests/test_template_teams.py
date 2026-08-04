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
                # expected_minutes is required for a projection to exist at all:
                # project_next_fixtures returns None without it, so a snapshot
                # carrying projected_points_5 and no expected minutes is a state
                # the refresh pipeline cannot produce.
                "snapshot": SimpleNamespace(price=5.0 + (index * 0.2), projected_points_5=25.0 - index, total_points=100 - index, reliable_value=10.0 - index / 10, forward_value=8.0 - index / 10, availability_factor=1.0, expected_minutes=85.0, upcoming_fixture_count=5, rotation_risk=10.0, captured_at=datetime.now(timezone.utc)),
            })
            player_id += 1
    return rows


def test_template_summaries_reuse_legal_recommender_squads():
    summary = template_summaries(_rows(), 100.0)
    templates = summary["templates"]

    assert templates
    assert summary["checks"] is None
    assert all(len(item["recommendation"]["starting"]) + len(item["recommendation"]["bench"]) == 15 for item in templates)
    assert all(item["recommendation"]["spent"] <= 100.0 for item in templates)


def test_template_summaries_report_not_ready_instead_of_inventing_squads():
    """With no projections, no template can be built and none is pretended."""
    rows = _rows()
    for row in rows:
        row["snapshot"].projected_points_5 = None
        row["snapshot"].points_per_game = None
        row["snapshot"].expected_minutes = None

    summary = template_summaries(rows, 100.0)

    assert summary["templates"] == []
    assert summary["checks"] is not None
    failed = [check["name"] for check in summary["checks"] if not check["passed"]]
    assert "projections_available" in failed
    assert "objective_variation" in failed
    assert summary["activates_when"]


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
