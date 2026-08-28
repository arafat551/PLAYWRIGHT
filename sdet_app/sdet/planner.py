"""Planner: turns discovered elements into coherent SDET scenarios (section 19).

Instead of testing buttons one-by-one, a CRUD page is planned as a full
workflow:
   CREATE -> VERIFY -> FIND -> READ -> UPDATE -> VERIFY -> OTHER ACTIONS
   -> DELETE LAST -> VERIFY DELETION

Rules it enforces:
  - Actions submitted by the user run in full (create, read, update, delete).
  - Sensitive actions (payments, real notifications, ...) are unchecked by
    default in the UI but remain selectable.
  - The engine only ever deletes data it created itself.
"""
from .classification import (action_type, is_sensitive, normalize,
                             CREATE_HINTS, DELETE_HINTS, UPDATE_HINTS)


def build_scenarios(pages, actions, selected, environment="STAGING",
                    help_ai=None):
    """Build an ordered list of scenario steps from the selected actions.

    Returns a list of step dicts consumed by the runner:
      {"step_type", "module", "label", "action", ...}
    """
    # index selected actions by page url
    by_page = {}
    for a in selected:
        purl = a.get("page_url")
        by_page.setdefault(purl, []).append(a)

    page_by_url = {p.url: p for p in pages}
    scenarios = []
    for purl, page_actions in by_page.items():
        page = page_by_url.get(purl)
        module = page.name if page else (page_actions[0].get("page_name") or "Général")
        scenarios.extend(_plan_page(module, purl, page_actions, environment, help_ai))
    return scenarios


def _plan_page(module, purl, actions, environment, help_ai):
    steps = []

    # Always start by verifying the page is reachable
    steps.append({
        "step_type": "access",
        "module": module,
        "url": purl,
        "label": "Accès page",
        "action_type": "NAVIGATION",
    })

    # Classify each selected action
    classified = []
    for a in actions:
        label = a.get("label", "")
        a_type = action_type(label)
        # Honor the explorer's fine-grained typing (form/table descriptors)
        raw_type = (a.get("action_type") or "").lower()
        if raw_type in ("form", "table"):
            a_type = "FORM"
        sensitive = is_sensitive(label)

        # OpenAI help only if still generic/ambiguous
        if a_type == "GENERIC" and help_ai:
            rec = help_ai(label)
            if rec and rec.get("action_type"):
                a_type = rec["action_type"]
                if rec.get("sensitive"):
                    sensitive = True

        a["_type"] = a_type
        a["_sensitive"] = sensitive
        classified.append(a)

    # Split by category
    creates = [a for a in classified if a["_type"] == "CREATE" and not a.get("_disabled")]
    deletes = [a for a in classified if a["_type"] == "DELETE" and not a.get("_disabled")]
    reads = [a for a in classified if a["_type"] == "READ"]
    updates = [a for a in classified if a["_type"] == "UPDATE"]
    # FORM actions are descriptors of a form's fields, not clickable elements
    # (they are handled implicitly by create/update). STATUS/NAVIGATION/GENERIC
    # are the clickable "other" actions.
    others = [a for a in classified if a["_type"] not in
              ("CREATE", "DELETE", "READ", "UPDATE", "FORM")]

    # 1) CREATE workflows (full creation + verify + find).
    #    Every submitted action was explicitly selected by the user, so the
    #    full workflow runs; the engine only ever deletes data it created.
    entity_name = None
    for c in creates:
        c["_enabled"] = True
        steps.append({
            "step_type": "create",
            "module": module,
            "url": purl,
            "label": c["label"],
            "action": "CREATE",
            "source_action": c,
        })

    # 2) Search + finds after creation (one search step per page, prefer the
    #    search input over a plain 'Rechercher' button)
    search_acts = [a for a in others if a["_type"] == "SEARCH"]
    search_acts.sort(key=lambda a: 0 if a.get("action_type") == "search" else 1)
    for s in search_acts[:1]:
        s["_enabled"] = True
        steps.append({
            "step_type": "search_create",
            "module": module,
            "url": purl,
            "label": s["label"],
            "action": "SEARCH",
            "source_action": s,
        })

    # 3) READ / UPDATE workflows (on the created row if any, else generic)
    for r in reads:
        r["_enabled"] = True
        steps.append({
            "step_type": "read",
            "module": module,
            "url": purl,
            "label": r["label"],
            "action": "READ",
            "source_action": r,
        })
    for u in updates:
        u["_enabled"] = True
        steps.append({
            "step_type": "update",
            "module": module,
            "url": purl,
            "label": u["label"],
            "action": "UPDATE",
            "source_action": u,
        })

    # 4) STATUS / other actions (non destructive)
    #    Pure form-submit verbs are covered by create/update flows and are NOT
    #    clicked standalone (they would submit an empty form).
    _submit_verbs = ("enregistrer", "sauvegarder", "valider", "submit",
                     "se connecter", "connexion", "ajouter", "creer",
                     "créer", "enregistrer les modifications")
    for o in others:
        if o["_type"] in ("STATUS", "NAVIGATION", "GENERIC"):
            olabel = (o.get("label") or "").lower()
            if o["_type"] == "GENERIC" and any(v in olabel for v in _submit_verbs):
                continue
            o["_enabled"] = True
            steps.append({
                "step_type": "generic",
                "module": module,
                "url": purl,
                "label": o["label"],
                "action": o["_type"],
                "source_action": o,
            })

    # 5) DELETE LAST (only safe deletes, enforced at runtime by marker)
    for d in deletes:
        d["_enabled"] = True
        steps.append({
            "step_type": "delete_last",
            "module": module,
            "url": purl,
            "label": d["label"],
            "action": "DELETE",
            "source_action": d,
        })

    return steps


def applicable_atomic_actions(actions, environment):
    """Return which atomic actions are available for UI checkboxes."""
    out = []
    for a in actions:
        a_type = action_type(a.get("label", ""))
        sensitive = is_sensitive(a.get("label", ""))
        out.append({
            "id": a.get("id"),
            "label": a.get("label"),
            "action_type": a_type,
            "page_url": a.get("page_url"),
            "page_name": a.get("page_name"),
            "sensitive": sensitive,
            "el_index": a.get("el_index"),
            "action_key": a.get("action_key", ""),
            "default_checked": not sensitive,
        })
    return out
