def test_login_redirects_to_dashboard(client):
    r = client.get("/")
    assert r.status_code in (301, 302)


def test_login_correct(client):
    r = client.post("/login", data={"email": "admin@example.com", "password": "admin123"},
                    follow_redirects=True)
    assert r.status_code == 200
    assert "TABLEAU DE BORD" in r.get_data(as_text=True)


def test_login_wrong(client):
    r = client.post("/login", data={"email": "admin@example.com", "password": "bad"},
                    follow_redirects=True)
    body = r.get_data(as_text=True)
    assert "Identifiants incorrects" in body


def test_project_creation(client):
    client.post("/login", data={"email": "admin@example.com", "password": "admin123"})
    r = client.post("/projects/new", data={
        "name": "MonApp", "url": "https://monapp.test", "email": "a@b.test",
        "password": "pw", "auth_type": "simple", "environment": "STAGING",
        "comments": "Tester tout"}, follow_redirects=True)
    assert r.status_code == 200
    assert "MonApp" in r.get_data(as_text=True)


def test_project_creation_with_2fa(client):
    """Un projet avec authentification 2FA / OTP doit pouvoir être créé."""
    from sdet_app import database
    client.post("/login", data={"email": "admin@example.com", "password": "admin123"})
    r = client.post("/projects/new", data={
        "name": "App2FA", "url": "https://app2fa.test", "email": "a@b.test",
        "password": "pw", "auth_type": "2fa", "environment": "STAGING",
        "comments": ""}, follow_redirects=True)
    assert r.status_code == 200
    assert "App2FA" in r.get_data(as_text=True)
    proj = [p for p in database.list_projects() if p["name"] == "App2FA"][0]
    assert proj["auth_type"] == "2fa"
    client.post("/projects/{}/delete".format(proj["id"]))


def test_public_project_can_be_created_without_credentials(client, _init_db):
    from sdet_app import database
    client.post("/login", data={"email": "admin@example.com", "password": "admin123"})
    response = client.post("/projects/new", data={
        "name": "PublicApp", "url": "https://public.test", "email": "",
        "password": "", "auth_type": "none", "environment": "STAGING",
        "comments": ""})
    assert response.status_code == 302
    project = next(p for p in database.list_projects()
                   if p["name"] == "PublicApp")
    assert project["auth_type"] == "none"
    assert project["email"] == ""
    assert project["password_enc"] == ""
    client.post("/projects/{}/delete".format(project["id"]))


def test_switching_project_to_public_clears_stored_credentials(client, _init_db):
    from sdet_app import database
    project_id = database.create_project(
        {"name": "PrivateToPublic", "url": "https://switch.test",
         "email": "qa@switch.test", "password": "secret",
         "auth_type": "simple", "environment": "STAGING", "comments": ""},
        encrypt=lambda value: "encrypted:" + value)
    client.post("/login", data={"email": "admin@example.com", "password": "admin123"})
    response = client.post(f"/projects/{project_id}/edit", data={
        "name": "PrivateToPublic", "url": "https://switch.test", "email": "",
        "password": "", "auth_type": "none", "environment": "STAGING",
        "comments": ""})
    assert response.status_code == 302
    project = database.get_project(project_id)
    assert project["email"] == ""
    assert project["password_enc"] == ""
    client.post("/projects/{}/delete".format(project_id))


def test_otp_route_submits_code_and_waits(client):
    """Le code OTP saisi dans l'interface doit être enregistré (waiting_otp)."""
    from sdet_app import database
    client.post("/login", data={"email": "admin@example.com", "password": "admin123"})
    pid = database.create_project(
        {"name": "Kpip2FA", "url": "https://kpip2fa.test", "email": "a@b.test",
         "password": "pw", "auth_type": "2fa", "environment": "STAGING",
         "comments": ""}, "admin@example.com", encrypt=lambda s: s)
    tid = database.create_test_run(pid, "Exploration", "admin@example.com")
    database.set_waiting_otp(tid)

    r = client.post("/tests/{}/otp".format(tid), data={"otp_code": "123456"})
    assert r.status_code == 302
    assert database.test_status(tid) == "otp_submitted"
    assert (database.get_test_run(tid).otp_code or "") == "123456"
    database.delete_project(pid)
    database.delete_test_run(tid)


def test_project_validation_required(client):
    client.post("/login", data={"email": "admin@example.com", "password": "admin123"})
    r = client.post("/projects/new", data={"name": "", "url": "not-a-url"},
                    follow_redirects=True)
    body = r.get_data(as_text=True)
    assert "obligatoire" in body


def test_project_toggle_redirects_and_switches_status(client):
    from sdet_app import database
    client.post("/login", data={"email": "admin@example.com", "password": "admin123"})
    client.post("/projects/new", data={
        "name": "Kpip", "url": "https://kpip.test", "email": "a@b.test",
        "password": "pw", "auth_type": "simple", "environment": "STAGING",
        "comments": ""}, follow_redirects=True)
    proj = [p for p in database.list_projects() if p["name"] == "Kpip"][0]
    r = client.post("/projects/{}/toggle".format(proj["id"]))
    assert r.status_code == 302
    assert "/projects" in r.headers.get("Location", "")
    assert database.get_project(proj["id"])["status"] == "disabled"
    client.post("/projects/{}/toggle".format(proj["id"]))
    assert database.get_project(proj["id"])["status"] == "active"
    client.post("/projects/{}/delete".format(proj["id"]))
