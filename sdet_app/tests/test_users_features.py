"""Tests for user management: modal creation, temporary password e-mail,
forgot/reset password, profile edition, view/edit/deactivate actions."""

import json


def _login(client, email, pw):
    return client.post("/login", data={"email": email, "password": pw})


def test_create_user_with_temp_password_and_email(client, _init_db):
    from sdet_app import database
    _login(client, "admin@example.com", "admin123")
    r = client.post("/users/new",
                    data={"full_name": "Tmp Pwd", "email": "tmp-pwd@x.test", "role": "qa"},
                    headers={"X-Requested-With": "fetch"})
    assert r.status_code == 200
    data = r.get_json()
    assert data["ok"] is True
    assert data["temp_password"], "un mot de passe temporaire doit être généré"
    assert data["email_sent"] is False  # SMTP non configuré en test
    assert database.get_user("tmp-pwd@x.test") is not None
    mails = database.list_outbox()
    assert any(m["recipient"] == "tmp-pwd@x.test"
               and "mot de passe temporaire" in m["body_html"].lower()
               for m in mails)


def test_create_user_duplicate_json(client, _init_db):
    from sdet_app import database
    database.create_user("dup-user@x.test", "pw", "Dup", "qa")
    _login(client, "admin@example.com", "admin123")
    r = client.post("/users/new",
                    data={"full_name": "", "email": "dup-user@x.test", "role": "qa"},
                    headers={"X-Requested-With": "fetch"})
    assert r.status_code == 400
    assert "existe déjà" in r.get_json()["error"]


def test_users_page_has_actions_and_modal(client, two_users):
    _login(client, "admin@example.com", "admin123")
    body = client.get("/users").get_data(as_text=True)
    assert "Ajouter un utilisateur" in body
    assert "addUserModal" in body
    assert "action-btn view icon-only" in body
    assert "action-btn edit icon-only" in body
    assert "action-btn danger icon-only" in body
    assert "openModal('addUserModal')" in body


def test_edit_user_json_and_last_admin_guard(client, _init_db):
    from sdet_app import database
    uid = database.create_user("edit-me@x.test", "pw", "Ancien nom", "qa")
    _login(client, "admin@example.com", "admin123")
    headers = {"X-Requested-With": "fetch"}
    r = client.post(f"/users/{uid}/edit",
                    data={"full_name": "Nouveau nom", "email": "edit-me@x.test",
                          "role": "admin"},
                    headers=headers)
    assert r.status_code == 200 and r.get_json()["ok"] is True
    u = database.get_user_by_id(uid)
    assert u.full_name == "Nouveau nom" and u.role == "admin"
    # dernier admin (celui-ci + admin de base) : on ne peut pas tout dégrader
    admin_ids = [x.id for x in database.list_users() if x.role == "admin" and x.is_active]
    if len(admin_ids) <= 1:
        r = client.post(f"/users/{uid}/edit",
                        data={"full_name": "X", "email": "edit-me@x.test", "role": "qa"},
                        headers=headers)
        assert r.status_code == 400
        assert "dernier administrateur" in r.get_json()["error"]


def test_edit_user_can_change_password(client, _init_db):
    from sdet_app import database
    uid = database.create_user("chpw@x.test", "oldpw", "ChangePw", "qa")
    _login(client, "admin@example.com", "admin123")
    headers = {"X-Requested-With": "fetch"}
    r = client.post(f"/users/{uid}/edit",
                    data={"full_name": "ChangePw", "email": "chpw@x.test",
                          "role": "qa", "new_password": "newpw123",
                          "new_password_confirm": "newpw123"},
                    headers=headers)
    assert r.status_code == 200 and r.get_json()["ok"] is True
    assert database.validate_credentials("chpw@x.test", "newpw123") is True
    assert database.validate_credentials("chpw@x.test", "oldpw") is False


def test_edit_user_password_requires_matching_confirmation(client, _init_db):
    from sdet_app import database
    uid = database.create_user("confpw@x.test", "oldpw", "ConfPw", "qa")
    _login(client, "admin@example.com", "admin123")
    headers = {"X-Requested-With": "fetch"}
    r = client.post(f"/users/{uid}/edit",
                    data={"full_name": "ConfPw", "email": "confpw@x.test",
                          "role": "qa", "new_password": "newpw123",
                          "new_password_confirm": "different"},
                    headers=headers)
    assert r.status_code == 400
    assert "confirmation" in r.get_json()["error"]
    assert database.validate_credentials("confpw@x.test", "newpw123") is False
    assert database.validate_credentials("confpw@x.test", "oldpw") is True


def test_edit_user_password_too_short_rejected(client, _init_db):
    from sdet_app import database
    uid = database.create_user("shortpw@x.test", "pw", "Short", "qa")
    _login(client, "admin@example.com", "admin123")
    headers = {"X-Requested-With": "fetch"}
    r = client.post(f"/users/{uid}/edit",
                    data={"full_name": "Short", "email": "shortpw@x.test",
                          "role": "qa", "new_password": "ab"},
                    headers=headers)
    assert r.status_code == 400
    assert "4 caractères" in r.get_json()["error"]


def test_deactivate_blocks_login_and_self_protection(client, _init_db):
    from sdet_app import database
    uid = database.create_user("deact@x.test", "pw", "Deact", "qa")
    _login(client, "admin@example.com", "admin123")
    r = client.post(f"/users/{uid}/toggle-active")
    assert r.status_code == 302
    assert database.get_user_by_id(uid).is_active == 0
    r = _login(client, "deact@x.test", "pw")
    assert "Identifiants incorrects" in r.get_data(as_text=True)
    # réactivation
    client.post(f"/users/{uid}/toggle-active")
    assert database.get_user_by_id(uid).is_active == 1
    # on ne peut pas désactiver son propre compte
    admin = database.get_user_by_id(1)
    r = client.post(f"/users/{admin.id}/toggle-active", follow_redirects=True)
    assert "votre propre compte" in r.get_data(as_text=True)


def test_profile_update_and_password_change(client, _init_db):
    from sdet_app import database
    uid = database.create_user("profile-me@x.test", "oldpw", "Profil", "qa")
    _login(client, "profile-me@x.test", "oldpw")
    r = client.post("/profile", data={
        "full_name": "Nouveau profil", "email": "profile-me@x.test",
        "current_password": "oldpw", "new_password": "newpw123",
        "new_password_confirm": "newpw123"},
        follow_redirects=True)
    body = r.get_data(as_text=True)
    assert "Profil mis à jour" in body
    assert database.validate_credentials("profile-me@x.test", "newpw123") is True
    # échec sans le bon mot de passe actuel
    r = client.post("/profile", data={
        "full_name": "X", "email": "profile-me@x.test",
        "current_password": "mauvais", "new_password": "whatever",
        "new_password_confirm": "whatever"},
        follow_redirects=True)
    assert "Mot de passe actuel incorrect" in r.get_data(as_text=True)
    # échec si la confirmation ne correspond pas
    r = client.post("/profile", data={
        "full_name": "X", "email": "profile-me@x.test",
        "current_password": "newpw123", "new_password": "second",
        "new_password_confirm": "autre-chose"},
        follow_redirects=True)
    assert "ne correspond pas" in r.get_data(as_text=True)
    assert database.validate_credentials("profile-me@x.test", "newpw123") is True


def test_forgot_password_sends_email_and_reset_works(client, _init_db):
    from sdet_app import database
    database.create_user("reset-me@x.test", "pw-before", "Reset", "qa")
    r = client.post("/forgot-password", data={"email": "reset-me@x.test"},
                    follow_redirects=True)
    assert "réinitialisation a été envoyé" in r.get_data(as_text=True)
    mails = database.list_outbox()
    reset = [m for m in mails if m["recipient"] == "reset-me@x.test"
             and "Réinitialisation" in m["subject"]]
    assert reset, "un e-mail de réinitialisation doit être en boîte"
    import re
    link = re.search(r"https?://[^\"]+/reset-password/([^\"]+)", reset[0]["body_html"])
    assert link, "l'e-mail doit contenir le lien de réinitialisation"
    token = link.group(1)
    uid = database.get_user_by_credentials("reset-me@x.test", "pw-before")
    assert uid is not None
    r = client.post(f"/reset-password/{token}",
                    data={"password": "new-pw-ok", "confirm": "new-pw-ok"},
                    follow_redirects=True)
    assert database.get_user_by_credentials("reset-me@x.test", "new-pw-ok") is not None
    assert database.get_user_by_credentials("reset-me@x.test", "pw-before") is None


def test_forgot_password_unknown_email_neutral(client, _init_db):
    r = client.post("/forgot-password", data={"email": "inconnu@x.test"},
                    follow_redirects=True)
    assert "Si cette adresse existe" in r.get_data(as_text=True)


def test_reset_invalid_token(client, _init_db):
    r = client.post("/reset-password/not-a-real-token",
                    data={"password": "abc", "confirm": "abc"},
                    follow_redirects=True)
    assert "Lien invalide ou expiré" in r.get_data(as_text=True)


def test_users_page_has_delete_button(client, two_users):
    _login(client, "admin@example.com", "admin123")
    body = client.get("/users").get_data(as_text=True)
    assert "Supprimer" in body
    assert "/delete" in body


def test_delete_user_works(client, _init_db):
    from sdet_app import database
    uid = database.create_user("delete-me@x.test", "pw", "DeleteMe", "qa")
    _login(client, "admin@example.com", "admin123")
    r = client.post(f"/users/{uid}/delete", follow_redirects=True)
    assert "Utilisateur supprimé" in r.get_data(as_text=True)
    assert database.get_user_by_id(uid) is None
    r = _login(client, "delete-me@x.test", "pw")
    assert "Identifiants incorrects" in r.get_data(as_text=True)


def test_cannot_delete_self(client, _init_db):
    from sdet_app import database
    _login(client, "admin@example.com", "admin123")
    admin = database.get_user_by_id(1)
    r = client.post(f"/users/{admin.id}/delete", follow_redirects=True)
    assert "votre propre compte" in r.get_data(as_text=True)
    assert database.get_user_by_id(1) is not None


def test_cannot_delete_last_admin(client, _init_db):
    from sdet_app import database
    _login(client, "admin@example.com", "admin123")
    # ne conserver qu'un seul admin actif
    for u in database.list_users():
        if u.role == "admin" and u.id != 1:
            database.set_user_role(u.id, "qa")
    admin = database.get_user_by_id(1)
    r = client.post(f"/users/{admin.id}/delete")
    assert database.get_user_by_id(1) is not None
    # un second admin peut être supprimé
    database.create_user("second-admin@x.test", "pw", "Second", "admin")
    target = database.get_user_by_id(
        [u for u in database.list_users() if u.email == "second-admin@x.test"][0].id)
    client.post(f"/users/{target.id}/delete")
    assert database.get_user_by_id(target.id) is None


def test_settings_hidden_and_blocked_for_qas(client, two_users):
    alice, _ = two_users
    _login(client, alice, "pwA")
    body = client.get("/dashboard").get_data(as_text=True)
    assert "Paramètres" not in body
    r = client.get("/settings")
    assert r.status_code == 302
    r = client.post("/settings", data={"max_pages": "9"})
    assert r.status_code == 302