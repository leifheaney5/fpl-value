from types import SimpleNamespace

from app.services.team_recommender import recommend_team


def test_recommender_builds_valid_fpl_squad_under_budget():
    rows = []
    positions = [("GKP", 2), ("DEF", 5), ("MID", 5), ("FWD", 3)]
    player_id = 1
    for position, count in positions:
        for index in range(count):
            rows.append({
                "player": SimpleNamespace(id=player_id, full_name=f"Player {player_id}", position_short=position),
                "team": SimpleNamespace(id=(player_id % 6) + 1, short_name=f"T{player_id % 6}"),
                "snapshot": SimpleNamespace(
                    price=5.0 + (index * 0.2), projected_points_5=25.0 - index,
                    total_points=100 - index, reliable_value=10.0 - index / 10,
                    forward_value=8.0 - index / 10, availability_factor=1.0,
                    rotation_risk=10.0,
                ),
            })
            player_id += 1

    result = recommend_team(rows, 100.0)
    selected = result["starting"] + result["bench"]

    assert len(selected) == 15
    assert len(result["starting"]) == 11
    assert len(result["bench"]) == 4
    assert result["spent"] <= 100.0
    assert {item["row"]["player"].position_short for item in selected} == {"GKP", "DEF", "MID", "FWD"}
    club_counts = {}
    for item in selected:
        club_id = item["row"]["team"].id
        club_counts[club_id] = club_counts.get(club_id, 0) + 1
    assert max(club_counts.values()) <= 3
    assert recommend_team(rows, 100.0, "safe")["strategy"] == "safe"
    assert recommend_team(rows, 100.0, "differential")["strategy"] == "differential"
