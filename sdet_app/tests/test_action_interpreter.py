"""Unit tests for the structured action library + interpreter contract."""
from sdet_app.sdet.action_library import (
    ACTION_TYPES, build_structured_step, list_action_types,
    build_crud_actions,
)
from sdet_app.sdet.action_interpreter import ActionInterpreter


def test_all_action_types_have_labels():
    for key, defn in ACTION_TYPES.items():
        assert defn["label"], key
        assert defn["description"], key


def test_list_action_types_exposes_fields():
    types = {t["key"]: t for t in list_action_types()}
    assert types["REMPLIR"]["fields"] == ["target", "value"]
    assert types["CLIQUEER"]["fields"] == ["target"]


def test_build_structured_step_generates_description():
    step = build_structured_step("REMPLIR", "Nom", "QA_TEST_2509")
    assert step["action_type"] == "REMPLIR"
    assert step["target"] == "Nom"
    assert step["value"] == "QA_TEST_2509"
    assert "Remplir" in step["description"]


def test_crud_actions_have_navigation_and_verification():
    actions = build_crud_actions("https://x.test/crm/clients")
    assert actions[0]["action_type"] == "NAVIGUER"
    assert actions[-1]["action_type"] == "VERIFIER"
    assert any(a["action_type"] == "REMPLIR" for a in actions)
    assert any(a["action_type"] == "CLIQUEER" for a in actions)


# ---------------------------------------------------------------------------
# Interpreter execution (fake page)
# ---------------------------------------------------------------------------

class _FakeLocator:
    def __init__(self, page):
        self.page = page
        self.first = self

    def count(self):
        return 1

    def is_visible(self):
        return True

    def click(self, timeout=0):
        self.page.clicked = True

    def wait_for(self, state="visible", timeout=0):
        pass

    def fill(self, value):
        self.page.filled = value

    def check(self, timeout=0):
        self.page.checked = True

    def uncheck(self, timeout=0):
        self.page.unchecked = True


def _get_by_role(page, role, name, exact=False):
    return _FakeLocator(page)


def _get_by_label(page, label, exact=False):
    return _FakeLocator(page)


class _FakePage:
    url = "https://x.test/login"

    def __init__(self, url="https://x.test/login"):
        self.url = url
        self.clicked = False
        self.filled = None

    def goto(self, url, timeout=0, wait_until=None):
        self.url = url
        return type("R", (), {"status": 200})()

    def query_selector(self, sel):
        return None

    def query_selector_all(self, sel):
        return []

    def evaluate(self, script, arg=None):
        if "responseStatus" in script:
            return 200
        return False

    def wait_for_timeout(self, ms):
        pass

    def wait_for_load_state(self, state, timeout=0):
        pass

    def get_by_text(self, text, exact=False):
        return _FakeLocator(self)

    def get_by_role(self, role, name, exact=False):
        return _get_by_role(self, role, name, exact)

    def get_by_label(self, label, exact=False):
        return _get_by_label(self, label, exact)

    def wait_for_selector(self, sel, state="visible", timeout=0):
        raise Exception("not found")

    def keyboard(self):
        return type("K", (), {"press": lambda self, k: None})()


def test_interpreter_navigate():
    page = _FakePage("https://x.test/old")
    interp = ActionInterpreter(page)
    status, msg = interp.execute(
        {"action_type": "NAVIGUER", "target": "https://x.test/clients",
         "value": "", "expected": "Page chargée"})
    assert status == "PASS"
    assert page.url == "https://x.test/clients"


def test_interpreter_click_and_fill():
    page = _FakePage()
    interp = ActionInterpreter(page)
    status, msg = interp.execute(
        {"action_type": "CLIQUEER", "target": "Ajouter", "value": "",
         "expected": "Formulaire ouvert"})
    assert status == "PASS"
    assert page.clicked
    status, msg = interp.execute(
        {"action_type": "REMPLIR", "target": "Nom", "value": "QA_T",
         "expected": "Champ rempli"})
    assert status == "PASS"


def test_interpreter_unknown_action_is_warning():
    page = _FakePage()
    interp = ActionInterpreter(page)
    status, msg = interp.execute(
        {"action_type": "BOGUS", "target": "", "value": "",
         "expected": ""})
    assert status == "WARNING"


def test_interpreter_verify_missing_target_passes():
    page = _FakePage()
    interp = ActionInterpreter(page)
    status, msg = interp.execute(
        {"action_type": "VERIFIER", "target": "", "value": "",
         "expected": ""})
    assert status == "PASS"