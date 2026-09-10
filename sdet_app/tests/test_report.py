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


def test_report_page_bug_cards(client, login, _init_db):
    """The report page shows copy-ready bug cards instead of the old
    'Anomalies (FAIL)' section: per-failure title, reproduction steps,
    environment and a copy button."""
    from sdet_app import database

    pid = database.create_project(
        {"name": "BUGPROJ", "url": "https://bug.test", "email": "a@b.test",
         "password": "pw", "auth_type": "simple", "environment": "STAGING",
         "comments": ""}, encrypt=lambda p: p)
    tid = database.create_test_run(pid, "Test", "qa@example.com", [])
    database.save_result(tid, {
        "module": "CRM", "function": "Clients",
        "action": "Étape 1/2: Ouvrir la page clients", "data": "",
        "status": "PASS", "severity": "",
        "expected": "Page chargée", "obtained": "OK"})
    database.save_result(tid, {
        "module": "CRM", "function": "Clients",
        "action": "Étape 2/2: Cliquer sur Ajouter", "data": "QA_001",
        "status": "FAIL", "severity": "MAJOR",
        "expected": "Formulaire ouvert", "obtained": "Bouton introuvable"},
        "screenshots/shot_1.png", 0)

    html = client.get(f"/reports/{tid}").get_data(as_text=True)

    # Old section removed
    assert "Anomalies (FAIL)" not in html
    # New section present
    assert "Rapports de bug prêts à copier" in html
    assert "Tout copier (1)" in html
    assert "CRM — Clients" in html
    assert "MAJOR" in html
    assert "Étapes de reproduction" in html
    assert "Étape 1/2: Ouvrir la page clients" in html
    assert "Bouton introuvable" in html
    assert "bug-markdown" in html
    assert 'onclick="copyBug(this)"' in html
    assert "Aucun bug détecté" not in html

    database.delete_test_run(tid)
    database.delete_project(pid)
