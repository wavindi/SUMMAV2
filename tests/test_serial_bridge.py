from urllib.error import URLError

from tools import serial_bridge


def test_xiao_receiver_protocol_lines_are_parsed():
    cases = {
        "got: black addpoint|rb-00041-007": ("black", "addpoint", None, "rb-00041-007"),
        "got: yellow subtractpoint|ry-00041-008": ("yellow", "subtractpoint", None, "ry-00041-008"),
        "got: reset|rb-00041-009": (None, None, "reset", "rb-00041-009"),
    }
    for line, expected in cases.items():
        match = serial_bridge._LINE_RE.search(line)
        assert match is not None
        assert match.groups() == expected


def test_post_retries_transient_backend_failure(monkeypatch):
    calls = []

    class Response:
        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return False

        def read(self):
            return b'{"success":true,"message":"ok"}'

    def urlopen(_request, timeout):
        calls.append(timeout)
        if len(calls) < 3:
            raise URLError("backend restarting")
        return Response()

    monkeypatch.setattr(serial_bridge.urllib_req, "urlopen", urlopen)
    monkeypatch.setattr(serial_bridge.time, "sleep", lambda _seconds: None)

    assert serial_bridge._post_event(
        "http://127.0.0.1:5000", "test-token", "black", "addpoint",
        "rb-00001-001", attempts=3,
    ) is True
    assert len(calls) == 3

