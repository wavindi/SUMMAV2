import backend_pi as backend
from concurrent.futures import ThreadPoolExecutor


def add(team, count=1):
    for _ in range(count):
        assert backend.process_add_point(team)["success"] is True


def win_game(team):
    add(team, 4)


def win_set(team):
    for _ in range(6):
        win_game(team)


def test_golden_point_progression_and_game_win():
    add("black", 3)
    assert (backend.gamestate["score1"], backend.gamestate["game1"]) == (40, 0)
    add("yellow", 3)
    add("black")
    assert (backend.gamestate["score1"], backend.gamestate["score2"]) == (0, 0)
    assert backend.gamestate["game1"] == 1


def test_advantage_returns_to_deuce_then_wins_by_two():
    backend.scoring_rules["deuce_mode"] = "advantage"
    add("black", 3)
    add("yellow", 3)
    add("black")
    assert (backend.gamestate["score1"], backend.gamestate["score2"]) == (45, 40)
    add("yellow")
    assert (backend.gamestate["score1"], backend.gamestate["score2"]) == (40, 40)
    add("black", 2)
    assert backend.gamestate["game1"] == 1


def test_deciding_super_tiebreak_starts_immediately_at_one_set_each():
    win_set("black")
    win_set("yellow")
    assert (backend.gamestate["set1"], backend.gamestate["set2"]) == (1, 1)
    assert backend.gamestate["mode"] == "supertiebreak"
    assert (backend.gamestate["game1"], backend.gamestate["game2"]) == (0, 0)


def test_completed_match_is_persisted_once_with_winning_game(clean_backend_state):
    win_set("black")
    win_set("black")
    assert backend.gamestate["matchwon"] is True
    assert backend.gamestate["winner"]["team"] == "black"
    assert len(clean_backend_state) == 1
    stats = backend.match_storage["matchdata"]
    assert stats["finalsetsscore"] == "2-0"
    assert stats["totalgameswon"]["black"] == 12


def test_tiebreak_requires_target_and_two_point_lead():
    backend.gamestate["mode"] = "tiebreak"
    add("black", 6)
    add("yellow", 6)
    add("black")
    assert backend.gamestate["set1"] == 0
    add("black")
    assert backend.gamestate["set1"] == 1
    assert backend.gamestate["sethistory"] == ["7-6(6)"]


def test_remote_event_auth_and_idempotency():
    client = backend.app.test_client()
    payload = {"action": "addpoint", "team": "black", "event_id": "remote-1"}

    denied = client.post("/remote_event", json=payload,
                         environ_overrides={"REMOTE_ADDR": "192.168.1.50"})
    assert denied.status_code == 401

    headers = {"Authorization": "Bearer test-token"}
    first = client.post("/remote_event", json=payload, headers=headers,
                        environ_overrides={"REMOTE_ADDR": "192.168.1.50"})
    duplicate = client.post("/remote_event", json=payload, headers=headers,
                            environ_overrides={"REMOTE_ADDR": "192.168.1.50"})
    assert first.status_code == 200
    assert duplicate.status_code == 200
    assert duplicate.get_json()["deduped"] is True
    assert backend.gamestate["point1"] == 1


def test_local_kiosk_can_control_without_embedding_secret():
    client = backend.app.test_client()
    response = client.post("/setgamemode", json={"mode": "competition"})
    assert response.status_code == 200
    assert backend.gamestate["gamemode"] == "competition"


def test_static_route_exposes_assets_but_not_source_or_database():
    client = backend.app.test_client()
    assert client.get("/padel_css.css").status_code == 200
    assert client.get("/splash.jpg").status_code == 200
    assert client.get("/backend_pi.py").status_code == 404
    assert client.get("/padel_matches.db").status_code == 404


def test_browser_wipe_immediately_false_is_honoured():
    client = backend.app.test_client()
    backend.match_storage["matchcompleted"] = True
    response = client.post("/markmatchdisplayed", json={"wipe_immediately": False})
    assert response.status_code == 200
    assert backend.match_storage["matchcompleted"] is True


def test_health_and_invalid_remote_payloads():
    client = backend.app.test_client()
    assert client.get("/health").status_code == 200
    headers = {"Authorization": "Bearer test-token"}
    assert client.post("/remote_event", json={"action": "dance", "event_id": "x"},
                       headers=headers).status_code == 400
    assert client.post("/remote_event", json={"action": "addpoint", "team": "blue",
                                               "event_id": "y"}, headers=headers).status_code == 400


def test_simultaneous_duplicate_radio_delivery_counts_once():
    payload = {"action": "addpoint", "team": "black", "event_id": "radio-retry-42"}
    headers = {"Authorization": "Bearer test-token"}

    def send_once(_):
        with backend.app.test_client() as client:
            return client.post("/remote_event", json=payload, headers=headers).get_json()

    with ThreadPoolExecutor(max_workers=8) as pool:
        results = list(pool.map(send_once, range(16)))
    assert backend.gamestate["point1"] == 1
    assert sum(not item.get("deduped", False) for item in results) == 1


def test_two_remotes_alternate_without_cross_team_corruption():
    client = backend.app.test_client()
    headers = {"Authorization": "Bearer test-token"}
    for sequence, team in enumerate(["black", "yellow"] * 3, start=1):
        response = client.post(
            "/remote_event",
            json={"action": "addpoint", "team": team,
                  "event_id": f"remote-{team}-{sequence}"},
            headers=headers,
        )
        assert response.status_code == 200
    assert (backend.gamestate["point1"], backend.gamestate["point2"]) == (3, 3)
    assert (backend.gamestate["score1"], backend.gamestate["score2"]) == (40, 40)


def test_subtract_point_and_remote_reset():
    client = backend.app.test_client()
    headers = {"Authorization": "Bearer test-token"}
    add("black", 2)
    result = backend.process_subtract_point("black")
    assert result["success"] is True
    assert (backend.gamestate["point1"], backend.gamestate["score1"]) == (1, 15)
    reset = client.post("/remote_event", json={"action": "reset", "event_id": "reset-1"},
                        headers=headers)
    assert reset.status_code == 200
    assert backend.gamestate["gamemode"] is None
    assert backend.gamestate["point1"] == backend.gamestate["game1"] == 0


def test_undo_after_set_point_restores_set_and_game_state():
    win_set("black")
    result = backend.process_subtract_point("black")
    assert result["success"] is True
    assert backend.gamestate["set1"] == 0
    assert backend.gamestate["sethistory"] == []
    assert backend.gamestate["game1"] == 5
    assert backend.gamestate["score1"] == 40


def test_six_all_enters_tiebreak_and_switches_after_six_points():
    for _ in range(5):
        win_game("black")
        win_game("yellow")
    win_game("black")
    win_game("yellow")
    assert backend.gamestate["mode"] == "tiebreak"
    for team in ["black", "yellow"] * 3:
        response = backend.process_add_point(team)
    assert response["sideswitch"]["required"] is True
    assert (backend.gamestate["score1"], backend.gamestate["score2"]) == (3, 3)


def test_undo_after_tiebreak_set_restores_tiebreak_score():
    backend.gamestate.update({"mode": "tiebreak", "game1": 6, "game2": 6})
    for team in ["black", "yellow"] * 5 + ["black", "black"]:
        add(team)
    assert backend.gamestate["set1"] == 1
    result = backend.process_subtract_point("black")
    assert result["success"] is True
    assert backend.gamestate["mode"] == "tiebreak"
    assert (backend.gamestate["set1"], backend.gamestate["set2"]) == (0, 0)
    assert (backend.gamestate["game1"], backend.gamestate["game2"]) == (6, 6)
    assert (backend.gamestate["score1"], backend.gamestate["score2"]) == (6, 5)
