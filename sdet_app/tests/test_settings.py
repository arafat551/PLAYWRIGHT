from sdet_app import config
from sdet_app import database


def test_settings_roundtrip():
    config.apply_settings({"max_pages": "80", "headless": "1"})
    stored = database.load_settings()
    assert stored["max_pages"] == "80"
    assert stored["headless"] == "1"


def test_setting_falls_back_to_env_default():
    config.reset_settings(["max_pages"])
    assert config.get_setting("max_pages", config.env.MAX_PAGES) == config.env.MAX_PAGES
    assert config.setting_int("max_pages", config.env.MAX_PAGES) == int(config.env.MAX_PAGES)


def test_setting_typed_accessors():
    config.apply_settings({"max_buttons": "200", "headless": "true"})
    assert config.setting_int("max_buttons", 300) == 200
    assert config.setting_bool("headless", False) is True
    config.apply_settings({"page_timeout": "15000"})
    assert config.setting_int("page_timeout", 30000) == 15000
    config.reset_settings(["max_buttons", "headless", "page_timeout"])


def test_settings_ui_saves(client):
    client.post("/login", data={"email": "admin@example.com", "password": "admin123"})
    r = client.post("/settings", data={
        "max_pages": "90", "max_buttons": "400", "max_forms": "20",
        "page_timeout": "20000", "headless": "0", "ai_model": ""},)
    assert r.status_code == 302
    r = client.get("/settings")
    html = r.data.decode("utf-8", "replace")
    assert 'value="90"' in html
    assert 'value="400"' in html
    client.post("/settings", data={
        "max_pages": "50", "max_buttons": "300", "max_forms": "30",
        "page_timeout": "30000", "headless": "0", "ai_model": ""})