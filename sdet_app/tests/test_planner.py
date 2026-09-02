from sdet_app.sdet import planner
from sdet_app.sdet import forms
from sdet_app.sdet import reporter


class FakePage:
    def __init__(self, url, name):
        self.url = url
        self.name = name


def _select(labels, env="STAGING"):
    pages = [FakePage("/clients", "Clients")]
    sel = [{"page_url": "/clients", "page_name": "Clients",
            "label": l, "action_type": "button"} for l in labels]
    return planner.build_scenarios(pages, [], sel, env)


def test_planner_crud_order():
    steps = _select(["Ajouter un client", "Rechercher", "Visualiser",
                     "Modifier", "Supprimer"])
    order = [s["step_type"] for s in steps]
    assert order == ["access", "create", "search_create", "read",
                     "update", "delete_last"]


def test_planner_access_already_first_for_generic():
    steps = _select(["Exporter"])
    assert steps[0]["step_type"] == "access"
    assert steps[-1]["step_type"] == "generic"


def test_planner_production_full_crud():
    steps = _select(["Ajouter un client", "Supprimer"], env="PRODUCTION")
    types = [s["step_type"] for s in steps]
    assert "create" in types
    assert "delete_last" in types


def test_planner_sensitive_default_off():
    steps = _select(["Payer la commande"])
    generic = [s for s in steps if s["step_type"] == "generic"]
    # sensitive generic actions are disabled by default
    src = generic[0]["source_action"]
    assert src.get("_sensitive") is True


def test_forms_generate_unique_data():
    suffix = "4821"
    data = forms.generate_data({"name": "email", "type": "email"}, suffix)
    assert data == f"qa{suffix}@example.test"
    data2 = forms.generate_data({"name": "nom", "type": "text"}, suffix)
    assert str(suffix) in data2


def test_report_success_percent():
    counters = {"total": 4, "passed": 3, "failed": 0, "warning": 1, "skipped": 0}
    assert reporter.success_percent(counters) == 75.0


def test_report_group_by_module():
    results = [{"module": "Clients", "status": "PASS", "function": "A"},
               {"module": "Clients", "status": "FAIL", "function": "B"},
               {"module": "Factures", "status": "PASS", "function": "C"}]
    mods = reporter.group_by_module(results)
    assert set(mods.keys()) == {"Clients", "Factures"}
    stats = reporter.module_stats(mods)
    clients = [s for s in stats if s["name"] == "Clients"][0]
    assert clients["pass"] == 1 and clients["fail"] == 1
