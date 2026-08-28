"""Verifier: decides the real test verdict (sections 25-26).

Rule: a successful CLICK is NEVER a PASS. PASS requires observable proof
(success message, URL change, data appearing/disappearing, DOM change, HTTP).

Heuristics are always computed. OpenAI may refine only ambiguous cases.
"""
from ..config import env

SUCCESS_HINTS = ("succes", "success", "créé", "cree", "enregistré", "enregistre",
                 "ajouté", "ajoute", "modifié", "modifie", "bienvenue", "merci",
                 "confirmé", "confirme", "création réussie", "mis à jour", "mis a jour")
ERROR_HINTS = ("erreur", "error", "invalide", "invalid", "obligatoire", "required",
               "requis", "échoué", "echoue", "failed", "incorrect", "champ requis",
               "ne peut pas", "impossible de")


def snapshot(page):
    try:
        text = page.evaluate("document.body.innerText").strip().lower()
    except Exception:
        text = ""
    return {"url": page.url, "text": text}


def _has_any(text, hints):
    return any(h in text for h in hints)


def verdict(before, after, expected="", project_context="", data=""):
    """Pure heuristic verdict on one action. Returns dict with status/expected/obtained."""
    url_changed = after["url"] != before["url"]
    has_success = _has_any(after["text"], SUCCESS_HINTS)
    had_success = _has_any(before["text"], SUCCESS_HINTS)
    has_error = _has_any(after["text"], ERROR_HINTS)
    had_error = _has_any(before["text"], ERROR_HINTS)
    text_changed = after["text"] != before["text"]

    # DELETE proof: the created marker was present and is now gone.
    marker = (data or "").strip().lower()
    if marker and marker in before["text"] and marker not in after["text"] \
            and "supprim" in (expected or "").lower():
        return {"status": "PASS", "severity": "",
                "expected": expected or "La donnee creee disparait",
                "obtained": "La donnee creee (marqueur QA) a disparu de la page"}

    if has_error and not had_error:
        return {"status": "PASS", "severity": "",
                "expected": expected or "Une validation/erreur s'affiche pour les donnees invalides",
                "obtained": "Une erreur de validation est apparue"}
    if has_success and not had_success:
        return {"status": "PASS", "severity": "",
                "expected": expected or "Une confirmation de succes s'affiche",
                "obtained": "Un message de succes est apparu"}
    if url_changed:
        return {"status": "PASS", "severity": "",
                "expected": expected or "Une navigation est effectuee",
                "obtained": f"URL changee vers {after['url']}"}
    if text_changed:
        return {"status": "WARNING", "severity": "MINOR",
                "expected": expected or "Le contenu evolue de maniere attendue",
                "obtained": "Le contenu a change sans message explicite"}
    return {"status": "FAIL", "severity": env.DEFAULT_FAIL_SEVERITY,
            "expected": expected or "Une consequence visible de l'action est attendue",
            "obtained": "Aucune consequence visible apres l'action"}


def verdict_with_ai(before, after, expected, project_context, action_desc, help_evaluate, data=""):
    base = verdict(before, after, expected, project_context, data=data)
    # Only ask AI when the heuristics are ambiguous (WARNING/inconclusive)
    if base["status"] != "WARNING":
        return base
    rec = help_evaluate(action_desc, before, after, project_context) if help_evaluate else None
    if not rec:
        return base
    return {
        "status": rec.get("status", base["status"]).upper(),
        "severity": rec.get("severity", ""),
        "expected": rec.get("expected") or expected,
        "obtained": rec.get("obtained") or base["obtained"],
    }


def access_verdict(http_status):
    if http_status and http_status < 400:
        return {"status": "PASS", "severity": ""}
    if http_status and http_status >= 500:
        return {"status": "FAIL", "severity": "CRITICAL"}
    if http_status and http_status >= 400:
        return {"status": "FAIL", "severity": "MAJOR"}
    return {"status": "WARNING", "severity": ""}
