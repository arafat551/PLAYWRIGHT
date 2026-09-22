"""Tests for user ownership / multi-user isolation and the PDF report."""


def _login(client, email, pw):
    return client.post("/login", data={"email": email, "password": pw})


def _make_project(client, name):
    from sdet_app import database
    client.post("/projects/new", data={
        "name": name, "url": f"https://{name}.test", "email": "a@b.test",
        "password": "pw", "auth_type": "simple", "environment": "STAGING",
        "comments": ""}, follow_redirects=True)
    proj = [p for p in database.list_projects() if p["name"] == name]
    return proj[0]["id"] if proj else None


def test_regular_user_only_sees_own_projects(client, two_users):
    alice, bob = two_users
    _login(client, bob, "pwB")
    pid_b = _make_project(client, "ProjetBob")
    assert pid_b is not None
    _login(client, alice, "pwA")
    body = client.get("/projects").get_data(as_text=True)
    assert "ProjetBob" not in body
    pid_a = _make_project(client, "ProjetAlice")
    assert pid_a is not None
    _login(client, bob, "pwB")
    body = client.get("/projects").get_data(as_text=True)
    assert "ProjetAlice" not in body
    assert "ProjetBob" in body
    _login(client, "admin@example.com", "admin123")
    body = client.get("/projects").get_data(as_text=True)
    assert "ProjetAlice" in body and "ProjetBob" in body


def test_user_cannot_access_other_users_project(client, two_users):
    alice, bob = two_users
    _login(client, bob, "pwB")
    pid_b = _make_project(client, "ProjetBob")
    _login(client, alice, "pwA")
    r = client.get(f"/projects/{pid_b}")
    assert r.status_code == 302
    r = client.get(f"/projects/{pid_b}/scenarios")
    assert r.status_code == 302
    r = client.get(f"/projects/{pid_b}/select")
    assert r.status_code == 302


def test_delete_restricted_to_owner_even_for_admin(client, two_users, _init_db):
    from sdet_app import database
    _login(client, two_users[1], "pwB")
    pid_b = _make_project(client, "ProjetBob")
    _login(client, "admin@example.com", "admin123")
    r = client.post(f"/projects/{pid_b}/delete")
    assert r.status_code == 302
    assert database.get_project(pid_b) is not None, \
        "l'admin ne doit pas pouvoir supprimer un projet d'autrui"
    _login(client, two_users[1], "pwB")
    client.post(f"/projects/{pid_b}/delete")
    assert database.get_project(pid_b) is None


def test_dashboard_scoped_per_user(client, two_users):
    alice, bob = two_users
    _login(client, bob, "pwB")
    _make_project(client, "ProjetBob")
    body = client.get("/dashboard").get_data(as_text=True)
    assert "ProjetBob" in body
    _login(client, alice, "pwA")
    body = client.get("/dashboard").get_data(as_text=True)
    assert "ProjetBob" not in body
    _login(client, "admin@example.com", "admin123")
    body = client.get("/dashboard").get_data(as_text=True)
    assert "ProjetBob" in body


def test_dashboard_vertical_chart_with_pagination(client, _init_db):
    _login(client, "admin@example.com", "admin123")
    for i in range(7):
        _make_project(client, f"ProjetPag-{i}")
    body = client.get("/dashboard").get_data(as_text=True)
    assert "vbar-chart" in body
    assert body.count('class="vbar-page"') == 2, \
        "le graphique doit être paginé par lots de 6 projets"
    assert 'class="vbar-grid-lines"' in body


def test_dashboard_donut_and_recent_pagination_rendered(client, _init_db):
    _login(client, "admin@example.com", "admin123")
    body = client.get("/dashboard").get_data(as_text=True)
    assert 'id="donut"' in body
    assert 'id="recentPager"' in body
    assert 'id="ppPager"' in body


def test_users_menu_visible_for_admin_only(client, two_users):
    alice, _ = two_users
    _login(client, alice, "pwA")
    body = client.get("/dashboard").get_data(as_text=True)
    assert "Utilisateurs" not in body
    _login(client, "admin@example.com", "admin123")
    body = client.get("/dashboard").get_data(as_text=True)
    assert "Utilisateurs" in body


def test_users_page_blocked_for_qa(client, two_users):
    alice, _ = two_users
    _login(client, alice, "pwA")
    r = client.get("/users")
    assert r.status_code == 302


def test_report_pdf_download(client, _init_db, monkeypatch):
    from sdet_app import app as app_mod, database
    _login(client, "admin@example.com", "admin123")
    pid = _make_project(client, "ProjetPdf")
    tid = database.create_test_run(
        pid, "Test", "admin@example.com",
        plan=[{"module": "CRM", "function": "Login",
               "action": "Ouvrir login", "expected": "Page login"}])
    database.save_result(tid, dict(
        module="CRM", function="Login", action="Ouvrir login", expected="Page login",
        obtained="", status="PASS", severity="", data=""))
    database.finalize_test_run(tid, "completed",
                               {"total": 1, "passed": 1, "failed": 0,
                                "warning": 0, "skipped": 0}, [])
    monkeypatch.setattr(app_mod, "_render_report_pdf",
                        lambda *a, **k: b"%PDF-1.4 fake")
    r = client.get(f"/reports/{tid}/download")
    assert r.status_code == 200
    assert r.mimetype == "application/pdf"
    assert r.data.startswith(b"%PDF-1.4")
    assert ".pdf" in r.headers.get("Content-Disposition", "")


def test_reports_list_has_download_column(client, _init_db, monkeypatch):
    from sdet_app import app as app_mod, database
    _login(client, "admin@example.com", "admin123")
    pid = _make_project(client, "ProjetColPdf")
    tid = database.create_test_run(pid, "Test", "admin@example.com")
    database.finalize_test_run(tid, "completed",
                               {"total": 0, "passed": 0, "failed": 0,
                                "warning": 0, "skipped": 0}, [])
    body = client.get("/reports").get_data(as_text=True)
    assert "Consulter" in body
    assert "PDF" in body
    assert f"/reports/{tid}/download" in body