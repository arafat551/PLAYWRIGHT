"""Envoi du rapport d'exécution par e-mail.

Après la fin d'un scénario (exécution automatique ou manuelle), ce module
construit un récapitulatif textuel des statistiques (total, PASS, FAIL,
WARNING, SKIPPED), convertit le rapport HTML en PDF et l'envoie en pièce
jointe aux destinataires configurés dans Paramètres (SMTP / rapport e-mail).
"""
import os
import re
import secrets
import traceback
from datetime import datetime

from .. import database, mailer
from ..config import env, get_setting, setting_bool
from . import reporter


def render_report_pdf(html):
    """Convert a report HTML document into A4 PDF bytes using headless
    Chromium (via Playwright). The HTML file is written inside REPORT_DIR so
    the relative '../static/...' screenshot links still resolve."""
    os.makedirs(env.REPORT_DIR, exist_ok=True)
    base = os.path.join(env.REPORT_DIR, f"_mail_{secrets.token_hex(8)}")
    tmp_html = base + ".html"
    pdf_path = base + ".pdf"
    try:
        with open(tmp_html, "w", encoding="utf-8") as f:
            f.write(html)
        from pathlib import Path
        from playwright.sync_api import sync_playwright
        with sync_playwright() as p:
            browser = p.chromium.launch()
            page = browser.new_page()

            # Le PDF doit être généré sans aucun dépendance réseau : les
            # polices Google et autres ressources externes du rapport peuvent
            # bloquer "load" (serveur isolé, pare-feu, proxy...) et faire
            # échouer la conversion → envoi sans pièce jointe.
            def _block_external(route):
                url = route.request.url
                if url.startswith(("http://", "https://")):
                    route.abort()
                else:
                    route.continue_()

            page.route("**/*", _block_external)

            try:
                page.goto(Path(tmp_html).as_uri(), wait_until="load", timeout=30000)
            except Exception:
                page.goto(Path(tmp_html).as_uri(),
                          wait_until="domcontentloaded", timeout=30000)
                page.wait_for_timeout(300)
            page.pdf(path=pdf_path, format="A4", print_background=True,
                     margin={"top": "0", "bottom": "0", "left": "0", "right": "0"})
            browser.close()
        with open(pdf_path, "rb") as f:
            return f.read()
    finally:
        for fp in (tmp_html, pdf_path):
            try:
                if os.path.exists(fp):
                    os.remove(fp)
            except OSError:
                pass


def build_email_content(project, test_run, report):
    """Build (subject, body_text, body_html) for the run report e-mail.

    Text-only message (no images, tables allowed) describing the statistics
    of the execution. The full report goes as a PDF attachment.
    """
    total = report["total"]
    passed = report["passed"]
    failed = report["failed"]
    warning = report["warning"]
    skipped = report["skipped"]
    pct = report["success_pct"]

    if str(report.get("status", "")).lower() in ("cancelled", "canceled"):
        verdict = "ANNULÉ"
    elif failed:
        verdict = "ÉCHEC"
    elif warning:
        verdict = "ATTENTION"
    elif total:
        verdict = "RÉUSSITE"
    else:
        verdict = "AUCUN TEST EXÉCUTÉ"

    header = [
        ("Projet", report["project"]),
        ("Environnement", report["environment"]),
        ("Type", report["run_type"]),
        ("Date", str(report["date"])),
        ("Lancé par", report["launched_by"]),
        ("Durée", report["duration"]),
    ]
    project_url = (report.get("project_url") or project.get("url") or "").strip()
    if project_url:
        header.insert(1, ("URL", project_url))

    # --- Version texte ------------------------------------------------------
    lines = [
        "Rapport d'exécution AUTOMATION",
        "",
        *[f"{label:<14}: {value}" for label, value in header],
        "",
        "Résultats des tests :",
        f"    Total   : {total}",
        f"    PASS    : {passed}",
        f"    FAIL    : {failed}",
        f"    WARNING : {warning}",
        f"    SKIPPED : {skipped}",
        "",
        f"Taux de réussite : {pct}%  ({verdict})",
        "",
    ]

    if failed:
        lines.append("Échecs détectés :")
        for i, f in enumerate(report["failures"], 1):
            module = f.get("module", "") or ""
            func = f.get("function", "") or f.get("function_name", "") or ""
            obt = f.get("obtained", "") or ""
            lines.append(f"    {i}. [{module}] {func} — {obt}")
        lines.append("")

    mods = report.get("module_stats") or []
    if mods:
        lines.append("Détail par module :")
        for m in mods:
            lines.append(
                f"    {m['name']}: total={m['total']} PASS={m['pass']} "
                f"FAIL={m['fail']} WARNING={m['warning']} SKIPPED={m['skipped']}")
        lines.append("")

    lines.append("Le rapport complet est joint à cet e-mail (PDF).")
    body_text = "\n".join(lines)

    # --- Version HTML -------------------------------------------------------
    verdict_label = {
        "ÉCHEC": ("ÉCHEC", "#b91c1c", "#fef2f2", "#fecaca"),
        "ATTENTION": ("ATTENTION", "#b45309", "#fffbeb", "#fde68a"),
        "RÉUSSITE": ("RÉUSSITE", "#15803d", "#f0fdf4", "#bbf7d0"),
        "AUCUN TEST EXÉCUTÉ": ("AUCUN TEST", "#64748b", "#f8fafc", "#e2e8f0"),
        "ANNULÉ": ("ANNULÉ", "#475569", "#f8fafc", "#e2e8f0"),
    }[verdict]
    v_text, v_color, v_bg, v_border = verdict_label
    v_icon = {"ÉCHEC": "&#10007;", "ATTENTION": "&#9888;",
              "RÉUSSITE": "&#10004;", "AUCUN TEST EXÉCUTÉ": "&#8212;",
              "ANNULÉ": "&#8212;"}[verdict]

    if failed:
        intro = (f"Voici les résultats des tests : <b>{passed} test(s) réussi(s)</b> "
                 f"et <b>{failed} échec(s)</b>")
    elif total:
        intro = (f"Voici les résultats des tests : <b>{passed} test(s) réussi(s)</b>, "
                 f"aucun échec")
    else:
        intro = "Aucun test n'a été exécuté lors de cette campagne."
    if warning:
        intro += f", dont <b>{warning} avertissement(s)</b>"
    if skipped:
        intro += f" et <b>{skipped} test(s) ignoré(s)</b>"
    intro += "."

    def _stat_card(value, label, color, bg, border):
        return (
            f'<div style="border:1px solid {border};border-radius:12px;background:{bg};'
            f'padding:10px 6px;text-align:center;">'
            f'<div style="font-size:22px;line-height:1.1;font-weight:800;color:{color};">'
            f'{value}</div>'
            f'<div style="font-size:8px;line-height:1.2;font-weight:800;letter-spacing:.7px;'
            f'color:{color};margin-top:3px;">{label}</div>'
            f'</div>')

    def _section(title, color):
        return (
            f'<div style="font-size:10px;font-weight:800;letter-spacing:1.1px;color:{color};'
            f'margin:14px 0 6px;text-transform:uppercase;">{title}</div>')

    def _meta_value(value, is_url=False):
        if is_url:
            return (f'<a href="{value}" style="color:#4f46e5;font-weight:600;'
                    f'word-break:break-word;">{value}</a>')
        return value

    # 1) Héro : pourcentage + verdict
    fill_width = max(min(int(pct), 100), 0)
    hero = (
        f'<div style="background:{v_bg};border:1px solid {v_border};border-radius:14px;'
        f'padding:14px 16px;text-align:center;margin:0 0 10px;">'
        f'<div style="font-size:9px;font-weight:800;letter-spacing:1.3px;color:{v_color};">'
        f'TAUX DE RÉUSSITE</div>'
        f'<div style="font-size:36px;line-height:1.1;font-weight:800;color:{v_color};'
        f'margin:2px 0 6px;">{pct}&thinsp;%</div>'
        f'<span style="display:inline-block;background:#ffffff;border:1px solid {v_border};'
        f'color:{v_color};border-radius:999px;padding:4px 14px;font-size:10px;font-weight:800;'
        f'letter-spacing:.7px;">{v_icon}&nbsp;{v_text}</span>'
        f'</div>'
        f'<table role="presentation" width="100%" cellpadding="0" cellspacing="0" '
        f'style="border-collapse:collapse;margin:0 0 12px;"><tr><td>'
        f'<table role="presentation" width="100%" cellpadding="0" cellspacing="0" '
        f'style="background:#eef2ff;border-radius:8px;border-collapse:collapse;">'
        f'<tr><td width="{fill_width}" style="background-color:{v_color};'
        f'background-image:linear-gradient(90deg,{v_color},#7c3aed);border-radius:8px;'
        f'height:8px;font-size:1px;line-height:8px;">&nbsp;</td>'
        f'<td width="{max(100 - fill_width, 1)}" style="height:8px;font-size:1px;">&nbsp;</td>'
        f'</tr></table></td></tr></table>')

    # 2) Cartes statistiques (2 × 2)
    cards = (
        f'<table role="presentation" width="100%" cellpadding="0" cellspacing="0" '
        f'style="border-collapse:collapse;margin:0 0 10px;">'
        f'<tr>'
        f'<td width="50%" style="padding:2px;vertical-align:top;">'
        f'{_stat_card(passed, "RÉUSSIS", "#15803d", "#f0fdf4", "#bbf7d0")}</td>'
        f'<td width="50%" style="padding:2px;vertical-align:top;">'
        f'{_stat_card(failed, "ÉCHECS", "#b91c1c", "#fef2f2", "#fecaca")}</td>'
        f'</tr>'
        f'<tr>'
        f'<td width="50%" style="padding:2px;vertical-align:top;">'
        f'{_stat_card(warning, "AVERTISSEMENTS", "#b45309", "#fffbeb", "#fde68a")}</td>'
        f'<td width="50%" style="padding:2px;vertical-align:top;">'
        f'{_stat_card(skipped, "IGNORÉS", "#64748b", "#f8fafc", "#e2e8f0")}</td>'
        f'</tr>'
        f'</table>')

    # 3) Informations du scénario
    meta_rows = ""
    for label, value in header:
        is_url = label == "URL"
        meta_rows += (
            f'<tr>'
            f'<td style="padding:7px 12px;border-bottom:1px solid #f1f5f9;font-size:9px;'
            f'font-weight:800;letter-spacing:.6px;color:#64748b;width:38%;'
            f'text-transform:uppercase;">{label}</td>'
            f'<td style="padding:7px 12px;border-bottom:1px solid #f1f5f9;font-size:12px;'
            f'color:#0f172a;font-weight:600;word-break:break-word;">'
            f'{_meta_value(value, is_url)}</td>'
            f'</tr>')
    meta_block = (
        f'<table role="presentation" width="100%" cellpadding="0" cellspacing="0" '
        f'style="border-collapse:collapse;border:1px solid #e2e8f0;border-radius:12px;'
        f'overflow:hidden;margin:0 0 6px;">{meta_rows}</table>')

    # 4) Échecs détectés
    failure_block = ""
    if failed:
        failure_rows = "".join(
            f'<table role="presentation" width="100%" cellpadding="0" cellspacing="0" '
            f'style="border-collapse:collapse;margin:3px 0;"><tr>'
            f'<td style="background:#fef2f2;border:1px solid #fecaca;border-radius:8px;'
            f'padding:8px 10px;font-size:11px;line-height:1.4;color:#7f1d1d;">'
            f'<b style="color:#991b1b;">[{f.get("module", "") or "Module"}] '
            f'{f.get("function", "") or f.get("function_name", "")}</b>'
            f' <span style="color:#b91c1c;">— {f.get("obtained", "") or "voir le rapport"}</span>'
            f'</td></tr></table>'
            for f in (report.get("failures") or []))
        failure_block = (
            f'{_section("Échecs détectés", "#b91c1c")}'
            f'{failure_rows}')

    content = (
        f'<p style="margin:0 0 6px;font-size:14px;line-height:1.55">{intro}</p>'
        f'{hero}'
        f'{cards}'
        f'{meta_block}'
        f'{failure_block}'
        f'<p style="font-size:11px;color:#94a3b8;margin:12px 0 0;border-top:1px solid #f1f5f9;'
        f'padding-top:10px;text-align:center;">Le rapport complet est joint à cet e-mail (PDF).</p>')

    body_html = mailer.render_html(
        "Rapport d'exécution AUTOMATION", content,
        preheader=f"{report['project']} · {pct}% de réussite"
                  f"{f' · {failed} échec(s)' if failed else ' · aucun échec'}")

    subject_template = get_setting(
        "report_email_subject", "Rapport AUTOMATION — {projet} ({taux}%)") or \
        "Rapport AUTOMATION — {projet} ({taux}%)"
    try:
        subject = subject_template.format(projet=report["project"] or "projet",
                                          taux=pct)
    except (KeyError, ValueError):
        subject = subject_template
    return subject, body_text, body_html


def send_run_report(tid):
    """Envoyer le rapport d'un scénario terminé aux destinataires.

    Destinataires prioritaires : ceux configurés sur le projet lui-même
    (« Destinataires du rapport » à la création / modification du projet).
    À défaut, retombe sur le destinataire global des Paramètres.

    No-op si l'envoi est désactivé ou si aucun destinataire n'est renseigné.
    Returns (sent, recipients)."""
    enabled = setting_bool("report_email_enabled", False)
    if not enabled:
        return 0, []

    test = database.get_test_run(tid)
    if not test:
        return 0, []

    project_recipients_raw = (getattr(test, "report_recipients", "") or "").strip()
    recipients = [r.strip() for r in re.split(r"[,;]", project_recipients_raw)
                  if r.strip()]
    if not recipients:
        recipients = [r.strip()
                      for r in re.split(r"[,;]", get_setting("report_email_recipient", "") or "")
                      if r.strip()]
    if not recipients:
        return 0, []

    results = [dict(r) if isinstance(r, dict) else r.__dict__
               for r in database.list_results(tid)]
    counters = reporter.counters_from_results(results)
    duration = 0
    try:
        start = datetime.strptime(str(getattr(test, "started_at", ""))[:19],
                                  "%Y-%m-%d %H:%M:%S")
        if getattr(test, "finished_at", None):
            end = datetime.strptime(str(test.finished_at)[:19], "%Y-%m-%d %H:%M:%S")
            duration = max(int((end - start).total_seconds()), 0)
    except ValueError:
        duration = 0

    run_info = {"started_at": getattr(test, "started_at", ""),
                "launched_by": getattr(test, "launched_by", ""),
                "run_type": getattr(test, "run_type", ""),
                "id": test.id}
    project = {"name": getattr(test, "project_name", ""),
               "url": getattr(test, "project_url", ""),
               "environment": getattr(test, "project_env", "")}
    report = reporter.build_report(project, run_info, results, counters, duration)
    report["_raw_results"] = results
    report["failures"] = reporter.failures(results)

    subject, body_text, body_html = build_email_content(project, run_info, report)

    pdf_name = f"rapport_{report['project'] or 'projet'}_{tid}.pdf"
    pdf_bytes = None
    try:
        pdf_bytes = render_report_pdf(reporter.render_html(report))
    except Exception:
        traceback.print_exc()
        print(f"[EMAIL] run {tid}: échec de génération du PDF — envoi sans pièce jointe",
              flush=True)
        pdf_bytes = None
    attachments = []
    if pdf_bytes:
        attachments.append({"name": pdf_name, "content": pdf_bytes,
                            "maintype": "application", "subtype": "pdf"})
    print(f"[EMAIL] run {tid}: {len(attachments)} pièce(s) jointe(s) "
          f"({len(pdf_bytes) if pdf_bytes else 0} octets), "
          f"destinataires={recipients}", flush=True)

    sent = 0
    for recipient in recipients:
        if mailer.send_email(recipient, subject, body_html,
                             body_text=body_text, attachments=attachments):
            sent += 1
    return sent, recipients