import io
import os
import sys

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from sdet_app.sdet.reporter import (
    build_report,
    counters_from_results,
    group_by_module,
    render_html,
    write_report_file,
)

from sdet_app.models import TestResult as _TestResult


def make_result(**kw):
    default = dict(
        id=1, test_id=42, module="CRM", function_name="Créer une catégorie",
        action="CREATE", data="", status="PASS", severity="MINOR",
        message="ok", screenshot="screenshots/shot_42_CRM_x.png",
        exec_time=1, created_at="",
    )
    default.update(kw)
    r = _TestResult(0)
    for k, v in default.items():
        setattr(r, k, v)
    return r


def test_group_by_module_maps_function_name():
    rows = [make_result()]
    modules = group_by_module([dict(r.__dict__) for r in rows])
    assert "CRM" in modules
    assert modules["CRM"][0]["function"] == "Créer une catégorie"


def test_counters_from_results():
    rows = [
        dict(make_result(id=1).__dict__ | {"status": "PASS"}),
        dict(make_result(id=2).__dict__ | {"status": "FAIL"}),
        dict(make_result(id=3).__dict__ | {"status": "WARNING"}),
        dict(make_result(id=4).__dict__ | {"status": "SKIPPED"}),
    ]
    c = counters_from_results(rows)
    assert c == {"total": 4, "passed": 1, "failed": 1, "warning": 1, "skipped": 1}


def test_render_html_uses_relative_static_screenshot(tmp_path):
    rows = [dict(make_result().__dict__)]
    modules = group_by_module(rows)
    counters = counters_from_results(rows)
    report = build_report(
        {"name": "KPIP", "environment": "prod"}, {"started_at": "2026-01-01"},
        rows, counters, 60,
    )
    path = str(tmp_path / "rapport_x.html")
    html = render_html(report, path)
    assert "capture" in html
    assert "../static/screenshots/shot_42_CRM_x.png" in html
    assert os.path.isfile(path)
    with io.open(path, encoding="utf-8") as f:
        assert "Créer une catégorie" in f.read()


def test_write_report_file_returns_report_path():
    env = pytest.importorskip("sdet_app.config").env
    rows = [dict(make_result(id=7, test_id=7).__dict__)]
    counters = counters_from_results(rows)
    path = write_report_file({"name": "KPIP"}, {"id": 7}, rows, counters, 60)
    assert path.startswith("reports/")
    full = os.path.join(env.REPORT_DIR, path.split("/", 1)[1])
    assert os.path.isfile(full)