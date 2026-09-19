import copy
import os
import sys
from pathlib import Path

import pytest


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
os.environ.setdefault("SUMMA_NODE_TOKEN", "test-token")
os.environ.setdefault("SUMMA_DB_PATH", ":memory:")

import backend_pi as backend  # noqa: E402


INITIAL_GAMESTATE = copy.deepcopy(backend.gamestate)


@pytest.fixture(autouse=True)
def clean_backend_state(monkeypatch):
    with backend.state_lock:
        backend.gamestate.clear()
        backend.gamestate.update(copy.deepcopy(INITIAL_GAMESTATE))
        backend.gamestate["gamemode"] = "basic"
        backend.wipe_match_storage()
        backend.idempotency_cache.clear()
        backend.scoring_rules.update({
            "deuce_mode": "golden_point",
            "tiebreak_target": 7,
            "supertiebreak_target": 10,
            "tiebreak_side_switch_every": 6,
        })
    saved = []
    monkeypatch.setattr(backend.store, "save_match", lambda record: saved.append(record) or len(saved))
    yield saved

