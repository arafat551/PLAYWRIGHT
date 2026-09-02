"""OpenAI assistance layer.

OpenAI is NOT the browser driver. It is only consulted when the classical
SDET rules cannot decide (ambiguity in action meaning or in a verification).

Every call must degrade gracefully to the rule-based engine when the API key
is missing or the endpoint is unavailable.
"""
import json

from ..config import env, get_setting

_openai_client = None
_openai_failed = False


def _client():
    global _openai_client, _openai_failed
    if _openai_failed or not env.OPENAI_API_KEY:
        return None
    if _openai_client is None:
        try:
            from openai import OpenAI
            _openai_client = OpenAI(api_key=env.OPENAI_API_KEY)
        except Exception:
            _openai_failed = True
            return None
    return _openai_client


def _extract_json(raw):
    raw = (raw or "").strip()
    if raw.startswith("```"):
        raw = raw.split("\n", 1)[1].rsplit("```", 1)[0].strip()
    try:
        return json.loads(raw)
    except Exception:
        start, end = raw.find("{"), raw.rfind("}")
        if start != -1 and end > start:
            try:
                return json.loads(raw[start:end + 1])
            except Exception:
                return None
        return None


def classifies_action(label, dom_hint=""):
    """Ask OpenAI to classify one ambiguous action. Returns a dict or None."""
    client = _client()
    if not client:
        return None
    try:
        resp = client.chat.completions.create(
            model=get_setting("ai_model", env.OPENAI_MODEL),
            messages=[
                {"role": "system", "content":
                 "You classify a web action for a QA SDET engine. "
                 "Reply with JSON: {\"action_type\":\"CREATE|READ|UPDATE|DELETE|SEARCH|STATUS|NAVIGATION|AUTH|FORM|GENERIC\","
                 " \"sensitive\": true/false, \"confidence\": 0.0-1.0}. "
                 "Do not allow DELETE/CREATE on pre-existing references."},
                {"role": "user", "content": f"Label: {label}\nDOM hint: {dom_hint}"},
            ],
            max_tokens=120,
            temperature=0,
        )
        data = _extract_json(resp.choices[0].message.content)
        if data and data.get("action_type"):
            return data
    except Exception:
        pass
    return None


def plan_step_action(step_desc, page_state, project_context=""):
    """Ask OpenAI to plan the next browser action for a natural-language step.

    `step_desc` is the human description of what to do (e.g. "Cliquer sur
    Ajouter"), `page_state` lists the actionable elements visible on the
    current page. Returns a JSON action dict, or None if unavailable.

    Action schema:
      {"action": "CLICK|FILL|SELECT|NAVIGATE|WAIT|SCROLL|VERIFY",
       "selector": "<css selector or element index>",
       "text": "<value to type or option to pick>",
       "done": true/false,
       "comment": "<short reason>"}
    """
    client = _client()
    if not client:
        return None
    try:
        system = (
            "You drive a QA browser. Given a step to perform and the list of "
            "actionable elements currently on the page, return the SINGLE "
            "browser action that best performs the step. Reply with valid JSON "
            "only, of the form: "
            '{"action":"CLICK|FILL|SELECT|NAVIGATE|WAIT|SCROLL|VERIFY",'
            '"selector":"<number of the element from the list, or a css selector>",'
            '"text":"<value to type if FILL/SELECT/NAVIGATE>",'
            '"done":true|false,'
            '"comment":"<short reason>"}. '
            'Set "done":true when the step is fully accomplished (nothing more '
            "to click), else false. The selector must be an integer index "
            "(0-based) into the provided element list whenever possible. "
            "Match the step description to the element whose label, type, or CSS "
            "class best corresponds (e.g. 'Ajouter' matches a button/link with "
            "label 'Ajouter' or 'Ajouter un client', or a '+' icon button, or "
            "an element with CSS class containing 'btn' and label 'Ajouter'). "
            "If no exact label match, prefer the closest semantic match."
        )
        user = (
            f"Projet/URL: {project_context}\n"
            f"Étape à réaliser: {step_desc}\n\n"
            f"Éléments actionnables présents sur la page (index: libellé [type [css]]):\n"
            + _fmt_elements(page_state)
        )
        resp = client.chat.completions.create(
            model=get_setting("ai_model", env.OPENAI_MODEL),
            messages=[
                {"role": "system", "content": system},
                {"role": "user", "content": user},
            ],
            max_tokens=400,
            temperature=0,
        )
        data = _extract_json(resp.choices[0].message.content)
        if data and data.get("action"):
            data["action"] = data["action"].upper()
            return data
    except Exception:
        pass
    return None


def _fmt_elements(page_state):
    if not page_state:
        return "(aucun élément)"[:400]
    lines = []
    for i, el in enumerate(page_state):
        label = (el.get("label") or "").strip()[:60]
        etype = (el.get("type") or "").strip()
        css = (el.get("css") or "").strip()[:40]
        hint = f" [{css}]" if css else ""
        lines.append(f"{i}: {label} [{etype}]{hint}")
    text = "\n".join(lines)
    return text[:3000]


def help_evaluate(action_desc, before, after, project_context=""):
    """Ask OpenAI to evaluate an ambiguous outcome. Returns dict or None."""
    client = _client()
    if not client:
        return None
    try:
        msg = f"""Projet: {project_context}
Action: {action_desc}
URL avant: {before.get('url','')}
Texte avant (extrait): {before.get('text','')[:600]}
URL apres: {after.get('url','')}
Texte apres (extrait): {after.get('text','')[:900]}

Evalue le resultat. Reponds JSON: {{
  "status": "PASS|FAIL|WARNING|SKIPPED",
  "expected": "...", "obtained": "...",
  "severity": "CRITICAL|MAJOR|MINOR"
}}"""
        resp = client.chat.completions.create(
            model=get_setting("ai_model", env.OPENAI_MODEL),
            messages=[
                {"role": "system", "content":
                 "You are a senior SDET. NEVER mark a successful click as PASS. "
                 "A test passes only if the observable result matches the expectation. "
                 "Reply with valid JSON only."},
                {"role": "user", "content": msg},
            ],
            max_tokens=500,
            temperature=0.2,
        )
        data = _extract_json(resp.choices[0].message.content)
        if data and data.get("status"):
            data["status"] = data["status"].upper()
            return data
    except Exception:
        pass
    return None
