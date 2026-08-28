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
