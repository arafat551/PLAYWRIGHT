from sdet_app.sdet import reporter


def _report():
    results = [
        {"module": "Clients", "function": "Ajouter un client", "action": "CREATE",
         "data": "QA_CLIENTS_4821", "status": "PASS", "severity": "",
         "expected": "créé", "obtained": "succès", "screenshot": ""},
        {"module": "Clients", "function": "Modifier client", "action": "UPDATE",
         "data": "QA_CLIENTS_4821", "status": "FAIL", "severity": "MAJOR",
         "expected": "conservé", "obtained": "ancien téléphone", "screenshot": "screenshots/x.png"},
    ]
    counters = reporter.counters_from_results(results)
    report = reporter.build_report(
        {"name": "COBRA", "environment": "STAGING"},
        {"started_at": "2026-01-01 10:00:00", "launched_by": "admin@example.com",
         "run_type": "Test", "id": 1}, results, counters, 120)
    return report


def test_report_counts():
    r = _report()
    assert r["total"] == 2
    assert r["passed"] == 1
    assert r["failed"] == 1
    assert r["success_pct"] == 50.0


def test_report_failures_listed():
    r = _report()
    assert len(r["failures"]) == 1
    assert r["failures"][0]["severity"] == "MAJOR"


def test_report_severity_counts():
    r = _report()
    assert r["severity_counts"]["MAJOR"] == 1


def test_report_html_render():
    r = _report()
    html = reporter.render_html(r)
    assert "RAPPORT AUTOMATION" in html
    assert "COBRA" in html
    assert "50.0" in html
