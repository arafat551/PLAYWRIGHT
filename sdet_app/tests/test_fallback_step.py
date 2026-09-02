"""Test the Playwright fallback step execution — works WITHOUT OpenAI.

Verifies that _parse_step_intent, _match_element_by_label, and
_fallback_step can drive the browser through a scenario purely with
Playwright, no API key needed.
"""
import pytest
from playwright.sync_api import sync_playwright


# -----------------------------------------------------------------------
# Realistic page: IMS product_categories with Ajouter button + form
# -----------------------------------------------------------------------
_PAGE_HTML = """\
<!DOCTYPE html>
<html lang="fr">
<head><meta charset="utf-8"><title>IMS - Product Categories</title>
<style>
  * { box-sizing: border-box; margin: 0; padding: 0; }
  body { font-family: sans-serif; padding: 24px; }
  .page-header { display: flex; justify-content: space-between;
                 align-items: center; margin-bottom: 20px; }
  h1 { font-size: 22px; }
  .btn { display: inline-block; padding: 8px 16px; cursor: pointer;
         color: white; border: none; border-radius: 4px; font-size: 14px;
         text-decoration: none; }
  .btn-primary { background-color: #3182ce; }
  .btn-success { background-color: #38a169; }
  .btn-danger { background-color: #e53e3e; }
  table { width: 100%; border-collapse: collapse; margin-top: 16px; }
  th, td { padding: 10px 12px; text-align: left; border-bottom: 1px solid #e2e8f0; }
  th { background: #f7fafc; font-weight: 600; }
  /* Search bar */
  .search-bar { margin-bottom: 16px; }
  .search-bar input { padding: 8px 12px; border: 1px solid #ccc; border-radius: 4px;
                      width: 300px; font-size: 14px; }
  /* Add form (hidden by default, shown after click) */
  #add-form { display: none; margin-top: 16px; padding: 16px; background: #f7fafc;
              border-radius: 8px; border: 1px solid #e2e8f0; }
  #add-form label { display: block; margin-bottom: 4px; font-weight: 600; }
  #add-form input { padding: 6px 10px; border: 1px solid #ccc; border-radius: 4px;
                    width: 300px; margin-bottom: 12px; }
  #add-form .btn { margin-top: 8px; }
  #log { position: fixed; bottom: 10px; left: 24px; right: 24px;
         padding: 10px; background: #1a202c; color: #48bb78;
         font-family: monospace; }
</style>
</head>
<body>

<div class="page-header">
  <h1>Product Categories</h1>
  <div class="btn btn-primary" id="btn-add"
       onclick="document.getElementById('add-form').style.display='block';
                document.getElementById('log').textContent += 'ADDClicked;'">
    Ajouter
  </div>
</div>

<div class="search-bar">
  <input type="text" id="search-input" placeholder="Rechercher une catégorie">
</div>

<div id="add-form">
  <label for="field-name">Nom</label>
  <input type="text" id="field-name" name="name" placeholder="Nom de la catégorie">
  <label for="field-code">Code</label>
  <input type="text" id="field-code" name="code" placeholder="Code unique">
  <label for="field-desc">Description</label>
  <input type="text" id="field-desc" name="description" placeholder="Description">
  <div class="btn btn-success" id="btn-save"
       onclick="document.getElementById('log').textContent += 'SAVEClicked;'">
    Enregistrer
  </div>
</div>

<table id="categories-table">
  <thead>
    <tr><th>Nom</th><th>Code</th><th>Actions</th></tr>
  </thead>
  <tbody>
    <tr>
      <td>Électronique</td>
      <td>ELEC</td>
      <td>
        <div class="btn btn-primary"
             onclick="document.getElementById('log').textContent += 'VIEW_ELEC;'">Voir</div>
        <div class="btn btn-danger"
             onclick="document.getElementById('log').textContent += 'DEL_ELEC;'">Supprimer</div>
      </td>
    </tr>
    <tr id="row-cat-test" data-name="Catégorie Test">
      <td>Catégorie Test</td>
      <td>CAT001</td>
      <td>
        <a href="#" class="edit-service" title="Modifier les détails"
           onclick="document.getElementById('log').textContent += 'EDIT_TEST;'"><span class="fa fa-edit"></span></a>
        <a href="#" class="delete-service" title="Supprimer les détails"
           onclick="document.getElementById('log').textContent += 'DEL_TEST;'"><span class="fa fa-trash"></span></a>
      </td>
    </tr>
  </tbody>
</table>

<div id="log"></div>

</body>
</html>
"""


@pytest.fixture(scope="module")
def page_url(tmp_path_factory):
    d = tmp_path_factory.mktemp("html")
    f = d / "ims_categories.html"
    f.write_text(_PAGE_HTML, encoding="utf-8")
    return f.as_uri()


@pytest.fixture(scope="module")
def browser():
    with sync_playwright() as p:
        br = p.chromium.launch(headless=True)
        yield br
        br.close()


@pytest.fixture(scope="module")
def live_page(browser, page_url):
    page = browser.new_page(viewport={"width": 1280, "height": 900})
    page.goto(page_url)
    page.wait_for_load_state("domcontentloaded")
    return page


from sdet_app.sdet.runner import Runner


def _make_runner(page):
    class _FakeSession:
        def __init__(self, page):
            self.page = page
        def goto(self, url, timeout=20000):
            self.page.goto(url, timeout=timeout)
            return None
    class _FakeDB:
        def save_result(self, *a, **kw): pass
    runner = Runner.__new__(Runner)
    runner.session = _FakeSession(page)
    runner.page = page
    runner.db = _FakeDB()
    runner.project = {"url": "http://test.local"}
    runner.tid = 999
    runner._is_cancelled = lambda: False
    runner.suffix = None
    runner.entity_name = None
    runner.shot_seq = 0
    runner.last_fill_value = None
    return runner


def _get_log(page):
    return page.evaluate("document.getElementById('log').textContent")


def _reset_log(page):
    page.evaluate("document.getElementById('log').textContent = ''")


# -----------------------------------------------------------------------
# Test 1: _parse_step_intent parses French descriptions correctly
# -----------------------------------------------------------------------
class TestParseStepIntent:

    def test_parse_click_ajouter(self, live_page):
        runner = _make_runner(live_page)
        action, target, value = runner._parse_step_intent(
            "Cliquer sur le bouton Ajouter")
        assert action == "click"
        assert "ajouter" in target.lower() or "Ajouter" in target

    def test_parse_click_modifier(self, live_page):
        runner = _make_runner(live_page)
        action, target, value = runner._parse_step_intent(
            "Cliquer sur Modifier pour Électronique")
        assert action == "click"
        assert "modifier" in target.lower() or "Modifier" in target

    def test_parse_click_supprimer(self, live_page):
        runner = _make_runner(live_page)
        action, target, value = runner._parse_step_intent(
            "Clique sur le bouton Supprimer")
        assert action == "click"
        assert "supprimer" in target.lower() or "Supprimer" in target

    def test_parse_fill(self, live_page):
        runner = _make_runner(live_page)
        action, target, value = runner._parse_step_intent(
            "Remplir le champ Nom avec Catégorie Test")
        assert action == "fill"
        assert "nom" in target.lower()
        assert "catégorie test" in value.lower() or "Catégorie Test" in value

    def test_parse_fill_code(self, live_page):
        runner = _make_runner(live_page)
        action, target, value = runner._parse_step_intent(
            "Remplir le champ Code avec CAT001")
        assert action == "fill"
        assert "code" in target.lower()
        assert "CAT001" in value

    def test_parse_search(self, live_page):
        runner = _make_runner(live_page)
        action, target, value = runner._parse_step_intent(
            "Chercher la catégorie Électronique")
        assert action == "search"
        assert "électronique" in target.lower() or "Electronique" in target

    def test_parse_wait(self, live_page):
        runner = _make_runner(live_page)
        action, target, value = runner._parse_step_intent(
            "Attendre 2 secondes")
        assert action == "wait"

    def test_parse_enregistrer(self, live_page):
        runner = _make_runner(live_page)
        action, target, value = runner._parse_step_intent(
            "Cliquer sur Enregistrer")
        assert action == "click"
        assert "enregistrer" in target.lower() or "Enregistrer" in target

    def test_parse_select_with_value(self, live_page):
        runner = _make_runner(live_page)
        action, target, value = runner._parse_step_intent(
            "Sélectionner Type avec AXE")
        assert action == "select"
        assert target.strip().lower() == "type"
        assert value.strip().lower() == "axe"

    def test_parse_select_without_value(self, live_page):
        runner = _make_runner(live_page)
        action, target, value = runner._parse_step_intent(
            "Sélectionner Type")
        assert action == "select"
        assert "type" in target.lower()
        assert value == ""


# -----------------------------------------------------------------------
# Test 2: _match_element_by_label finds the right element
# -----------------------------------------------------------------------
class TestMatchElementByLabel:

    def test_exact_match(self, live_page):
        runner = _make_runner(live_page)
        state = runner._collect_page_state()
        idx, el = runner._match_element_by_label(state, "Ajouter")
        assert el is not None
        assert "ajouter" in el["label"].lower()

    def test_fuzzy_match(self, live_page):
        runner = _make_runner(live_page)
        state = runner._collect_page_state()
        # "Ajouter" should match even if element says "Ajouter"
        idx, el = runner._match_element_by_label(state, "bouton ajouter")
        assert el is not None

    def test_no_match(self, live_page):
        runner = _make_runner(live_page)
        state = runner._collect_page_state()
        idx, el = runner._match_element_by_label(state, "ZZZZNONEXISTENT")
        assert el is None

    def test_match_voir(self, live_page):
        runner = _make_runner(live_page)
        state = runner._collect_page_state()
        idx, el = runner._match_element_by_label(state, "Voir")
        assert el is not None
        assert "voir" in el["label"].lower()


# -----------------------------------------------------------------------
# Test 3: _fallback_step clicks the right button
# -----------------------------------------------------------------------
class TestFallbackStep:

    def test_click_add_button(self, live_page):
        """'Ajouter' should click the add button and show the form."""
        runner = _make_runner(live_page)
        _reset_log(live_page)
        # Hide the form first
        live_page.evaluate("document.getElementById('add-form').style.display='none'")
        ok = runner._fallback_step("Cliquer sur le bouton Ajouter")
        assert ok is True
        log = _get_log(live_page)
        assert "ADDClicked" in log

    def test_click_save_button(self, live_page):
        """'Enregistrer' should click the save button."""
        runner = _make_runner(live_page)
        _reset_log(live_page)
        # Show the form first
        live_page.evaluate("document.getElementById('add-form').style.display='block'")
        ok = runner._fallback_step("Cliquer sur Enregistrer")
        assert ok is True
        log = _get_log(live_page)
        assert "SAVEClicked" in log

    def test_fill_field(self, live_page):
        """'Remplir le champ Nom' should fill the input."""
        runner = _make_runner(live_page)
        # Show the form
        live_page.evaluate("document.getElementById('add-form').style.display='block'")
        ok = runner._fallback_step("Remplir le champ Nom avec Test Category")
        assert ok is True
        val = live_page.evaluate("document.getElementById('field-name').value")
        assert "Test Category" in val

    def test_search_field(self, live_page):
        """'Chercher' should type into the search input."""
        runner = _make_runner(live_page)
        ok = runner._fallback_step("Chercher la catégorie Électronique")
        assert ok is True
        val = live_page.evaluate("document.getElementById('search-input').value")
        # Search types the full term; 'électronique' should be present
        assert "lectronique" in val.lower()

    def test_wait_step(self, live_page):
        """'Attendre' should succeed without error."""
        runner = _make_runner(live_page)
        ok = runner._fallback_step("Attendre 2 secondes")
        assert ok is True

    def test_full_scenario_no_ai(self, live_page):
        """Simulate the full IMS > product_categories scenario:
        1. Click Ajouter
        2. Fill Nom
        3. Fill Code
        4. Click Enregistrer
        """
        runner = _make_runner(live_page)
        _reset_log(live_page)
        # Hide form
        live_page.evaluate("document.getElementById('add-form').style.display='none'")

        steps = [
            "Cliquer sur le bouton Ajouter",
            "Remplir le champ Nom avec Catégorie Test",
            "Remplir le champ Code avec CAT001",
            "Cliquer sur Enregistrer",
        ]

        for i, s in enumerate(steps, 1):
            ok = runner._fallback_step(s)
            assert ok is True, f"Step {i} failed: {s}"

        log = _get_log(live_page)
        assert "ADDClicked" in log
        assert "SAVEClicked" in log

        # Verify form fields were filled
        name_val = live_page.evaluate("document.getElementById('field-name').value")
        code_val = live_page.evaluate("document.getElementById('field-code').value")
        assert "Catégorie Test" in name_val
        assert "CAT001" in code_val


# -----------------------------------------------------------------------
# Test 4: row-action buttons (view/edit/delete in a table)
# -----------------------------------------------------------------------
class TestRowActions:

    def test_parse_visualiser_action_column(self, live_page):
        runner = _make_runner(live_page)
        action, target, value = runner._parse_step_intent(
            "Dans la partie action cliquer sur le bouton visualiser")
        assert action == "click"
        assert target.lower() == "visualiser"

    def test_parse_modifier_action_column(self, live_page):
        runner = _make_runner(live_page)
        action, target, value = runner._parse_step_intent(
            "Dans la partie action cliquer sur le bouton modifier")
        assert action == "click"
        assert target.lower() == "modifier"

    def test_row_action_group_mapping(self, live_page):
        runner = _make_runner(live_page)
        assert runner._row_action_group("modifier") is not None
        assert runner._row_action_group("supprimer") is not None
        assert runner._row_action_group("visualiser") is not None
        assert runner._row_action_group("editer") is not None
        # Normal buttons are NOT row actions
        assert runner._row_action_group("ajouter") is None
        assert runner._row_action_group("sauver") is None

    def test_click_row_action_targets_last_filled_row(self, live_page):
        runner = _make_runner(live_page)
        _reset_log(live_page)
        runner.last_fill_value = "Catégorie Test"
        ok = runner._fallback_step(
            "Dans la partie action cliquer sur le bouton modifier")
        assert ok is True
        log = _get_log(live_page)
        assert "EDIT_TEST" in log
        assert "EDIT_ELEC" not in log

    def test_click_delete_icon_button_in_row(self, live_page):
        runner = _make_runner(live_page)
        _reset_log(live_page)
        runner.last_fill_value = "Catégorie Test"
        ok = runner._fallback_step(
            "Dans la partie action cliquer sur le bouton supprimer")
        assert ok is True
        assert "DEL_TEST" in _get_log(live_page)

    def test_row_action_not_applied_to_normal_buttons(self, live_page):
        """Ajouter must still go through the normal click path, not the row
        action path."""
        runner = _make_runner(live_page)
        _reset_log(live_page)
        live_page.evaluate("document.getElementById('add-form').style.display='none'")
        ok = runner._fallback_step("Cliquer sur le bouton Ajouter")
        assert ok is True
        assert "ADDClicked" in _get_log(live_page)


# -----------------------------------------------------------------------
# Test 5: verify-row primitive (Option C "vérifier que la ligne contient X")
# -----------------------------------------------------------------------
class TestVerifyRow:

    def test_parse_verify_contient(self, live_page):
        runner = _make_runner(live_page)
        action, target, value = runner._parse_step_intent(
            "Vérifier que la ligne Contient Catégorie QA_0915")
        assert action == "verify"
        assert "catégorie qa_0915" in target.lower()

    def test_parse_verify_apparait(self, live_page):
        runner = _make_runner(live_page)
        action, target, value = runner._parse_step_intent(
            "Vérifier que Catégorie QA_0915 apparaît dans la liste")
        assert action == "verify"
        assert target.lower() == "catégorie qa_0915"

    def test_parse_verify_presence(self, live_page):
        runner = _make_runner(live_page)
        action, target, value = runner._parse_step_intent(
            "Vérifier la présence de Moto dans le tableau")
        assert action == "verify"
        assert target.lower() == "moto"

    def test_verify_found(self, live_page):
        runner = _make_runner(live_page)
        ok = runner._fallback_step("Vérifier que la ligne Contient Électronique")
        assert ok is True

    def test_verify_not_found(self, live_page):
        runner = _make_runner(live_page)
        ok = runner._fallback_step("Vérifier que la ligne Contient InexistantXYZ")
        assert ok is False
