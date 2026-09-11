"""Test that Runner._record systematically captures a screenshot for every
recorded step (PASS included), so the report's "Capture" column always
shows an image instead of "—" for most rows.
"""
from sdet_app.sdet.runner import Runner


def _make_runner(page, db):
    class _FakeSession:
        def __init__(self, page):
            self.page = page

    runner = Runner.__new__(Runner)
    runner.session = _FakeSession(page)
    runner.page = page
    runner.db = db
    runner.project = {"url": "http://test.local"}
    runner.tid = 42
    runner._is_cancelled = lambda: False
    runner.suffix = None
    runner.entity_name = None
    runner.shot_seq = 0
    runner.last_fill_value = None
    return runner


class _FakePage:
    def __init__(self):
        self.shot_paths = []

    def screenshot(self, path=None):
        self.shot_paths.append(path)
        return b"png"


class _FakeDB:
    def __init__(self):
        self.saved = []

    def save_result(self, tid, res, screenshot="", http_status=0):
        self.saved.append({"tid": tid, "res": res, "screenshot": screenshot,
                           "http": http_status})


def _base_res():
    return {"module": "CRM", "function": "Clients", "action": "READ",
            "data": "", "severity": ""}


def test_record_without_screenshot_captures_one():
    """A step recorded without an explicit screenshot gets one automatically."""
    page = _FakePage()
    db = _FakeDB()
    runner = _make_runner(page, db)

    runner._record(_base_res(), "PASS", "Page consultée", "OK")

    assert len(page.shot_paths) == 1
    shot = db.saved[0]["screenshot"]
    assert shot.startswith("screenshots/shot_42_CRM_Clients_PASS_1.png")


def test_record_with_explicit_screenshot_does_not_capture_again():
    """Callers that already captured (FAIL/WARNING paths) keep their shot —
    no duplicate capture."""
    page = _FakePage()
    db = _FakeDB()
    runner = _make_runner(page, db)

    runner._record(_base_res(), "FAIL", "Page chargée sans erreur",
                   "Code HTTP 500", "screenshots/shot_42_CRM_Clients_FAIL_1.png")

    assert page.shot_paths == []
    assert db.saved[0]["screenshot"] == "screenshots/shot_42_CRM_Clients_FAIL_1.png"


def test_record_screenshot_failure_keeps_row_saved():
    """If the capture itself fails, the step is still recorded (empty shot)."""
    class _BrokenPage:
        def screenshot(self, path=None):
            raise RuntimeError("target closed")

    page = _BrokenPage()
    db = _FakeDB()
    runner = _make_runner(page, db)

    runner._record(_base_res(), "FAIL", "Attendu", "Obtenu")

    assert len(db.saved) == 1
    assert db.saved[0]["screenshot"] == ""
    assert db.saved[0]["res"]["module"] == "CRM"