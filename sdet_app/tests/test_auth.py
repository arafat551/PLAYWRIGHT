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
