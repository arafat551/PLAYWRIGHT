"""Reporter: aggregates run results into a final professional report.

Sections 26-40: header (project/env/date/launcher/duration), per-module
results, anomalies table, severity, percentage, screenshot links.
"""
import json
import os
from datetime import datetime

from ..config import env


def success_percent(counters):
    total = counters["total"]
    if total <= 0:
        return 0.0
    passed = counters["passed"]
    return round((passed / total) * 100, 1)


def group_by_module(results):
    modules = {}
    for r in results:
        d = dict(r)
        if "function_name" in d and "function" not in d:
            d["function"] = d["function_name"]
        m = d.get("module", "Général")
        modules.setdefault(m, []).append(d)
    return modules


def module_stats(modules):
    stats = []
    for name, rows in modules.items():
        stats.append({
            "name": name,
            "total": len(rows),
            "pass": sum(1 for r in rows if r["status"] == "PASS"),
            "fail": sum(1 for r in rows if r["status"] == "FAIL"),
            "warning": sum(1 for r in rows if r["status"] == "WARNING"),
            "skipped": sum(1 for r in rows if r["status"] == "SKIPPED"),
        })
    return stats


def failures(results):
    return [r for r in results if r["status"] == "FAIL"]


def severity_counts(results):
    counts = {"CRITICAL": 0, "MAJOR": 0, "MINOR": 0}
    for r in results:
        sev = (r.get("severity") or "").upper()
        if sev in counts:
            counts[sev] += 1
    return counts


def build_report(project, test_run, results, counters, duration):
    modules = group_by_module(results)
    return {
        "project": project.get("name", ""),
        "environment": project.get("environment", ""),
        "date": (test_run.get("started_at") or datetime.now().isoformat()),
        "launched_by": test_run.get("launched_by", ""),
        "duration": _fmt_duration(duration),
        "run_type": test_run.get("run_type", "Test"),
        "total": counters["total"],
        "passed": counters["passed"],
        "failed": counters["failed"],
        "warning": counters["warning"],
        "skipped": counters["skipped"],
        "success_pct": success_percent(counters),
        "modules": modules,
        "module_stats": module_stats(modules),
        "failures": failures(results),
        "severity_counts": severity_counts(results),
    }


def render_html(report, report_path=None):
    """Render a self-contained HTML report file."""
    rows = []
    for m, items in report["modules"].items():
        rows.append(f"<h3>{m}</h3><table><thead><tr><th>Fonction</th><th>Statut</th><th>Preuve</th></tr></thead><tbody>")
        for r in items:
            status = r["status"]
            icon = {"PASS": "&#9989;", "FAIL": "&#10060;", "WARNING": "&#9888;&#65039;", "SKIPPED": "&#128197;"}.get(status, "&#10067;")
            shot = ""
            if r.get("screenshot"):
                # The report lives in <sdet_app>/reports/, screenshots live in
                # <sdet_app>/static/screenshots/ — resolve relative to file.
                shot = f'<a href="../static/{r["screenshot"]}">capture</a>'
            rows.append(f'<tr><td>{r.get("function")}</td><td>{icon} {status}</td><td>{shot}</td></tr>')
        rows.append("</tbody></table>")

    html = f"""<!DOCTYPE html><html><head><meta charset="utf-8"><title>Rapport AUTOMATION</title>
<style>body{{font-family:sans-serif;margin:2rem;color:#222}}
h1{{border-bottom:2px solid #333;padding-bottom:.3rem}}
table{{border-collapse:collapse;width:100%;margin-bottom:1.5rem}}
th,td{{border:1px solid #ccc;padding:.4rem .6rem;text-align:left;font-size:.92rem}}
th{{background:#f0f0f0}}.ok{{color:green}}.bad{{color:red}}.warn{{color:#b58900}}</style>
</head><body>
<h1>RAPPORT AUTOMATION</h1>
<p><b>Projet :</b> {report['project']} &nbsp; <b>Environnement :</b> {report['environment']}
&nbsp; <b>Type :</b> {report['run_type']}<br>
<b>Date :</b> {report['date']} &nbsp; <b>Lancé par :</b> {report['launched_by']} &nbsp; <b>Durée :</b> {report['duration']}</p>
<h2>Tests : {report['total']}</h2>
<p><span class="ok">PASS : {report['passed']}</span> &nbsp; <span class="bad">FAIL : {report['failed']}</span>
&nbsp; <span class="warn">WARNING : {report['warning']}</span> &nbsp; SKIPPED : {report['skipped']}</p>
<p><b>Réussite : {report['success_pct']} %</b></p>
<h2>Résultats par module</h2>
{''.join(rows)}
</body></html>"""
    if report_path:
        os.makedirs(os.path.dirname(report_path), exist_ok=True)
        with open(report_path, "w", encoding="utf-8") as f:
            f.write(html)
    return html


def write_report_file(project, test_run, results, counters, duration):
    report = build_report(project, test_run, results, counters, duration)
    fname = f"rapport_{project.get('name','projet')}_{test_run.get('id')}.html"
    path = os.path.join(env.REPORT_DIR, fname)
    render_html(report, path)
    return f"reports/{fname}"


def _fmt_duration(seconds):
    seconds = int(seconds or 0)
    m, s = divmod(seconds, 60)
    return f"{m}m {s:02d}s"


def counters_from_results(results):
    counters = {"total": 0, "passed": 0, "failed": 0, "warning": 0, "skipped": 0}
    for r in results:
        counters["total"] += 1
        s = r["status"]
        if s == "PASS":
            counters["passed"] += 1
        elif s == "FAIL":
            counters["failed"] += 1
        elif s == "WARNING":
            counters["warning"] += 1
        else:
            counters["skipped"] += 1
    return counters
