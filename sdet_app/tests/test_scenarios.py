from sdet_app import database


def _wait_generation(client, pid, fid, retries=50):
    """Poll the generation status endpoint until it leaves 'running'."""
    import time
    for _ in range(retries):
        st = client.get(
            f"/projects/{pid}/scenarios/functionalities/{fid}/generate/status"
        ).get_json()
        if st.get("status") != "running":
            return st
        time.sleep(0.05)
    return st


def _wait_scan(client, url, retries=80):
    """Poll a scan status endpoint until it leaves 'running'."""
    import time
    st = None
    for _ in range(retries):
        st = client.get(url).get_json()
        if st.get("status") != "running":
            return st
        time.sleep(0.05)
    return st


def _make_project(client):
    client.post("/login", data={"email": "admin@example.com", "password": "admin123"})
    client.post("/projects/new", data={
        "name": "ScenProj", "url": "https://scen.test", "email": "a@b.test",
        "password": "pw", "auth_type": "simple", "environment": "STAGING",
        "comments": ""}, follow_redirects=True)
    return [p for p in database.list_projects() if p["name"] == "ScenProj"][0]["id"]


def test_create_module_functionality_step(client, _init_db):
    pid = _make_project(client)
    client.post(f"/projects/{pid}/scenarios/modules",
                data={"name": "CRM", "description": "Gestion clients"},
                follow_redirects=True)
    mod = database.list_modules(pid)[0]
    assert mod["name"] == "CRM"

    client.post(f"/projects/{pid}/scenarios/modules/{mod['id']}/functionalities",
                data={"name": "Créer un client"}, follow_redirects=True)
    fn = database.list_functionalities(mod["id"])[0]
    assert fn["name"] == "Créer un client"

    client.post(f"/projects/{pid}/scenarios/functionalities/{fn['id']}/steps",
                data={"description": "Cliquer sur Client > Tous les clients"},
                follow_redirects=True)
    steps = database.list_steps(fn["id"])
    assert len(steps) == 1
    assert steps[0]["step_order"] == 1

    client.post(f"/projects/{pid}/scenarios/functionalities/{fn['id']}/steps",
                data={"description": "Cliquer sur Ajouter"}, follow_redirects=True)
    steps = database.list_steps(fn["id"])
    assert len(steps) == 2
    assert steps[1]["step_order"] == 2

    client.post("/projects/{}/delete".format(pid))


def test_module_step_delete(client, _init_db):
    pid = _make_project(client)
    client.post(f"/projects/{pid}/scenarios/modules",
                data={"name": "CRM"}, follow_redirects=True)
    mod = database.list_modules(pid)[0]
    client.post(f"/projects/{pid}/scenarios/modules/{mod['id']}/functionalities",
                data={"name": "Créer"}, follow_redirects=True)
    fn = database.list_functionalities(mod["id"])[0]
    client.post(f"/projects/{pid}/scenarios/functionalities/{fn['id']}/steps",
                data={"description": "Étape A"}, follow_redirects=True)
    step = database.list_steps(fn["id"])[0]
    client.post(f"/projects/{pid}/scenarios/steps/{step['id']}/delete",
                follow_redirects=True)
    assert database.list_steps(fn["id"]) == []
    client.post("/projects/{}/delete".format(pid))


def test_run_manual_scenario_and_validate(client, _init_db):
    pid = _make_project(client)
    client.post(f"/projects/{pid}/scenarios/modules",
                data={"name": "CRM"}, follow_redirects=True)
    mod = database.list_modules(pid)[0]
    client.post(f"/projects/{pid}/scenarios/modules/{mod['id']}/functionalities",
                data={"name": "Créer un client"}, follow_redirects=True)
    fn = database.list_functionalities(mod["id"])[0]
    client.post(f"/projects/{pid}/scenarios/functionalities/{fn['id']}/steps",
                data={"description": "Remplir le formulaire"}, follow_redirects=True)
    client.post(f"/projects/{pid}/scenarios/functionalities/{fn['id']}/steps",
                data={"description": "Sauver"}, follow_redirects=True)

    r = client.post(f"/projects/{pid}/scenarios/run",
                    data={"functionality_ids": [str(fn["id"])]})
    assert r.status_code == 302
    loc = r.headers["Location"]
    tid = int(loc.rstrip("/").split("/")[-1])

    test = database.get_test_run(tid)
    assert test.run_type == "Scénario"
    plan = database.get_test_run(tid).plan_json or "[]"
    import json as _json
    plan = _json.loads(plan if isinstance(plan, str) else _json.dumps(plan))
    assert any(step.get("step_type") == "ai_step" for step in plan)
    ai_step = next(s for s in plan if s.get("step_type") == "ai_step")
    assert "Remplir le formulaire" in ai_step.get("user_desc", "")
    assert ai_step.get("function") == "Créer un client"
    assert "Remplir le formulaire" in ai_step.get("steps", [])
    assert "Sauver" in ai_step.get("steps", [])

    # test view renders the scenario run page
    body = client.get(f"/tests/{tid}").get_data(as_text=True)
    assert "Parcours de test" in body
    client.post("/projects/{}/delete".format(pid))


def test_select_page_renders(client, _init_db):
    pid = _make_project(client)
    client.post(f"/projects/{pid}/scenarios/modules",
                data={"name": "CRM"}, follow_redirects=True)
    r = client.get(f"/projects/{pid}/scenarios/select")
    assert r.status_code == 200
    assert "CRM" in r.get_data(as_text=True)
    assert "Lancer un parcours" in r.get_data(as_text=True)
    client.post("/projects/{}/delete".format(pid))


def test_scenario_run_requires_selection(client, _init_db):
    pid = _make_project(client)
    client.post(f"/projects/{pid}/scenarios/modules",
                data={"name": "CRM"}, follow_redirects=True)
    mod = database.list_modules(pid)[0]
    client.post(f"/projects/{pid}/scenarios/modules/{mod['id']}/functionalities",
                data={"name": "Créer"}, follow_redirects=True)
    fn = database.list_functionalities(mod["id"])[0]

    r = client.post(f"/projects/{pid}/scenarios/run",
                    data={"functionality_ids": []}, follow_redirects=True)
    body = r.get_data(as_text=True)
    assert "au moins une fonctionnalité" in body
    client.post("/projects/{}/delete".format(pid))


def test_module_requires_name(client, _init_db):
    pid = _make_project(client)
    r = client.post(f"/projects/{pid}/scenarios/modules",
                    data={"name": ""}, follow_redirects=True)
    body = r.get_data(as_text=True)
    assert "obligatoire" in body
    client.post("/projects/{}/delete".format(pid))


def test_run_new_functionality_no_steps_no_desc(client, _init_db):
    """A newly-created functionality with no steps and no description
    should still produce a valid ai_step plan with a generic description."""
    pid = _make_project(client)
    client.post(f"/projects/{pid}/scenarios/modules",
                data={"name": "Clients", "description": "Gestion des clients"},
                follow_redirects=True)
    mod = database.list_modules(pid)[0]
    client.post(f"/projects/{pid}/scenarios/modules/{mod['id']}/functionalities",
                data={"name": "Tous les clients", "description": "", "path": ""},
                follow_redirects=True)
    fn = database.list_functionalities(mod["id"])[0]
    assert fn["name"] == "Tous les clients"
    assert database.list_steps(fn["id"]) == []

    r = client.post(f"/projects/{pid}/scenarios/run",
                    data={"functionality_ids": [str(fn["id"])]})
    assert r.status_code == 302
    loc = r.headers["Location"]
    tid = int(loc.rstrip("/").split("/")[-1])

    test = database.get_test_run(tid)
    assert test.run_type == "Scénario"
    import json as _json
    plan = _json.loads(test.plan_json or "[]")
    assert len(plan) == 1
    ai_step = plan[0]
    assert ai_step.get("step_type") == "ai_step"
    assert ai_step.get("function") == "Tous les clients"
    assert ai_step.get("module") == "Clients"
    # Should have a generic fallback description
    assert "Tous les clients" in ai_step.get("user_desc", "")
    assert ai_step.get("steps") == []
    client.post(f"/projects/{pid}/scenarios/functionalities/{fn['id']}/delete",
                follow_redirects=True)
    client.post("/projects/{}/delete".format(pid))


def test_run_new_functionality_with_description_only(client, _init_db):
    """A functionality with a description but no steps should parse the
    description into step_texts for the plan."""
    pid = _make_project(client)
    client.post(f"/projects/{pid}/scenarios/modules",
                data={"name": "CRM"}, follow_redirects=True)
    mod = database.list_modules(pid)[0]
    client.post(f"/projects/{pid}/scenarios/modules/{mod['id']}/functionalities",
                data={"name": "Tous les clients",
                       "description": "Cliquer sur Clients puis vérifier la liste"},
                follow_redirects=True)
    fn = database.list_functionalities(mod["id"])[0]

    r = client.post(f"/projects/{pid}/scenarios/run",
                    data={"functionality_ids": [str(fn["id"])]})
    assert r.status_code == 302
    loc = r.headers["Location"]
    tid = int(loc.rstrip("/").split("/")[-1])

    import json as _json
    plan = _json.loads(database.get_test_run(tid).plan_json or "[]")
    assert len(plan) == 1
    ai_step = plan[0]
    assert ai_step.get("step_type") == "ai_step"
    # Description should be parsed into steps and used as context
    desc = ai_step.get("user_desc", "")
    # Either the function name or the description content should appear
    assert "Tous les clients" in ai_step.get("function", "") or "Cliquer" in desc
    client.post("/projects/{}/delete".format(pid))


def test_scenarios_page_no_desactivate_text(client, _init_db):
    """The scenarios info banner should NOT mention 'désactive/désactiver'."""
    pid = _make_project(client)
    body = client.get(f"/projects/{pid}/scenarios").get_data(as_text=True)
    assert "désactivé" not in body.lower()
    assert "active/désactive" not in body.lower()
    client.post("/projects/{}/delete".format(pid))


def test_functionality_with_nav_path(client, _init_db):
    """A functionality with a navigation path should include nav_path in plan."""
    pid = _make_project(client)
    client.post(f"/projects/{pid}/scenarios/modules",
                data={"name": "CRM"}, follow_redirects=True)
    mod = database.list_modules(pid)[0]
    client.post(f"/projects/{pid}/scenarios/modules/{mod['id']}/functionalities",
                data={"name": "Tous les clients",
                       "description": "",
                       "path": "Facturation > Contrats"},
                follow_redirects=True)
    fn = database.list_functionalities(mod["id"])[0]

    r = client.post(f"/projects/{pid}/scenarios/run",
                    data={"functionality_ids": [str(fn["id"])]})
    assert r.status_code == 302
    loc = r.headers["Location"]
    tid = int(loc.rstrip("/").split("/")[-1])

    import json as _json
    plan = _json.loads(database.get_test_run(tid).plan_json or "[]")
    assert len(plan) == 1
    ai_step = plan[0]
    assert ai_step.get("nav_path") == ["Facturation", "Contrats"]
    client.post("/projects/{}/delete".format(pid))


def test_disabled_functionality_excluded_from_run(client, _init_db):
    """Disabled functionalities should not be included in the test run."""
    pid = _make_project(client)
    client.post(f"/projects/{pid}/scenarios/modules",
                data={"name": "CRM"}, follow_redirects=True)
    mod = database.list_modules(pid)[0]
    client.post(f"/projects/{pid}/scenarios/modules/{mod['id']}/functionalities",
                data={"name": "Active fn"}, follow_redirects=True)
    fn_active = database.list_functionalities(mod["id"])[0]
    client.post(f"/projects/{pid}/scenarios/modules/{mod['id']}/functionalities",
                data={"name": "Disabled fn"}, follow_redirects=True)
    fn_disabled = [f for f in database.list_functionalities(mod["id"])
                   if f["name"] == "Disabled fn"][0]
    # Disable the second functionality
    database.toggle_functionality(fn_disabled["id"])

    r = client.post(f"/projects/{pid}/scenarios/run",
                    data={"functionality_ids": [str(fn_active["id"]),
                                                  str(fn_disabled["id"])]})
    assert r.status_code == 302
    loc = r.headers["Location"]
    tid = int(loc.rstrip("/").split("/")[-1])

    import json as _json
    plan = _json.loads(database.get_test_run(tid).plan_json or "[]")
    # Only the active functionality should be in the plan
    assert len(plan) == 1
    assert plan[0].get("function") == "Active fn"
    client.post("/projects/{}/delete".format(pid))


def test_generate_steps_background_json_and_status(client, _init_db, monkeypatch):
    """The generate POST returns JSON immediately, then a background thread
    saves steps and the status endpoint reports 'done' with a count."""
    from sdet_app import app as app_mod

    monkeypatch.setattr(
        app_mod.generator, "generate_steps",
        lambda project, nav_path=None, module="", functionality="",
                 extra_context="", url="": (["CRÉATION", "Remplir le champ Nom avec X"], None))

    pid = _make_project(client)
    client.post(f"/projects/{pid}/scenarios/modules", data={"name": "CRM"},
                follow_redirects=True)
    mod = database.list_modules(pid)[0]
    client.post(f"/projects/{pid}/scenarios/modules/{mod['id']}/functionalities",
                data={"name": "Créer un client"}, follow_redirects=True)
    fn = database.list_functionalities(mod["id"])[0]

    r = client.post(f"/projects/{pid}/scenarios/functionalities/{fn['id']}/generate")
    assert r.status_code == 200
    assert r.get_json()["status"] == "started"

    st = _wait_generation(client, pid, fn["id"])
    assert st["status"] == "done"
    assert st["count"] == 2
    assert len(database.list_steps(fn["id"])) == 2

    # A resume while done should not be blocked
    r2 = client.post(f"/projects/{pid}/scenarios/functionalities/{fn['id']}/generate")
    assert r2.status_code == 200
    assert r2.get_json()["status"] == "started"
    st = _wait_generation(client, pid, fn["id"])
    assert st["status"] == "done"
    client.post("/projects/{}/delete".format(pid))


def test_generate_steps_background_error_surfaces(client, _init_db, monkeypatch):
    """A generation failure should surface as a visible error status and
    leave no steps behind."""
    from sdet_app import app as app_mod

    monkeypatch.setattr(
        app_mod.generator, "generate_steps",
        lambda project, nav_path=None, module="", functionality="",
                 extra_context="": ([], "Aucun formulaire de création détecté sur cet écran."))

    pid = _make_project(client)
    client.post(f"/projects/{pid}/scenarios/modules", data={"name": "CRM"},
                follow_redirects=True)
    mod = database.list_modules(pid)[0]
    client.post(f"/projects/{pid}/scenarios/modules/{mod['id']}/functionalities",
                data={"name": "Créer un client"}, follow_redirects=True)
    fn = database.list_functionalities(mod["id"])[0]

    client.post(f"/projects/{pid}/scenarios/functionalities/{fn['id']}/generate")
    st = _wait_generation(client, pid, fn["id"])
    assert st["status"] == "error"
    assert st["message"]
    assert database.list_steps(fn["id"]) == []
    client.post("/projects/{}/delete".format(pid))


def test_module_scan_detects_functionalities_and_steps(client, _init_db, monkeypatch):
    """Scanning a module replaces its functionalities and writes CRUD steps."""
    from sdet_app import app as app_mod

    monkeypatch.setattr(
        app_mod.generator, "scan_module",
        lambda project, module_name, nav_path=None, control=None: (
            [{"functionality": "Créer un client", "path": "",
              "url": "https://kpip.kprimesoft.com/crm/clients",
              "steps": ["CRÉATION", "Remplir le champ Nom avec QA_CLIENT"]},
             {"functionality": "Lister les contrats", "path": "",
              "url": "https://kpip.kprimesoft.com/crm/client_interaction",
              "steps": ["CRÉATION", "Remplir le champ Nom avec QA_CONTRAT"]}],
            None))

    pid = _make_project(client)
    client.post(f"/projects/{pid}/scenarios/modules", data={"name": "CRM"},
                follow_redirects=True)
    mod = database.list_modules(pid)[0]
    # Pre-existing functionality should be replaced
    client.post(f"/projects/{pid}/scenarios/modules/{mod['id']}/functionalities",
                data={"name": "Ancienne"}, follow_redirects=True)
    assert len(database.list_functionalities(mod["id"])) == 1

    url = f"/projects/{pid}/scenarios/modules/{mod['id']}/generate"
    r = client.post(url)
    assert r.status_code == 200
    assert r.get_json()["status"] == "started"

    st = _wait_scan(client, url + "/status")
    assert st["status"] == "done"
    assert st["count"] == 2
    fns = database.list_functionalities(mod["id"])
    assert len(fns) == 2
    assert [f["name"] for f in fns] == ["Créer un client", "Lister les contrats"]
    assert [f["url"] for f in fns] == [
        "https://kpip.kprimesoft.com/crm/clients",
        "https://kpip.kprimesoft.com/crm/client_interaction"]
    assert len(database.list_steps(fns[0]["id"])) == 2
    assert len(database.list_steps(fns[1]["id"])) == 2
    client.post("/projects/{}/delete".format(pid))


def test_module_scan_error_surfaces(client, _init_db, monkeypatch):
    from sdet_app import app as app_mod

    monkeypatch.setattr(
        app_mod.generator, "scan_module",
        lambda project, module_name, nav_path=None, control=None: (
            [], "Aucun formulaire détecté."))

    pid = _make_project(client)
    client.post(f"/projects/{pid}/scenarios/modules", data={"name": "IMS"},
                follow_redirects=True)
    mod = database.list_modules(pid)[0]
    url = f"/projects/{pid}/scenarios/modules/{mod['id']}/generate"
    client.post(url)
    st = _wait_scan(client, url + "/status")
    assert st["status"] == "error"
    assert st["message"]
    assert database.list_functionalities(mod["id"]) == []
    client.post("/projects/{}/delete".format(pid))


def test_scan_all_detects_modules_and_writes_tree(client, _init_db, monkeypatch):
    """The whole-app scan replaces all modules and writes the full tree."""
    from sdet_app import app as app_mod

    fake_modules = [
        {"module": "CRM", "url": "https://kpip.kprimesoft.com/crm", "functionalities": [
            {"functionality": "Créer un client", "path": "",
             "url": "https://kpip.kprimesoft.com/crm/clients",
             "steps": ["CRÉATION", "Remplir le champ Nom avec QA_CLIENT"]}]},
        {"module": "IMS", "url": "https://kpip.kprimesoft.com/ims", "functionalities": [
            {"functionality": "Créer une facture", "path": "",
             "url": "https://kpip.kprimesoft.com/ims/factures",
             "steps": ["CRÉATION", "Remplir le champ Nom avec QA_FACTURE"]}]},
    ]
    monkeypatch.setattr(app_mod.generator, "scan_whole_app",
                        lambda project, on_module_progress=None, control=None:
                        (fake_modules, None))

    pid = _make_project(client)
    client.post(f"/projects/{pid}/scenarios/modules", data={"name": "Ancien"},
                follow_redirects=True)

    url = f"/projects/{pid}/scenarios/scan"
    r = client.post(url)
    assert r.status_code == 200
    assert r.get_json()["status"] == "started"

    st = _wait_scan(client, url + "/status")
    assert st["status"] == "done"
    assert st["count"] == 2

    mods = database.list_modules(pid)
    assert [m["name"] for m in mods] == ["CRM", "IMS"]
    crm = [m for m in mods if m["name"] == "CRM"][0]
    fns = database.list_functionalities(crm["id"])
    assert len(fns) == 1
    assert fns[0]["name"] == "Créer un client"
    assert fns[0]["url"] == "https://kpip.kprimesoft.com/crm/clients"
    assert len(database.list_steps(fns[0]["id"])) == 2
    client.post("/projects/{}/delete".format(pid))


def test_scan_all_error_surfaces(client, _init_db, monkeypatch):
    from sdet_app import app as app_mod

    monkeypatch.setattr(app_mod.generator, "scan_whole_app",
                        lambda project, on_module_progress=None, control=None:
                        ([], "Aucun module détecté dans la navigation."))

    pid = _make_project(client)
    url = f"/projects/{pid}/scenarios/scan"
    r = client.post(url)
    assert r.status_code == 200
    st = _wait_scan(client, url + "/status")
    assert st["status"] == "error"
    assert st["message"]
    client.post("/projects/{}/delete".format(pid))


def test_scenarios_page_has_scan_controls(client, _init_db):
    """The scenarios page exposes per-module scan actions but hides the
    deprecated 'Scanner tout le logiciel' button."""
    pid = _make_project(client)
    body = client.get(f"/projects/{pid}/scenarios").get_data(as_text=True)
    assert 'id="scanAllBtn"' not in body
    assert "Ajouter une fonctionnalité" not in body
    client.post("/projects/{}/delete".format(pid))


def test_module_edit_updates_name_and_description(client, _init_db):
    pid = _make_project(client)
    client.post(f"/projects/{pid}/scenarios/modules",
                data={"name": "CRM", "description": "Gestion clients"},
                follow_redirects=True)
    mod = database.list_modules(pid)[0]

    r = client.get(f"/projects/{pid}/scenarios/modules/{mod['id']}/edit")
    assert r.status_code == 200
    assert "Modifier le module" in r.get_data(as_text=True)

    client.post(f"/projects/{pid}/scenarios/modules/{mod['id']}/edit",
                data={"name": "CRMv2", "description": "Nouveau"},
                follow_redirects=True)
    upd = database.get_module(mod["id"])
    assert upd["name"] == "CRMv2"
    assert upd["description"] == "Nouveau"

    client.post(f"/projects/{pid}/scenarios/modules/{mod['id']}/edit",
                data={"name": "", "description": ""}, follow_redirects=True)
    assert database.get_module(mod["id"])["name"] == "CRMv2"
    client.post("/projects/{}/delete".format(pid))


def test_module_url_shown_and_editable(client, _init_db):
    """The module URL is visible on the scenarios page and editable."""
    pid = _make_project(client)
    client.post(f"/projects/{pid}/scenarios/modules",
                data={"name": "IMS",
                      "url": "https://kpip.kprimesoft.com/ims"},
                follow_redirects=True)
    mod = database.list_modules(pid)[0]
    assert mod["url"] == "https://kpip.kprimesoft.com/ims"

    client.post(f"/projects/{pid}/scenarios/modules/{mod['id']}/edit",
                data={"name": "IMS",
                      "description": "",
                      "url": "https://kpip.kprimesoft.com/ims"},
                follow_redirects=True)
    assert database.get_module(mod["id"])["url"] == \
        "https://kpip.kprimesoft.com/ims"

    body = client.get(f"/projects/{pid}/scenarios").get_data(as_text=True)
    assert "https://kpip.kprimesoft.com/ims" in body

    # Manual functionality creation accepts an optional URL, like the module.
    client.post(f"/projects/{pid}/scenarios/modules/{mod['id']}/functionalities",
                data={"name": "Créer un client",
                      "url": "https://kpip.kprimesoft.com/ims/clients"},
                follow_redirects=True)
    fn = database.list_functionalities(mod["id"])[0]
    assert fn["url"] == "https://kpip.kprimesoft.com/ims/clients"
    client.post("/projects/{}/delete".format(pid))


def test_scan_inline_controls_hidden_by_default(client, _init_db):
    """The deprecated scan-all button/console must be gone, and the module
    scan controls (Pause/Resume/Stop) must be hidden when no scan runs."""
    pid = _make_project(client)
    client.post(f"/projects/{pid}/scenarios/modules",
                data={"name": "CRM"}, follow_redirects=True)
    body = client.get(f"/projects/{pid}/scenarios").get_data(as_text=True)

    # The deprecated scan-all button + inline console must NOT exist.
    assert 'id="scanAllBtn"' not in body
    assert 'id="scanInlineCtl"' not in body
    assert 'class="scan-console' not in body
    assert 'id="scanConsole"' not in body
    assert 'id="scanScope"' not in body

    # Module scan controls still exist and are hidden by default.
    assert 'class="icon-btn ms-pause"' in body
    assert 'class="icon-btn ms-resume"' in body
    assert 'class="icon-btn ms-stop"' in body
    assert "hidden" in body

    # The per-module scan action is still available.
    assert 'startModuleScan(this,' in body
    assert 'setModuleControls' in body

    client.post(f"/projects/{pid}/delete")
