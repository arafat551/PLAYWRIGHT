"""Forms: analyse a form's fields and generate relevant QA data (section 23).

Every field is inspected for: label, type, required, placeholder, select,
radio, checkbox, textarea. Then realistic unique data is generated using the
SDET_ marker so the engine can recognise its own created data.
"""
import random
import string

from ..config import env, setting_int


def _tag(el):
    """Lowercase tag of an ElementHandle (ElementHandle has no .tag_name())."""
    try:
        return (el.evaluate("n => n.tagName") or "").lower()
    except Exception:
        return ""


def find_form(page):
    """Return the visible form to act on (dialog/modal form priority).

    Waits for the form to be present up to PAGE_TIMEOUT (Paramètres) so transient
    post-navigation states don't yield a false negative.
    """
    selector = ('[role="dialog"] form, .modal form, form, '
                '[role="dialog"], .modal')
    try:
        page.wait_for_selector(selector, state="attached",
                               timeout=setting_int("page_timeout", env.PAGE_TIMEOUT))
    except Exception:
        pass
    try:
        candidates = page.query_selector_all(selector)
    except Exception:
        candidates = []
    for el in candidates:
        try:
            if el.is_visible():
                tag = _tag(el)
                form = el if tag == "form" else el.query_selector("form")
                if form:
                    return form
        except Exception:
            continue
    # fallback: wait a little more and retry once
    try:
        page.wait_for_timeout(800)
        candidates = page.query_selector_all(selector)
        for el in candidates:
            if el.is_visible():
                tag = _tag(el)
                form = el if tag == "form" else el.query_selector("form")
                if form:
                    return form
    except Exception:
        pass
    return None


def unique_suffix():
    return str(random.randint(1000, 9999))


def build_entity_name(prefix="QA", label=""):
    """e.g. QA_CLIENT_4821 or QA_Kossi_4821"""
    clean = (label or "").strip().split()
    word = clean[0] if clean else "Item"
    return f"{prefix}_{word.upper()}_{unique_suffix()}"


def _label_for_field(field):
    try:
        label = (field.get_attribute("aria-label") or "").strip()
        if label:
            return label
    except Exception:
        pass
    try:
        name = (field.get_attribute("name") or "").strip()
        if name:
            return name
    except Exception:
        pass
    try:
        ph = (field.get_attribute("placeholder") or "").strip()
        if ph:
            return ph
    except Exception:
        pass
    return ""


def _field_type(field):
    try:
        return (field.get_attribute("type") or "text").lower()
    except Exception:
        return "text"


def _required(field):
    try:
        return field.get_attribute("required") is not None
    except Exception:
        return False


def analyse_form(form_el):
    """Describe every field of the form in a structured way."""
    fields = []
    try:
        raw = form_el.query_selector_all("input, select, textarea")
    except Exception:
        raw = []
    for inp in raw:
        tag = _tag(inp)
        itype = _field_type(inp)
        # skip hidden/internal fields
        if itype in ("hidden", "submit", "button", "file", "reset"):
            continue
        choices = []
        if tag == "select":
            try:
                choices = [o["value"] if isinstance(o, dict) else (o.get_attribute("value") or "")
                           for o in inp.query_selector_all("option")]
            except Exception:
                choices = []
        fields.append({
            "tag": tag,
            "type": itype,
            "name": _label_for_field(inp),
            "required": _required(inp),
            "choices": choices,
        })
    return fields


def generate_data(field, suffix=""):
    """Generate QA data for a single described field."""
    name = normalize(field.get("name", ""))
    ftype = field.get("type", "text")
    tag = field.get("tag", "input")

    if tag == "select":
        return None  # caller picks from choices
    if ftype == "checkbox":
        return True
    if ftype == "radio":
        return True
    if ftype == "email" or "email" in name or "mail" in name:
        return f"qa{suffix}@example.test"
    if ftype == "password":
        return "QaPass123!"
    if ftype == "tel" or "tel" in name or "phone" in name or "telephone" in name:
        return f"90{suffix[:1]}00000"
    if ftype == "url":
        return "https://example.com"
    if ftype == "number" or "quantite" in name or "quantity" in name:
        return "5"
    if ftype == "date" or "date" in name:
        return "2026-01-15"
    if "email" in name:
        return f"qa{suffix}@example.test"
    if "nom" in name or "name" in name or "client" in name or "titre" in name or "title" in name:
        return f"QA Kossi {suffix}"
    if "description" in name or "comment" in name or "note" in name or "message" in name:
        return "Donnee automatique QA"
    if "prix" in name or "price" in name:
        return "99.99"
    return "Test QA automatique"


def normalize(s):
    return (s or "").lower().replace("é", "e").replace("è", "e").replace("à", "a") \
        .replace("ô", "o").replace("î", "i").replace("û", "u").replace("ç", "c")


def fill_form(page, form_el, suffix="", entity_name=""):
    """Fill the form with QA data. Returns number of fields filled + entity name."""
    filled = 0
    entity_name = entity_name or build_entity_name("QA")
    try:
        fields = form_el.query_selector_all("input, select, textarea")
    except Exception:
        return 0, entity_name
    for inp in fields:
        tag = _tag(inp)
        itype = _field_type(inp)
        if itype in ("hidden", "submit", "button", "file", "reset"):
            continue
        field_desc = {
            "tag": tag,
            "type": itype,
            "name": _label_for_field(inp),
        }
        try:
            if tag == "select":
                choices = [o.get_attribute("value") for o in inp.query_selector_all("option")]
                choices = [c for c in choices if c not in ("", None)]
                if len(choices) > 0:
                    inp.select_option(value=choices[0])
                    filled += 1
                continue
            if itype in ("checkbox", "radio"):
                try:
                    if not inp.is_checked():
                        inp.check()
                        filled += 1
                except Exception:
                    pass
                continue
            val = generate_data(field_desc, suffix)
            if itype == "textarea" or tag == "textarea":
                inp.fill("Donnee automatique QA")
            elif itype == "email" or "email" in field_desc["name"]:
                inp.fill(f"qa{suffix}@example.test")
            elif "nom" in field_desc["name"] or "name" in field_desc["name"] \
                    or "client" in field_desc["name"] or "titre" in field_desc["name"]:
                inp.fill(entity_name)
            else:
                inp.fill(str(val))
            filled += 1
        except Exception:
            continue
    return filled, entity_name


def toggle_field(page, form_el, suffix="", entity_name=""):
    """Change one visible field so an edit becomes provable. Returns changed count.

    The primary name field keeps the entity marker so the row remains
    recognisable for later find/delete verification.
    """
    try:
        fields = form_el.query_selector_all("input, select, textarea")
    except Exception:
        fields = []
    for inp in fields:
        itype = _field_type(inp)
        if itype in ("hidden", "submit", "button", "file", "reset"):
            continue
        name = _label_for_field(inp)
        tag = _tag(inp)
        try:
            if itype in ("checkbox", "radio"):
                try:
                    if inp.is_checked():
                        inp.uncheck()
                    else:
                        inp.check()
                    if inp.is_visible():
                        return 1
                except Exception:
                    pass
                continue
            if tag == "select":
                choices = [o.get_attribute("value") for o in inp.query_selector_all("option")]
                choices = [c for c in choices if c not in ("", None)]
                if len(choices) > 1:
                    inp.select_option(value=choices[-1])
                    return 1
                continue
            curr = (inp.input_value() or "").strip()
            if "nom" in name or "name" in name:
                base = entity_name or "QA_MOD"
                new_val = f"{base} modifie"
            else:
                new_val = (str(curr) + "M" if curr else "Donnee modifiee QA")
            inp.fill(new_val)
            return 1
        except Exception:
            continue
    return 0


def submit_form(page, form_el=None):
    try:
        if form_el:
            sub = form_el.query_selector(
                'button[type="submit"], input[type="submit"], button:not([type])')
            if sub:
                sub.click()
                return
    except Exception:
        pass
    page.keyboard.press("Enter")
    try:
        page.wait_for_load_state("domcontentloaded", timeout=15000)
    except Exception:
        pass
    page.wait_for_timeout(1500)
