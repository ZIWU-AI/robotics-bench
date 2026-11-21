"""Offline tests of the case kit: recording, call envelope, gate's tampering helpers, vendored copies."""
import json
import shutil
import sys
import tempfile
import threading
import unittest
import urllib.error
import urllib.request
from pathlib import Path

HERE = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(HERE))
from casekit import recording  # noqa: E402
from casekit.episode import BadRequest, Episode, Refused, make_server, op  # noqa: E402
from casekit.verify import load_export  # noqa: E402
import gate  # noqa: E402
import sync  # noqa: E402

REPO = HERE.parents[1]


class Counter(Episode):
    API, BACKEND = "counter-api-v1", "toy"
    BUDGETS = {"actions": 3}

    def __init__(self, out):
        self.n = 0
        super().__init__(out)

    def snapshot(self):
        return {"n": self.n}

    @op(budget="actions")
    def bump(self, body):
        by = body.get("by", 1)
        if not isinstance(by, int):
            raise BadRequest("by must be an integer")
        if by > 5:
            raise Refused("at most 5 per bump")
        self.n += by
        return {"executed": True, "by": by}

    @op(record=False)
    def peek(self, body):
        return {"n": self.n}


class SingleClockTest(unittest.TestCase):
    def test_wall_budget_rejected(self):
        class Timed(Counter):
            BUDGETS = {"actions": 3, "wall_s": 60}
        tmp = Path(tempfile.mkdtemp())
        try:
            with self.assertRaisesRegex(ValueError, "timeout_sec"):
                Timed(tmp / "rec")
        finally:
            shutil.rmtree(tmp, ignore_errors=True)

    def test_left_has_no_clock(self):
        tmp = Path(tempfile.mkdtemp())
        try:
            self.assertEqual(Counter(tmp / "rec").left(), {"actions": 3})
        finally:
            shutil.rmtree(tmp, ignore_errors=True)


class EnvelopeTest(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.ep = Counter(self.tmp / "rec")
        self.httpd = make_server(self.ep, "tok")
        threading.Thread(target=self.httpd.serve_forever, daemon=True).start()
        self.url = f"http://127.0.0.1:{self.httpd.server_port}"

    def tearDown(self):
        self.httpd.shutdown()
        self.httpd.server_close()
        shutil.rmtree(self.tmp, ignore_errors=True)

    def call(self, name, body=None, token="tok", raw=None):
        get = name == "info" and body is None and raw is None
        data = raw if raw is not None else (None if get else json.dumps(body or {}).encode())
        req = urllib.request.Request(f"{self.url}/v1/{name}", data=data, method="GET" if get else "POST",
                                     headers={"Authorization": f"Bearer {token}"})
        try:
            with urllib.request.urlopen(req, timeout=10) as r:
                return r.status, json.load(r)
        except urllib.error.HTTPError as e:
            return e.code, json.loads(e.read())

    def test_envelope(self):
        self.assertEqual(self.call("info", token="bad")[0], 401)
        code, info = self.call("info")
        self.assertEqual((code, info["api"], info["ops"]), (200, "counter-api-v1", ["bump", "peek", "finish"]))
        self.assertEqual(self.call("nope", {})[0], 404)
        self.assertEqual(self.call("bump", raw=b"{x")[0], 400)
        self.assertEqual(self.call("bump", {"by": "x"})[0], 400)              # malformed: not counted
        self.assertTrue(self.call("bump", {"by": 2})[1]["ok"])
        r = self.call("bump", {"by": 9})[1]                                    # refused: counted, recorded
        self.assertEqual((r["ok"], r["left"]["actions"]), (False, 1))
        self.assertTrue(self.call("bump", {})[1]["ok"])
        r = self.call("bump", {})[1]
        self.assertIn("budget", r["refused"][0])
        self.assertEqual(self.call("peek", {})[1]["n"], 3)
        self.assertTrue(self.call("finish", {})[1]["ok"])
        self.assertFalse(self.call("finish", {})[1]["ok"])
        self.assertFalse(self.call("peek", {})[1]["ok"])
        entries, seal = recording.verify(self.tmp / "rec")
        self.assertEqual([e["op"] for e in entries], ["bump", "bump", "bump", "finish"])
        self.assertEqual([e["state"]["n"] for e in entries], [2, 2, 3, 3])
        self.assertEqual((seal["api"], seal["backend"], seal["used"]), ("counter-api-v1", "toy", {"actions": 3}))

    def test_collector_finish(self):
        self.call("bump", {})
        self.assertTrue(self.call("finish", {"by": "collector"})[1]["ok"])
        entries, _ = recording.verify(self.tmp / "rec")
        self.assertEqual((entries[-1]["op"], entries[-1]["request"]), ("finish", {"by": "collector"}))
        _, _, problems = load_export(self.tmp / "rec", api="counter-api-v1", backend="toy")
        self.assertEqual(problems, ["the agent did not call finish; the collector sealed the episode"])

    def test_agent_finish(self):
        self.call("bump", {})
        self.assertTrue(self.call("finish", {})[1]["ok"])
        self.assertFalse(self.call("finish", {"by": "collector"})[1]["ok"])      # the collector comes too late
        entries, _ = recording.verify(self.tmp / "rec")
        self.assertEqual(entries[-1]["request"], {})
        self.assertEqual(load_export(self.tmp / "rec", api="counter-api-v1", backend="toy")[2], [])

    def test_tampering_helpers(self):
        for _ in range(2):
            self.call("bump", {})
        self.call("finish", {})
        src = self.tmp / "rec"
        for fn, expect in ((gate.tamper_state, "valid"), (gate.truncate, "valid"), (gate.collector_finish, "valid"),
                           (gate.edit_line, "broken")):
            d = self.tmp / fn.__name__
            shutil.copytree(src, d)
            fn(d)
            if expect == "broken":
                with self.assertRaises(ValueError):
                    recording.verify(d)
                continue
            entries, _ = recording.verify(d)                                   # chain re-sealed, content changed
            orig, _ = recording.verify(src)
            self.assertNotEqual(entries, orig)
            if fn is gate.truncate:
                self.assertNotEqual(entries[-1]["op"], "finish")


class CopiesTest(unittest.TestCase):
    def test_example_in_sync(self):
        ex = HERE / "example/gantry-push"
        self.assertEqual(sync.problems(ex), [])
        for name in ("sim.py", "scene.json"):
            self.assertEqual((ex / "environment/service" / name).read_bytes(), (ex / "tests" / name).read_bytes(), name)


if __name__ == "__main__":
    unittest.main()
