#!/usr/bin/env python3
"""
SUMMA Padel Scoreboard Backend - V2

Forked from SUMMAV1-1/padel_backend.py. Changes:
  - ESP32-C3 nodes POST directly to /addpoint /subtractpoint /resetmatch
  - New POST /sensor_heartbeat stores node liveness + rebroadcasts to UI
  - Bearer-token auth on sensor-origin POSTs
  - threading.Lock around every gamestate mutation
  - 60s idempotency cache keyed by event_id (kills retry double-counts)
  - No pigpio / smbus2 / pygame — audio moves to the browser
  - Env-driven config (see .env.example)
"""

import os
import json
import time
import logging
import threading
from collections import OrderedDict
from datetime import datetime
from functools import wraps

from flask import Flask, request, jsonify, send_from_directory
from flask_cors import CORS
from flask_socketio import SocketIO, emit

import store  # SQLite match persistence (stdlib only)

# ---------------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------------
HOST = os.environ.get("SUMMA_HOST", "0.0.0.0")
PORT = int(os.environ.get("SUMMA_PORT", "5000"))
NODE_TOKEN = os.environ.get("SUMMA_NODE_TOKEN", "change-me-in-env")
LOG_LEVEL = os.environ.get("SUMMA_LOG_LEVEL", "INFO").upper()
HEARTBEAT_STALE_S = 6  # node considered offline after this many seconds
DB_PATH = os.environ.get(
    "SUMMA_DB_PATH",
    os.path.join(os.path.dirname(os.path.abspath(__file__)), "padel_matches.db"),
)
store.init_db(DB_PATH)

# Scoring rules — runtime-configurable via POST /setscoringrules.
# Defaults match official padel: golden point at 40-40, match-tiebreak to 10,
# ends change every 6 points inside a tiebreak.
scoring_rules = {
    "deuce_mode": "golden_point",        # "golden_point" | "advantage"
    "tiebreak_target": 7,                # first to N (with 2-lead) wins tiebreak
    "supertiebreak_target": 10,          # first to N (with 2-lead) wins match-TB
    "tiebreak_side_switch_every": 6,     # broadcast CHANGE SIDES at these intervals
}

logging.basicConfig(level=LOG_LEVEL, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
log = logging.getLogger("summa")
logging.getLogger("werkzeug").setLevel(logging.ERROR)

app = Flask(__name__)
CORS(app, cors_allowed_origins="*")
socketio = SocketIO(
    app,
    cors_allowed_origins="*",
    async_mode="threading",
    logger=False,
    engineio_logger=False,
    ping_timeout=60,
    ping_interval=25,
)

# ---------------------------------------------------------------------------
# State
# ---------------------------------------------------------------------------
state_lock = threading.Lock()

gamestate = {
    "game1": 0, "game2": 0,
    "set1": 0, "set2": 0,
    "point1": 0, "point2": 0,
    "score1": 0, "score2": 0,
    "matchwon": False,
    "winner": None,
    "sethistory": [],
    "matchhistory": [],
    "matchstarttime": datetime.now().isoformat(),
    "matchendtime": None,
    "lastupdated": datetime.now().isoformat(),
    "shouldswitchsides": False,
    "totalgamesinset": 0,
    "initialswitchdone": False,
    "mode": "normal",
    "gamemode": None,
    "lastgamestate": None,
    "lastsideswitch": None,
}

match_storage = {
    "matchcompleted": False,
    "matchdata": {
        "winnerteam": None, "winnername": None, "finalsetsscore": None,
        "detailedsets": [], "matchduration": None,
        "totalpointswon": {"black": 0, "yellow": 0},
        "totalgameswon": {"black": 0, "yellow": 0},
        "setsbreakdown": [], "matchsummary": None,
    },
    "displayshown": False,
}

# ESP32 node → team mapping (can be swapped at runtime)
sensor_mapping = {
    "xiao-black": "black",
    "xiao-yellow": "yellow",
    "last_swap": None,
}

heartbeats = {}  # node_id → {team, rssi, vbat_mv, uptime_s, fw, state, last_seen}

# Idempotency cache: event_id → (timestamp, response_dict)
IDEMPOTENCY_TTL_S = 60
idempotency_cache = OrderedDict()


def remember_event(event_id, response):
    if not event_id:
        return
    now = time.time()
    idempotency_cache[event_id] = (now, response)
    # Prune old entries
    while idempotency_cache:
        k, (t, _) = next(iter(idempotency_cache.items()))
        if now - t > IDEMPOTENCY_TTL_S:
            idempotency_cache.popitem(last=False)
        else:
            break


def recall_event(event_id):
    if not event_id:
        return None
    hit = idempotency_cache.get(event_id)
    if not hit:
        return None
    t, response = hit
    if time.time() - t > IDEMPOTENCY_TTL_S:
        idempotency_cache.pop(event_id, None)
        return None
    return response


# ---------------------------------------------------------------------------
# Auth
# ---------------------------------------------------------------------------
def sensor_auth_required(fn):
    """Allow either a valid bearer token (sensor origin) or a same-origin UI request."""

    @wraps(fn)
    def wrapper(*args, **kwargs):
        auth = request.headers.get("Authorization", "")
        source = (request.get_json(silent=True) or {}).get("source")
        is_sensor_call = auth.startswith("Bearer ") or source == "esp32"
        if is_sensor_call:
            token = auth.split(" ", 1)[1] if auth.startswith("Bearer ") else ""
            if token != NODE_TOKEN:
                return jsonify({"success": False, "error": "invalid token"}), 401
        return fn(*args, **kwargs)

    return wrapper


# ---------------------------------------------------------------------------
# Scoring engine — forked 1:1 from V1, no logic changes, only guarded by lock
# ---------------------------------------------------------------------------
def triggerbasicmodesideswitchifneeded():
    if gamestate["matchwon"]:
        return
    if gamestate["gamemode"] != "basic":
        return
    totalgames = gamestate["game1"] + gamestate["game2"]
    set1, set2 = gamestate["set1"], gamestate["set2"]
    totalsets = set1 + set2
    if totalsets == 0 and totalgames == 0:
        return
    if totalgames == 0 and totalsets in [1, 2] and not gamestate.get("initialswitchdone", False):
        gamestate["initialswitchdone"] = True
        gamestate["shouldswitchsides"] = True
        gamestate["totalgamesinset"] = 0
        gamestate["lastsideswitch"] = {
            "games": (gamestate["game1"], gamestate["game2"]),
            "sets": (gamestate["set1"], gamestate["set2"]),
            "timestamp": datetime.now().isoformat(),
        }
        broadcastsideswitch()


def check_side_switch():
    if gamestate["matchwon"]:
        return False
    totalgames = gamestate["game1"] + gamestate["game2"]
    if gamestate["gamemode"] == "basic":
        return False
    if totalgames % 2 == 1:
        gamestate["shouldswitchsides"] = True
        gamestate["totalgamesinset"] = totalgames
        gamestate["lastsideswitch"] = {
            "games": (gamestate["game1"], gamestate["game2"]),
            "sets": (gamestate["set1"], gamestate["set2"]),
            "timestamp": datetime.now().isoformat(),
        }
        return True
    gamestate["shouldswitchsides"] = False
    return False


def undo_side_switch_if_needed():
    if not gamestate.get("lastsideswitch"):
        return False
    last = gamestate["lastsideswitch"]
    if (gamestate["set1"], gamestate["set2"]) != last["sets"]:
        gamestate["lastsideswitch"] = None
        gamestate["shouldswitchsides"] = True
        gamestate["initialswitchdone"] = False
        broadcastsideswitch()
        return True
    if gamestate["gamemode"] in ["competition", "lock"]:
        switchtotal = sum(last["games"])
        currenttotal = gamestate["game1"] + gamestate["game2"]
        if switchtotal % 2 == 1 and currenttotal % 2 == 0:
            gamestate["lastsideswitch"] = None
            gamestate["shouldswitchsides"] = True
            broadcastsideswitch()
            return True
    return False


def broadcast_gamestate():
    socketio.emit("gamestateupdate", gamestate, namespace="/")


def broadcast_point_scored(team, actiontype):
    socketio.emit(
        "pointscored",
        {"team": team, "action": actiontype, "gamestate": gamestate, "timestamp": datetime.now().isoformat()},
        namespace="/",
    )


def broadcastsideswitch():
    if gamestate["matchwon"]:
        return
    data = {
        "totalgames": gamestate["totalgamesinset"],
        "gamescore": f"{gamestate['game1']}-{gamestate['game2']}",
        "setscore": f"{gamestate['set1']}-{gamestate['set2']}",
        "message": "CHANGE SIDES",
        "timestamp": datetime.now().isoformat(),
    }
    socketio.emit("sideswitchrequired", data, namespace="/")
    socketio.emit("play_change_audio", {}, namespace="/")  # client-side plays change.mp3
    log.info("Side switch broadcast: %s", data)


def broadcast_match_won():
    socketio.emit(
        "matchwon",
        {"winner": gamestate["winner"], "matchdata": match_storage["matchdata"], "timestamp": datetime.now().isoformat()},
        namespace="/",
    )


def add_to_history(action, team, sb, sa, gb, ga, seb, sea):
    gamestate["matchhistory"].append({
        "timestamp": datetime.now().isoformat(),
        "action": action, "team": team,
        "scores": {"before": {"score1": sb[0], "score2": sb[1]}, "after": {"score1": sa[0], "score2": sa[1]}},
        "games": {"before": {"game1": gb[0], "game2": gb[1]}, "after": {"game1": ga[0], "game2": ga[1]}},
        "sets": {"before": {"set1": seb[0], "set2": seb[1]}, "after": {"set1": sea[0], "set2": sea[1]}},
    })


def calculate_match_statistics():
    bp = sum(1 for h in gamestate["matchhistory"] if h["action"] == "point" and h["team"] == "black")
    yp = sum(1 for h in gamestate["matchhistory"] if h["action"] == "point" and h["team"] == "yellow")
    bg = sum(1 for h in gamestate["matchhistory"] if h["action"] == "game" and h["team"] == "black")
    yg = sum(1 for h in gamestate["matchhistory"] if h["action"] == "game" and h["team"] == "yellow")
    breakdown = []
    for i, s in enumerate(gamestate["sethistory"], 1):
        if "-" in s:
            b = int(s.split("-")[0].split("(")[0])
            y = int(s.split("-")[1].split("(")[0])
            breakdown.append({"setnumber": i, "blackgames": b, "yellowgames": y, "setwinner": "black" if b > y else "yellow"})
    return {"totalpoints": {"black": bp, "yellow": yp}, "totalgames": {"black": bg, "yellow": yg}, "setsbreakdown": breakdown}


def store_match_data():
    if not gamestate["matchwon"] or not gamestate["winner"]:
        return
    stats = calculate_match_statistics()
    start = datetime.fromisoformat(gamestate["matchstarttime"])
    end = datetime.fromisoformat(gamestate["matchendtime"])
    dur_s = int((end - start).total_seconds())
    dur_txt = f"{dur_s // 60}m {dur_s % 60}s" if dur_s >= 60 else f"{dur_s}s"
    sets_disp = [f"{b['blackgames']}-{b['yellowgames']}" for b in stats["setsbreakdown"]]
    match_storage["matchcompleted"] = True
    match_storage["matchdata"] = {
        "winnerteam": gamestate["winner"]["team"],
        "winnername": gamestate["winner"]["teamname"],
        "finalsetsscore": gamestate["winner"]["finalsets"],
        "detailedsets": sets_disp,
        "matchduration": dur_txt,
        "totalpointswon": stats["totalpoints"],
        "totalgameswon": stats["totalgames"],
        "setsbreakdown": stats["setsbreakdown"],
        "matchsummary": f"Sets: {', '.join(sets_disp)} | Points: {stats['totalpoints']['black']}-{stats['totalpoints']['yellow']} | Games: {stats['totalgames']['black']}-{stats['totalgames']['yellow']}",
        "timestamp": gamestate["matchendtime"],
    }
    match_storage["displayshown"] = False

    # Persist to SQLite so the match survives a backend restart. Never let a
    # DB hiccup crash the match-completion flow.
    try:
        row_id = store.save_match({
            "started_at": gamestate.get("matchstarttime"),
            "ended_at":   gamestate.get("matchendtime"),
            "winner":     json.dumps(gamestate.get("winner")),
            "sets_json":  json.dumps(sets_disp),
            "stats_json": json.dumps(match_storage["matchdata"]),
            "mode":       gamestate.get("gamemode"),
        })
        log.info("match persisted to SQLite (id=%s)", row_id)
    except Exception as e:
        log.warning("store.save_match failed: %s", e)


def wipe_match_storage():
    match_storage["matchcompleted"] = False
    match_storage["matchdata"] = {
        "winnerteam": None, "winnername": None, "finalsetsscore": None,
        "detailedsets": [], "matchduration": None,
        "totalpointswon": {"black": 0, "yellow": 0},
        "totalgameswon": {"black": 0, "yellow": 0},
        "setsbreakdown": [], "matchsummary": None,
    }
    match_storage["displayshown"] = False


def check_set_winner():
    g1, g2 = gamestate["game1"], gamestate["game2"]
    s1, s2 = gamestate["set1"], gamestate["set2"]

    def _finish_set(winner):
        setbefore = (s1, s2)
        if winner == "black":
            gamestate["set1"] += 1
        else:
            gamestate["set2"] += 1
        gamestate["sethistory"].append(f"{g1}-{g2}")
        add_to_history("set", winner,
                       (gamestate["score1"], gamestate["score2"]), (0, 0),
                       (g1, g2), (0, 0),
                       setbefore, (gamestate["set1"], gamestate["set2"]))
        gamestate["lastgamestate"] = {
            "game1": g1, "game2": g2,
            "point1": gamestate["point1"], "point2": gamestate["point2"],
            "score1": gamestate["score1"], "score2": gamestate["score2"],
            "winner": winner,
        }
        gamestate["game1"] = 0
        gamestate["game2"] = 0
        gamestate["totalgamesinset"] = 0
        gamestate["shouldswitchsides"] = False
        gamestate["initialswitchdone"] = False
        matchwon = check_match_winner()
        if not matchwon:
            triggerbasicmodesideswitchifneeded()
        return matchwon

    if g1 >= 6 and (g1 - g2) >= 2:
        return _finish_set("black")
    if g2 >= 6 and (g2 - g1) >= 2:
        return _finish_set("yellow")
    if g1 == 6 and g2 == 6 and gamestate["mode"] == "normal":
        if (s1 == 0 and s2 == 0) or (s1 == 1 and s2 == 0) or (s1 == 0 and s2 == 1):
            gamestate["mode"] = "tiebreak"
        elif s1 == 1 and s2 == 1:
            gamestate["mode"] = "supertiebreak"
        gamestate["point1"] = 0
        gamestate["point2"] = 0
        gamestate["score1"] = 0
        gamestate["score2"] = 0
    return False


def check_match_winner():
    if gamestate["set1"] >= 2 or gamestate["set2"] >= 2:
        winner = "black" if gamestate["set1"] >= 2 else "yellow"
        gamestate["matchwon"] = True
        gamestate["matchendtime"] = datetime.now().isoformat()
        total_b = sum(int(s.split("-")[0].split("(")[0]) for s in gamestate["sethistory"] if "-" in s) + gamestate["game1"]
        total_y = sum(int(s.split("-")[1].split("(")[0]) for s in gamestate["sethistory"] if "-" in s) + gamestate["game2"]
        gamestate["winner"] = {
            "team": winner,
            "teamname": f"{winner.upper()} TEAM",
            "finalsets": f"{gamestate['set1']}-{gamestate['set2']}",
            "matchsummary": ", ".join(gamestate["sethistory"]),
            "totalgameswon": total_b if winner == "black" else total_y,
            "matchduration": calculate_match_duration(),
        }
        add_to_history("match", winner,
                       (gamestate["score1"], gamestate["score2"]), (gamestate["score1"], gamestate["score2"]),
                       (gamestate["game1"], gamestate["game2"]), (gamestate["game1"], gamestate["game2"]),
                       (gamestate["set1"], gamestate["set2"]), (gamestate["set1"], gamestate["set2"]))
        store_match_data()
        return True
    return False


def calculate_match_duration():
    if gamestate["matchendtime"]:
        s = datetime.fromisoformat(gamestate["matchstarttime"])
        e = datetime.fromisoformat(gamestate["matchendtime"])
        return f"{int((e - s).total_seconds() // 60)} minutes"
    return "In progress"


def set_normal_score_from_points():
    """Map point counter to the displayed tennis score, honouring advantage mode.

    Golden-point mode: no 'Ad' — 40-40 stays 40-40 and the next point wins.
    Advantage mode: after 40-40, the leading side displays 'Ad' (encoded as 45)
    and the trailing side 40; losing the next point returns both to 40.
    """
    p1, p2 = gamestate["point1"], gamestate["point2"]
    base = lambda p: 0 if p == 0 else 15 if p == 1 else 30 if p == 2 else 40

    if scoring_rules["deuce_mode"] == "advantage" and (p1 >= 3 and p2 >= 3):
        if p1 == p2:
            gamestate["score1"] = gamestate["score2"] = 40
        elif p1 == p2 + 1:
            gamestate["score1"], gamestate["score2"] = 45, 40  # Ad-in
        elif p2 == p1 + 1:
            gamestate["score1"], gamestate["score2"] = 40, 45  # Ad-out
        else:
            gamestate["score1"], gamestate["score2"] = base(p1), base(p2)
    else:
        gamestate["score1"], gamestate["score2"] = base(p1), base(p2)


def _normal_game_winner():
    """Return 'black' / 'yellow' if the current points win the game, else None.

    Golden-point (padel default): first side to reach 4 points wins — at 40-40
    the next point takes the game with no 2-point margin required.
    Advantage: classical tennis — need 4+ points AND a 2-point lead.
    """
    p1, p2 = gamestate["point1"], gamestate["point2"]
    if scoring_rules["deuce_mode"] == "golden_point":
        if p1 >= 4 and p1 > p2:
            return "black"
        if p2 >= 4 and p2 > p1:
            return "yellow"
        return None
    # advantage
    if p1 >= 4 and (p1 - p2) >= 2:
        return "black"
    if p2 >= 4 and (p2 - p1) >= 2:
        return "yellow"
    return None


def reset_points():
    gamestate["point1"] = gamestate["point2"] = 0
    gamestate["score1"] = gamestate["score2"] = 0


def handle_normal_game_win(team):
    gamestate["lastgamestate"] = {
        "game1": gamestate["game1"], "game2": gamestate["game2"],
        "point1": gamestate["point1"], "point2": gamestate["point2"],
        "score1": gamestate["score1"], "score2": gamestate["score2"],
        "winner": team,
    }
    if team == "black":
        gamestate["game1"] += 1
    else:
        gamestate["game2"] += 1
    reset_points()
    check_set_winner()


def handletiebreakwin(team):
    g1, g2 = gamestate["game1"], gamestate["game2"]
    setbefore = (gamestate["set1"], gamestate["set2"])
    tbscore = gamestate["point2"] if team == "black" else gamestate["point1"]
    if team == "black":
        gamestate["set1"] += 1
        gamestate["sethistory"].append(f"7-6({tbscore})")
    else:
        gamestate["set2"] += 1
        gamestate["sethistory"].append(f"6-7({tbscore})")
    add_to_history("set", team,
                   (gamestate["score1"], gamestate["score2"]), (0, 0),
                   (g1, g2), (0, 0),
                   setbefore, (gamestate["set1"], gamestate["set2"]))
    gamestate["game1"] = 0
    gamestate["game2"] = 0
    gamestate["totalgamesinset"] = 0
    gamestate["shouldswitchsides"] = False
    gamestate["initialswitchdone"] = False
    reset_points()
    gamestate["mode"] = "normal"
    if not check_match_winner():
        triggerbasicmodesideswitchifneeded()


def handle_supertiebreak_win(team):
    setbefore = (gamestate["set1"], gamestate["set2"])
    if team == "black":
        gamestate["set1"] += 1
        gamestate["sethistory"].append(f"10-{gamestate['point2']}(STB)")
    else:
        gamestate["set2"] += 1
        gamestate["sethistory"].append(f"{gamestate['point1']}-10(STB)")
    add_to_history("set", team,
                   (gamestate["score1"], gamestate["score2"]), (0, 0),
                   (gamestate["game1"], gamestate["game2"]), (0, 0),
                   setbefore, (gamestate["set1"], gamestate["set2"]))
    gamestate["initialswitchdone"] = False
    reset_points()
    gamestate["mode"] = "normal"
    check_match_winner()


def scoring_game_mode_selected():
    return gamestate["gamemode"] in ["basic", "competition", "lock"]


def _maybe_tiebreak_side_switch(total_points):
    """Inside a (super-)tiebreak, broadcast CHANGE SIDES every N points.

    N comes from scoring_rules["tiebreak_side_switch_every"] (default 6, the
    ITF/FIP rule). Silent at 0-0 and when the configured interval is 0/None.
    """
    every = scoring_rules.get("tiebreak_side_switch_every") or 0
    if every <= 0 or total_points == 0:
        return
    if total_points % every == 0:
        gamestate["shouldswitchsides"] = True
        gamestate["totalgamesinset"] = total_points  # reused as display counter
        gamestate["lastsideswitch"] = {
            "games": (gamestate["game1"], gamestate["game2"]),
            "sets": (gamestate["set1"], gamestate["set2"]),
            "timestamp": datetime.now().isoformat(),
        }
        broadcastsideswitch()


def process_add_point(team):
    if not scoring_game_mode_selected():
        broadcast_point_scored(team, "addpoint")
        return {"success": True, "ignored": True, "message": "Mode not selected", "gamestate": gamestate}
    if gamestate["matchwon"]:
        return {"success": False, "error": "Match completed", "winner": gamestate["winner"], "matchwon": True}

    sb = (gamestate["score1"], gamestate["score2"])
    gb = (gamestate["game1"], gamestate["game2"])
    seb = (gamestate["set1"], gamestate["set2"])
    action = "point"
    game_just_won = False
    phase = gamestate["mode"]

    if team == "black":
        gamestate["point1"] += 1
    else:
        gamestate["point2"] += 1
    p1, p2 = gamestate["point1"], gamestate["point2"]

    if phase == "normal":
        set_normal_score_from_points()
        winner = _normal_game_winner()
        if winner:
            handle_normal_game_win(winner); action = "game"; game_just_won = True
    elif phase == "tiebreak":
        gamestate["score1"], gamestate["score2"] = p1, p2
        tb_target = scoring_rules["tiebreak_target"]
        if team == "black" and p1 >= tb_target and (p1 - p2) >= 2:
            handletiebreakwin("black"); action = "set"
        elif team == "yellow" and p2 >= tb_target and (p2 - p1) >= 2:
            handletiebreakwin("yellow"); action = "set"
        else:
            _maybe_tiebreak_side_switch(p1 + p2)
    elif phase == "supertiebreak":
        gamestate["score1"], gamestate["score2"] = p1, p2
        stb_target = scoring_rules["supertiebreak_target"]
        if team == "black" and p1 >= stb_target and (p1 - p2) >= 2:
            handle_supertiebreak_win("black"); action = "set"
        elif team == "yellow" and p2 >= stb_target and (p2 - p1) >= 2:
            handle_supertiebreak_win("yellow"); action = "set"
        else:
            _maybe_tiebreak_side_switch(p1 + p2)

    if not gamestate["matchwon"]:
        add_to_history(action, team, sb, (gamestate["score1"], gamestate["score2"]),
                       gb, (gamestate["game1"], gamestate["game2"]),
                       seb, (gamestate["set1"], gamestate["set2"]))

    gamestate["lastupdated"] = datetime.now().isoformat()

    if game_just_won and not gamestate["matchwon"] and gamestate["mode"] == "normal":
        if check_side_switch():
            broadcastsideswitch()

    broadcast_gamestate()
    if gamestate["matchwon"]:
        broadcast_match_won()
    else:
        broadcast_point_scored(team, action)

    resp = {
        "success": True,
        "message": f"Point added to {team}",
        "gamestate": gamestate,
        "matchwon": gamestate["matchwon"],
        "winner": gamestate["winner"] if gamestate["matchwon"] else None,
    }
    if gamestate["shouldswitchsides"]:
        resp["sideswitch"] = {
            "required": True,
            "totalgames": gamestate["totalgamesinset"],
            "gamescore": f"{gamestate['game1']}-{gamestate['game2']}",
            "setscore": f"{gamestate['set1']}-{gamestate['set2']}",
        }
        gamestate["shouldswitchsides"] = False
    return resp


def process_subtract_point(team):
    if not scoring_game_mode_selected():
        broadcast_point_scored(team, "subtractpoint")
        return {"success": True, "ignored": True, "message": "Mode not selected", "gamestate": gamestate}
    if gamestate["matchwon"]:
        return {"success": False, "error": "Match completed"}

    sb = (gamestate["score1"], gamestate["score2"])
    gb = (gamestate["game1"], gamestate["game2"])
    seb = (gamestate["set1"], gamestate["set2"])

    if gamestate["point1"] == 0 and gamestate["point2"] == 0:
        if not gamestate.get("lastgamestate"):
            return {"success": False, "error": "No previous game state"}
        side_switch_undone = undo_side_switch_if_needed()
        last = gamestate["lastgamestate"]
        gamestate.update({
            "game1": last["game1"], "game2": last["game2"],
            "point1": last["point1"], "point2": last["point2"],
            "score1": last["score1"], "score2": last["score2"],
        })
        winner = last.get("winner") or team
        if winner == "black":
            gamestate["point1"] = max(0, gamestate["point1"] - 1)
        else:
            gamestate["point2"] = max(0, gamestate["point2"] - 1)
        if gamestate["mode"] == "normal":
            set_normal_score_from_points()
        else:
            gamestate["score1"], gamestate["score2"] = gamestate["point1"], gamestate["point2"]
        gamestate["lastgamestate"] = None
        gamestate["totalgamesinset"] = gamestate["game1"] + gamestate["game2"]
        if not side_switch_undone:
            gamestate["shouldswitchsides"] = False
        add_to_history("point_undo", winner, sb, (gamestate["score1"], gamestate["score2"]),
                       gb, (gamestate["game1"], gamestate["game2"]),
                       seb, (gamestate["set1"], gamestate["set2"]))
        gamestate["lastupdated"] = datetime.now().isoformat()
        broadcast_gamestate()
        return {"success": True, "message": f"Game undone, subtracted from {winner}", "gamestate": gamestate}

    if team == "black":
        gamestate["point1"] = max(0, gamestate["point1"] - 1)
    else:
        gamestate["point2"] = max(0, gamestate["point2"] - 1)
    if gamestate["mode"] == "normal":
        set_normal_score_from_points()
    else:
        gamestate["score1"], gamestate["score2"] = gamestate["point1"], gamestate["point2"]
    add_to_history("point_subtract", team, sb, (gamestate["score1"], gamestate["score2"]),
                   gb, (gamestate["game1"], gamestate["game2"]),
                   seb, (gamestate["set1"], gamestate["set2"]))
    gamestate["lastupdated"] = datetime.now().isoformat()
    broadcast_gamestate()
    return {"success": True, "message": f"Point subtracted from {team}", "gamestate": gamestate}


# ---------------------------------------------------------------------------
# Socket.IO
# ---------------------------------------------------------------------------
@socketio.on("connect")
def _on_connect():
    log.info("UI client connected: %s", request.sid)
    emit("gamestateupdate", gamestate)
    emit("sensor_heartbeat", heartbeats)
    if gamestate["gamemode"] == "basic":
        with state_lock:
            triggerbasicmodesideswitchifneeded()


@socketio.on("request_gamestate")
def _on_request_state():
    emit("gamestateupdate", gamestate)


# ---------------------------------------------------------------------------
# Routes
# ---------------------------------------------------------------------------
@app.route("/")
def _root():
    return send_from_directory(".", "padel_scoreboard.html")


@app.route("/<path:filename>")
def _static(filename):
    if os.path.exists(filename):
        return send_from_directory(".", filename)
    return f"File {filename} not found", 404


def _team_from_payload(data):
    """Resolve team from payload: explicit team OR node_id via sensor_mapping."""
    team = data.get("team")
    if team in ("black", "yellow"):
        return team
    node_id = data.get("node_id")
    if node_id and node_id in sensor_mapping:
        return sensor_mapping[node_id]
    return "black"  # last-resort default preserves V1 behaviour


def _handle_event(processor):
    data = request.get_json(silent=True) or {}
    event_id = data.get("event_id")
    cached = recall_event(event_id)
    if cached is not None:
        return jsonify({**cached, "deduped": True}), 200
    team = _team_from_payload(data)
    with state_lock:
        result = processor(team)
    remember_event(event_id, result)
    return jsonify(result), (200 if result.get("success") else 400)


@app.route("/addpoint", methods=["POST"])
@sensor_auth_required
def _addpoint():
    return _handle_event(process_add_point)


@app.route("/subtractpoint", methods=["POST"])
@sensor_auth_required
def _subtractpoint():
    return _handle_event(process_subtract_point)


@app.route("/gamestate", methods=["GET"])
def _gamestate():
    with state_lock:
        r = dict(gamestate)
    r["matchstorage_available"] = match_storage["matchcompleted"] and not match_storage["displayshown"]
    return jsonify(r)


@app.route("/getmatchdata", methods=["GET"])
def _getmatchdata():
    if not match_storage["matchcompleted"]:
        return jsonify({"success": False, "error": "No completed match"}), 404
    return jsonify({"success": True, "matchdata": match_storage["matchdata"], "displayshown": match_storage["displayshown"]})


@app.route("/markmatchdisplayed", methods=["POST"])
def _markdisplayed():
    if not match_storage["matchcompleted"]:
        return jsonify({"success": False, "error": "No match data"}), 400
    match_storage["displayshown"] = True
    wipe = (request.get_json(silent=True) or {}).get("wipeimmediately", True)
    if wipe:
        wipe_match_storage()
    return jsonify({"success": True})


@app.route("/scoringrules", methods=["GET"])
def _get_scoringrules():
    return jsonify({"success": True, "rules": scoring_rules})


@app.route("/setscoringrules", methods=["POST"])
def _set_scoringrules():
    data = request.get_json(silent=True) or {}
    errors = []
    if "deuce_mode" in data:
        if data["deuce_mode"] not in ("golden_point", "advantage"):
            errors.append("deuce_mode must be 'golden_point' or 'advantage'")
        else:
            scoring_rules["deuce_mode"] = data["deuce_mode"]
    for key, lo, hi in (
        ("tiebreak_target", 3, 21),
        ("supertiebreak_target", 3, 21),
        ("tiebreak_side_switch_every", 0, 99),
    ):
        if key in data:
            try:
                v = int(data[key])
            except (TypeError, ValueError):
                errors.append(f"{key} must be an integer"); continue
            if not (lo <= v <= hi):
                errors.append(f"{key} out of range [{lo},{hi}]"); continue
            scoring_rules[key] = v
    if errors:
        return jsonify({"success": False, "errors": errors, "rules": scoring_rules}), 400
    log.info("scoring rules updated: %s", scoring_rules)
    return jsonify({"success": True, "rules": scoring_rules})


@app.route("/setgamemode", methods=["POST"])
def _setgamemode():
    data = request.get_json(silent=True) or {}
    mode = data.get("mode")
    if mode is not None and mode not in ("basic", "competition", "lock"):
        return jsonify({"success": False, "error": "invalid mode"}), 400
    with state_lock:
        gamestate["gamemode"] = mode
        gamestate["initialswitchdone"] = False
        if mode == "basic":
            triggerbasicmodesideswitchifneeded()
    broadcast_gamestate()
    return jsonify({"success": True, "gamemode": mode})


@app.route("/resetmatch", methods=["POST"])
@sensor_auth_required
def _resetmatch():
    data = request.get_json(silent=True) or {}
    event_id = data.get("event_id")
    cached = recall_event(event_id)
    if cached is not None:
        return jsonify({**cached, "deduped": True}), 200
    with state_lock:
        wipe_match_storage()
        gamestate.update({
            "game1": 0, "game2": 0, "set1": 0, "set2": 0,
            "point1": 0, "point2": 0, "score1": 0, "score2": 0,
            "matchwon": False, "winner": None,
            "sethistory": [], "matchhistory": [],
            "matchstarttime": datetime.now().isoformat(),
            "matchendtime": None,
            "lastupdated": datetime.now().isoformat(),
            "shouldswitchsides": False, "totalgamesinset": 0,
            "mode": "normal", "gamemode": None,
            "initialswitchdone": False,
            "lastgamestate": None, "lastsideswitch": None,
        })
    broadcast_gamestate()
    socketio.emit("match_reset_triggered", namespace="/")
    result = {"success": True, "message": "Match reset"}
    remember_event(event_id, result)
    return jsonify(result)


def _do_reset_match():
    """Shared helper: wipes + resets gamestate and broadcasts. Caller owns idempotency."""
    with state_lock:
        wipe_match_storage()
        gamestate.update({
            "game1": 0, "game2": 0, "set1": 0, "set2": 0,
            "point1": 0, "point2": 0, "score1": 0, "score2": 0,
            "matchwon": False, "winner": None,
            "sethistory": [], "matchhistory": [],
            "matchstarttime": datetime.now().isoformat(),
            "matchendtime": None,
            "lastupdated": datetime.now().isoformat(),
            "shouldswitchsides": False, "totalgamesinset": 0,
            "mode": "normal", "gamemode": None,
            "initialswitchdone": False,
            "lastgamestate": None, "lastsideswitch": None,
        })
    broadcast_gamestate()
    socketio.emit("match_reset_triggered", namespace="/")
    return {"success": True, "message": "Match reset"}


@app.route("/remote_event", methods=["POST"])
@sensor_auth_required
def _remote_event():
    """
    Unified endpoint for remote-driven events (future ESP-NOW USB-serial
    bridge lands here). Accepts:
        {"action":"addpoint",      "team":"black"|"yellow", "event_id":"..."}
        {"action":"subtractpoint", "team":"black"|"yellow", "event_id":"..."}
        {"action":"reset",                                  "event_id":"..."}
    Shares the same idempotency cache as /addpoint /subtractpoint /resetmatch,
    so a retried POST with the same event_id will not double-count.
    """
    data = request.get_json(silent=True) or {}
    event_id = data.get("event_id")
    cached = recall_event(event_id)
    if cached is not None:
        return jsonify({**cached, "deduped": True}), 200

    action = (data.get("action") or "").strip().lower()
    if action in ("addpoint", "subtractpoint"):
        team = _team_from_payload(data)
        processor = process_add_point if action == "addpoint" else process_subtract_point
        with state_lock:
            result = processor(team)
    elif action == "reset":
        result = _do_reset_match()
    else:
        return jsonify({"success": False, "error": f"bad action: {action!r}"}), 400

    remember_event(event_id, result)
    return jsonify(result), (200 if result.get("success") else 400)


@app.route("/matches", methods=["GET"])
def _list_matches():
    """Return the most-recent persisted matches (newest first)."""
    try:
        limit = int(request.args.get("limit", 20))
    except (TypeError, ValueError):
        limit = 20
    try:
        rows = store.list_matches(limit)
    except Exception as e:
        log.warning("store.list_matches failed: %s", e)
        return jsonify({"success": False, "error": str(e), "matches": []}), 500
    return jsonify({"success": True, "count": len(rows), "matches": rows})


@app.route("/sensor_heartbeat", methods=["POST"])
@sensor_auth_required
def _heartbeat():
    data = request.get_json(silent=True) or {}
    node_id = data.get("node_id")
    if not node_id:
        return jsonify({"success": False, "error": "missing node_id"}), 400
    entry = {
        "node_id": node_id,
        "team": data.get("team") or sensor_mapping.get(node_id),
        "rssi": data.get("rssi"),
        "vbat_mv": data.get("vbat_mv"),
        "uptime_s": data.get("uptime_s"),
        "fw": data.get("fw"),
        "state": data.get("state"),
        "last_seen": time.time(),
        "last_seen_iso": datetime.now().isoformat(),
    }
    heartbeats[node_id] = entry
    socketio.emit("sensor_heartbeat", _heartbeat_snapshot(), namespace="/")
    return jsonify({"success": True})


def _heartbeat_snapshot():
    now = time.time()
    return {
        nid: {**v, "online": (now - v["last_seen"]) < HEARTBEAT_STALE_S}
        for nid, v in heartbeats.items()
    }


@app.route("/sensorvalidation", methods=["GET"])
def _sensorvalidation():
    snap = _heartbeat_snapshot()
    all_online = snap and all(v["online"] for v in snap.values())
    return jsonify({
        "validated": bool(all_online),
        "status": "valid" if all_online else "degraded",
        "nodes": snap,
        "timestamp": datetime.now().isoformat(),
    })


@app.route("/swapsensors", methods=["POST"])
def _swapsensors():
    with state_lock:
        ids = list(sensor_mapping.keys())
        ids = [i for i in ids if i != "last_swap"]
        if len(ids) >= 2:
            a, b = ids[0], ids[1]
            sensor_mapping[a], sensor_mapping[b] = sensor_mapping[b], sensor_mapping[a]
        sensor_mapping["last_swap"] = datetime.now().isoformat()
    socketio.emit("sensor_mapping_updated", sensor_mapping, namespace="/")
    return jsonify({"success": True, "mapping": sensor_mapping})


@app.route("/getsensormapping", methods=["GET"])
def _getmapping():
    return jsonify({"success": True, "mapping": sensor_mapping})


@app.route("/health", methods=["GET"])
def _health():
    return jsonify({
        "status": "healthy",
        "timestamp": datetime.now().isoformat(),
        "gamestate": gamestate,
        "heartbeats": _heartbeat_snapshot(),
        "idempotency_entries": len(idempotency_cache),
    })


if __name__ == "__main__":
    log.info("SUMMA V2 backend starting on %s:%s", HOST, PORT)
    if NODE_TOKEN == "change-me-in-env":
        log.warning("SUMMA_NODE_TOKEN is default — set it in .env for production")
    socketio.run(app, host=HOST, port=PORT, debug=False, allow_unsafe_werkzeug=True)
