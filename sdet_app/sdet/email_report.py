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

    if failed:
        verdict, verdict_color = "ÉCHEC", "#b91c1c"
    elif warning:
        verdict, verdict_color = "ATTENTION", "#b45309"
    elif total:
        verdict, verdict_color = "RÉUSSITE", "#15803d"
    else:
        verdict, verdict_color = "AUCUN TEST EXÉCUTÉ", "#64748b"

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

    stat_rows = "".join(
        f'<tr>'
        f'<td style="padding:14px 20px;border:1px solid #e2e8f0;font-weight:600;'
        f'font-size:15px;color:#0f172a;background:#fafbfe;width:60%">{label}</td>'
        f'<td style="padding:14px 20px;border:1px solid #e2e8f0;text-align:center;'
        f'font-size:18px;font-weight:800;color:{color}">{value}</td></tr>'
        for label, value, color in (
            ("Total", total, "#0f172a"),
            ("PASS", passed, "#15803d"),
            ("FAIL", failed, "#b91c1c"),
            ("WARNING", warning, "#b45309"),
            ("SKIPPED", skipped, "#64748b"),
        ))

    url_html = (f'<br><b>URL :</b> <a href="{project_url}" style="color:#4f46e5">'
                f'{project_url}</a>' if project_url else "")
    meta_block = (
        f'<div style="margin:0 0 18px;padding:12px 16px;background:#f8fafc;'
        f'border-radius:8px;font-size:13px;line-height:1.7">'
        f'<b>Projet :</b> {report["project"]} &nbsp; '
        f'<b>Environnement :</b> {report["environment"]}{url_html}<br>'
        f'<b>Date :</b> {report["date"]} &nbsp; '
        f'<b>Lancé par :</b> {report["launched_by"]} &nbsp; '
        f'<b>Durée :</b> {report["duration"]}'
        f'</div>'
    )

    body_html = f"""<!DOCTYPE html><html lang="fr"><head><meta charset="utf-8"></head>
<body style="margin:0;padding:0;font-family:Arial,Helvetica,sans-serif;background:#f4f6fb">
<div style="max-width:600px;margin:0 auto;padding:18px">
  <div style="background:#4f46e5;border-radius:10px 10px 0 0;padding:12px 20px">
    <h1 style="margin:0;color:#fff;font-size:16px">Rapport d'exécution AUTOMATION</h1>
  </div>
  <div style="background:#fff;border:1px solid #e5e9f2;border-top:0;border-radius:0 0 10px 10px;padding:22px">
    <p style="font-size:14px;margin:0 0 18px;line-height:1.6">{intro}</p>
    {meta_block}
    <table style="border-collapse:collapse;width:100%;font-size:14px">{stat_rows}</table>
    <p style="font-size:15px;margin:18px 0 0;padding:10px 14px;background:#f8fafc;border-radius:8px">
      <b>Taux de réussite : {pct}%</b> — verdict <b style="color:{verdict_color}">{verdict}</b></p>
    <p style="font-size:12px;color:#94a3b8;margin:20px 0 0;border-top:1px solid #f1f5f9;
      padding-top:10px;text-align:center">
      Le rapport complet est joint à cet e-mail (PDF).</p>
  </div>
</div></body></html>"""

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
    """Envoyer le rapport d'un scénario terminé aux destinataires configurés.

    No-op si l'envoi est désactivé ou si aucun destinataire n'est renseigné.
    Returns (sent, recipients)."""
    enabled = setting_bool("report_email_enabled", False)
    if not enabled:
        return 0, []
    recipients = [r.strip() for r in re.split(r"[,;]", get_setting("report_email_recipient", "") or "")
                  if r.strip()]
    if not recipients:
        return 0, []

    test = database.get_test_run(tid)
    if not test:
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