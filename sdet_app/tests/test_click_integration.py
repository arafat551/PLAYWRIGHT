"""Integration test: realistic page with DataTable, multiple buttons,
and the exact CSS class pattern the user has: btn btn-sm btn-primary float-right.

Tests that _collect_page_state finds all buttons AND that _click_by_text
clicks the RIGHT one (not just any button).
"""
import pytest
from playwright.sync_api import sync_playwright
from sdet_app.config import setting_bool, env

# ---------------------------------------------------------------------------
# Realistic page: sidebar nav + DataTable with action buttons per row
# ---------------------------------------------------------------------------
_PAGE_HTML = """\
<!DOCTYPE html>
<html lang="fr">
<head><meta charset="utf-8"><title>CRM - Clients</title>
<style>
  * { box-sizing: border-box; margin: 0; padding: 0; }
  body { font-family: sans-serif; display: flex; min-height: 100vh; }
  nav { width: 220px; background: #1a202c; color: white; padding: 20px; }
  nav a { display: block; color: #cbd5e0; padding: 8px 12px; margin: 2px 0;
          text-decoration: none; border-radius: 4px; }
  nav a:hover, nav a.active { background: #2d3748; color: white; }
  main { flex: 1; padding: 24px; }
  .page-header { display: flex; justify-content: space-between;
                 align-items: center; margin-bottom: 20px; }
  h1 { font-size: 22px; }
  /* The EXACT class pattern the user has */
  .btn { display: inline-block; padding: 8px 16px; cursor: pointer;
         color: white; border: none; border-radius: 4px; font-size: 14px;
         text-decoration: none; }
  .btn-sm { padding: 4px 10px; font-size: 12px; }
  .btn-primary { background-color: #ed8936; }
  .btn-success { background-color: #48bb78; }
  .btn-danger { background-color: #e53e3e; }
  .float-right { float: right; }
  table { width: 100%; border-collapse: collapse; }
  th, td { padding: 10px 12px; text-align: left; border-bottom: 1px solid #e2e8f0; }
  th { background: #f7fafc; font-weight: 600; }
  .actions-cell { text-align: right; white-space: nowrap; }
</style>
</head>
<body>

<!-- Sidebar nav (should NOT appear as clickable actions) -->
<nav>
  <a href="#" class="active">Clients</a>
  <a href="#">Factures</a>
  <a href="#">Paramètres</a>
</nav>

<main>
  <div class="page-header">
    <h1>Liste des clients</h1>
    <!-- THE BUTTON THE USER WANTS TO CLICK -->
    <div id="add-client-btn"
         class="btn btn-sm btn-primary float-right"
         onclick="document.getElementById('log').textContent += 'ADD_CLIENT;'">
      Ajouter un client
    </div>
  </div>

  <!-- DataTable with per-row action buttons -->
  <table>
    <thead>
      <tr>
        <th>Nom</th>
        <th>Email</th>
        <th>Actions</th>
      </tr>
    </thead>
    <tbody>
      <tr>
        <td>Dupont SA</td>
        <td>contact@dupont.fr</td>
        <td class="actions-cell">
          <div class="btn btn-sm btn-primary"
               onclick="document.getElementById('log').textContent += 'ROW1_VIEW;'">
            Voir
          </div>
          <div class="btn btn-sm btn-success"
               onclick="document.getElementById('log').textContent += 'ROW1_EDIT;'">
            Modifier
          </div>
          <div class="btn btn-sm btn-danger"
               onclick="document.getElementById('log').textContent += 'ROW1_DEL;'">
            Supprimer
          </div>
        </td>
      </tr>
      <tr>
        <td>Martin & Fils</td>
        <td>info@martin.com</td>
        <td class="actions-cell">
          <div class="btn btn-sm btn-primary"
               onclick="document.getElementById('log').textContent += 'ROW2_VIEW;'">
            Voir
          </div>
          <div class="btn btn-sm btn-success"
               onclick="document.getElementById('log').textContent += 'ROW2_EDIT;'">
            Modifier
          </div>
          <div class="btn btn-sm btn-danger"
               onclick="document.getElementById('log').textContent += 'ROW2_DEL;'">
            Supprimer
          </div>
        </td>
      </tr>
    </tbody>
  </table>
</main>

<div id="log" style="position:fixed; bottom:10px; left:240px; right:24px;
     padding:10px; background:#1a202c; color:#48bb78; font-family:monospace;"></div>

</body>
</html>
"""


@pytest.fixture(scope="module")
def page_url(tmp_path_factory):
    d = tmp_path_factory.mktemp("html")
    f = d / "realistic_page.html"
    f.write_text(_PAGE_HTML, encoding="utf-8")
    return f.as_uri()


@pytest.fixture(scope="module")
def browser():
    with sync_playwright() as p:
        br = p.chromium.launch(headless=setting_bool("headless", env.HEADLESS))
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


# ---------------------------------------------------------------------------
# Test 1: _collect_page_state finds EVERYTHING (nav excluded)
# ---------------------------------------------------------------------------

class TestCollectPageState:
    """_collect_page_state should find all actionable elements but NOT
    navigation links from the sidebar."""

    def test_finds_add_client_button(self, live_page):
        runner = _make_runner(live_page)
        state = runner._collect_page_state()
        labels = [s["label"] for s in state]
        assert "Ajouter un client" in labels

    def test_finds_row_buttons(self, live_page):
        runner = _make_runner(live_page)
        state = runner._collect_page_state()
        labels = [s["label"] for s in state]
        # Voir, Modifier, Supprimer appear in each row but deduped
        assert "Voir" in labels
        assert "Modifier" in labels
        assert "Supprimer" in labels

    def test_sidebar_nav_NOT_in_state(self, live_page):
        """Navigation links (sidebar) must NOT appear as actionable elements."""
        runner = _make_runner(live_page)
        state = runner._collect_page_state()
        labels = [s["label"].lower() for s in state]
        assert "clients" not in labels  # sidebar link
        assert "factures" not in labels
        assert "paramètres" not in labels

    def test_button_count(self, live_page):
        """Should find: Ajouter un client + Voir + Modifier + Supprimer
        = 4 unique labels (deduped by label+type)."""
        runner = _make_runner(live_page)
        state = runner._collect_page_state()
        labels = [s["label"] for s in state]
        # At least these 4 unique buttons
        for expected in ("Ajouter un client", "Voir", "Modifier", "Supprimer"):
            assert expected in labels, f"Missing: {expected} — found: {labels}"

    def test_add_client_button_is_type_button(self, live_page):
        runner = _make_runner(live_page)
        state = runner._collect_page_state()
        by_label = {s["label"]: s for s in state}
        add_btn = by_label.get("Ajouter un client")
        assert add_btn is not None
        assert add_btn["type"] == "button"

    def test_add_client_has_css(self, live_page):
        """The CSS class should be passed through for AI context."""
        runner = _make_runner(live_page)
        state = runner._collect_page_state()
        by_label = {s["label"]: s for s in state}
        add_btn = by_label.get("Ajouter un client")
        assert add_btn is not None
        css = add_btn.get("css", "")
        assert "btn" in css
        assert "primary" in css


# ---------------------------------------------------------------------------
# Test 2: _click_by_text clicks the RIGHT button
# ---------------------------------------------------------------------------

class TestClickByText:
    """_click_by_text should find and click the specific button matching
    the text, not just any button on the page."""

    def test_click_add_client(self, live_page):
        """Click 'Ajouter un client' — should fire ONLY the header button."""
        runner = _make_runner(live_page)
        _reset_log(live_page)
        ok = runner._click_by_text("Ajouter un client")
        assert ok is True
        log = _get_log(live_page)
        assert "ADD_CLIENT" in log
        # Must NOT have clicked row buttons
        assert "ROW1" not in log
        assert "ROW2" not in log

    def test_click_modifier(self, live_page):
        """Click 'Modifier' — should fire a row action."""
        runner = _make_runner(live_page)
        _reset_log(live_page)
        ok = runner._click_by_text("Modifier")
        assert ok is True
        log = _get_log(live_page)
        # Should click one of the Modifier row buttons
        assert "ROW1_EDIT" in log or "ROW2_EDIT" in log

    def test_click_supprimer(self, live_page):
        runner = _make_runner(live_page)
        _reset_log(live_page)
        ok = runner._click_by_text("Supprimer")
        assert ok is True
        log = _get_log(live_page)
        assert "ROW1_DEL" in log or "ROW2_DEL" in log

    def test_click_voir(self, live_page):
        runner = _make_runner(live_page)
        _reset_log(live_page)
        ok = runner._click_by_text("Voir")
        assert ok is True
        log = _get_log(live_page)
        assert "ROW1_VIEW" in log or "ROW2_VIEW" in log

    def test_nonexistent_returns_false(self, live_page):
        runner = _make_runner(live_page)
        ok = runner._click_by_text("Ce bouton nexiste pas")
        assert ok is False


# ---------------------------------------------------------------------------
# Test 3: Full AI planning flow — collect → plan → execute
# ---------------------------------------------------------------------------

class TestAIFlow:
    """Simulate what ai_step does: collect page state, build element list,
    verify the right element is findable by description text."""

    def test_collect_then_find_add_button(self, live_page):
        """Simulate: description says 'Cliquer sur Ajouter un client'.
        The collect_page_state should return an element the AI can match."""
        runner = _make_runner(live_page)
        state = runner._collect_page_state()
        # Build the same format the AI receives
        ai_state = [{"label": s["label"], "type": s["type"],
                     "css": s.get("css", "")} for s in state]

        # Find the element the AI would pick for "Cliquer sur Ajouter un client"
        matched = None
        for i, el in enumerate(ai_state):
            if "ajouter" in el["label"].lower():
                matched = el
                break

        assert matched is not None, f"No element matched 'Ajouter' in: {[e['label'] for e in ai_state]}"
        assert matched["type"] == "button"
        assert "btn" in matched.get("css", "")

    def test_collect_then_find_modifier_in_table(self, live_page):
        """Description says 'Cliquer sur Modifier'. The table row buttons
        should be findable."""
        runner = _make_runner(live_page)
        state = runner._collect_page_state()
        ai_state = [{"label": s["label"], "type": s["type"],
                     "css": s.get("css", "")} for s in state]

        matched = [e for e in ai_state if "modifier" in e["label"].lower()]
        assert len(matched) >= 1, "Modifier button not found in page state"
        assert matched[0]["type"] == "button"
