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
        "project_url": (project.get("url") or "").strip(),
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
        "status": str(test_run.get("status") or ""),
        "modules": modules,
        "module_stats": module_stats(modules),
        "failures": failures(results),
        "severity_counts": severity_counts(results),
    }


def render_html(report, report_path=None):
    """Render a self-contained HTML report file with gallery, lightbox, and timeline."""
    import json as _json

    pct = report["success_pct"]
    pct_cls = "score-good" if pct >= 80 else ("score-mid" if pct >= 50 else "score-bad")

    # Collect all screenshots for the lightbox
    all_shots = []
    shot_idx = 0
    for m, items in report["modules"].items():
        for r in items:
            if r.get("screenshot"):
                shot_rel = f'../static/{r["screenshot"]}'
                all_shots.append({
                    "src": shot_rel,
                    "func": r.get("function", ""),
                    "module": m,
                    "status": r["status"],
                    "idx": shot_idx,
                })
                shot_idx += 1

    # Timeline HTML
    tl_items = ""
    step = 0
    for r in report.get("_raw_results", []):
        step += 1
        status = r.get("status", "")
        icon_pass = '<svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.5"><path d="M20 6L9 17l-5-5"/></svg>'
        icon_fail = '<svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.5"><circle cx="12" cy="12" r="10"/><path d="M15 9l-6 6M9 9l6 6"/></svg>'
        icon_warn = '<svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.5"><path d="M12 9v4M12 17h.01"/><path d="M10.29 3.86L1.82 18a2 2 0 001.71 3h16.94a2 2 0 001.71-3L13.71 3.86a2 2 0 00-3.42 0z"/></svg>'
        icon_skip = '<svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.5"><path d="M5 12h14"/></svg>'
        icon = {"PASS": icon_pass, "FAIL": icon_fail, "WARNING": icon_warn}.get(status, icon_skip)
        shot_html = ""
        if r.get("screenshot"):
            shot_html = f'<div class="timeline-shot"><img src="../static/{r["screenshot"]}" alt="{r.get("function","")}" loading="lazy" onclick="openLightboxBySrc(this.src)"></div>'
        tl_items += (
            f'<div class="timeline-item timeline-{status.lower()}">'
            f'<div class="timeline-dot">{icon}</div>'
            f'<div class="timeline-content">'
            f'<div class="timeline-head"><span class="badge badge-{status}">{status}</span> '
            f'<span class="timeline-func">{r.get("function","")}</span>'
            f'{" <span class=muted> — " + (r.get("action","") or "") + "</span>" if r.get("action") and r.get("action") not in (r.get("function") or "") else ""}</div>'
            f'{shot_html}</div></div>'
        )
    timeline_section = ""
    if step:
        legend = (
            '<div class="tl-legend">'
            '<span class="tl-legend-item"><span class="tl-legend-dot" style="background:#16a34a"></span> PASS — étape réussie</span>'
            '<span class="tl-legend-item"><span class="tl-legend-dot" style="background:#dc2626"></span> FAIL — étape échouée</span>'
            '<span class="tl-legend-item"><span class="tl-legend-dot" style="background:#d97706"></span> WARNING — alerte / à vérifier</span>'
            '<span class="tl-legend-item"><span class="tl-legend-dot" style="background:#94a3b8"></span> SKIPPED — étape ignorée</span>'
            '</div>'
        )
        timeline_section = f'<div class="card"><h2>Timeline des étapes</h2>{legend}<div class="timeline">{tl_items}</div></div>'

    # Per-module result rows with step numbers and larger thumbnails
    rows = []
    global_step = 0
    for m, items in report["modules"].items():
        total = len(items)
        pass_count = sum(1 for i in items if i["status"] == "PASS")
        fail_count = sum(1 for i in items if i["status"] == "FAIL")
        warn_count = sum(1 for i in items if i["status"] == "WARNING")
        skip_count = sum(1 for i in items if i["status"] == "SKIPPED")
        pct_mod = round((pass_count / total) * 100, 1) if total else 0
        pct_bar = round((pass_count / total) * 100) if total else 0
        open_attr = " open" if fail_count > 0 else ""
        rows.append(f'<details class="module-block"{open_attr}><summary>'
                    f'<span class="module-title">{m}</span>'
                    f'<span class="module-counts muted">PASS {pass_count} / FAIL {fail_count} / WARN {warn_count} / SKIP {skip_count}</span>'
                    f'<span class="module-pct">{pct_mod}%</span></summary>')
        if total:
            rows.append(f'<div class="module-bar"><div class="module-bar-fill" style="width:{pct_bar}%"></div></div>')
        rows.append('<table class="data-table"><thead><tr><th style="width:44px">#</th><th>Statut</th><th>Fonction</th><th>Attendu / Obtenu</th><th style="width:180px">Capture</th></tr></thead><tbody>')
        for r in items:
            global_step += 1
            status = r["status"]
            icon = {"PASS": "&#9989;", "FAIL": "&#10060;", "WARNING": "&#9888;&#65039;", "SKIPPED": "&#128197;"}.get(status, "&#10067;")
            shot = ""
            if r.get("screenshot"):
                shot_rel = f'../static/{r["screenshot"]}'
                shot = f'<a href="javascript:void(0)" onclick="openLightboxBySrc(\'{shot_rel}\')" class="shot-link"><img class="report-thumb" src="{shot_rel}" alt="capture {r.get("function","")}"></a>'
            obtained = ""
            if status in ("FAIL", "WARNING") and r.get("obtained"):
                obtained = f'<div class="fail-detail">Obtenu : {r["obtained"]}</div>'
            rows.append(f'<tr class="row-{status}"><td class="step-num">{global_step}</td>'
                        f'<td><span class="badge badge-{status}">{icon} {status}</span></td>'
                        f'<td>{r.get("function","")}'
                        f'{" <span class=muted> — " + (r.get("action","") or "") + "</span>" if r.get("action") and r.get("action") not in (r.get("function") or "") else ""}</td>'
                        f'<td class="text-small"><span class="muted">{r.get("expected","")}</span>{obtained}</td>'
                        f'<td>{shot}</td></tr>')
        rows.append("</tbody></table></details>")

    # Lightbox JSON data
    shots_json = _json.dumps(all_shots, ensure_ascii=False)

    project_url = (report.get("project_url") or "").strip()
    url_line = (f'<p><b>URL :</b> <a href="{project_url}">{project_url}</a></p>'
                if project_url else "")

    html = f"""<!DOCTYPE html><html lang="fr"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>Rapport AUTOMATION — {report['project']}</title>
<link rel="preconnect" href="https://fonts.googleapis.com">
<link rel="preconnect" href="https://fonts.gstatic.com" crossorigin>
<link href="https://fonts.googleapis.com/css2?family=Inter:wght@400;500;600;700;800&display=swap" rel="stylesheet">
<style>
:root {{
  --brand:#4f46e5; --brand-100:#e0e7ff; --brand-600:#4338ca;
  --success:#16a34a; --success-050:#ecfdf5; --success-600:#15803d;
  --danger:#dc2626; --danger-050:#fef2f2; --danger-600:#b91c1c;
  --warning:#d97706; --warning-050:#fffbeb;
  --bg:#f4f6fb; --surface:#fff; --surface-2:#f8fafc;
  --border:#e5e9f2; --border-strong:#d8dde9;
  --text:#0f172a; --text-2:#475569; --muted:#8b94a7;
  --radius:12px; --radius-sm:8px;
  --shadow-sm:0 1px 2px rgba(16,24,40,.04),0 1px 3px rgba(16,24,40,.05);
  --shadow-md:0 4px 6px -1px rgba(16,24,40,.06),0 2px 8px -2px rgba(16,24,40,.06);
}}
*{{margin:0;padding:0;box-sizing:border-box}}
body{{font-family:"Inter",sans-serif;background:var(--bg);color:var(--text);line-height:1.55;font-size:15px;padding:2rem}}
a{{color:var(--brand);text-decoration:none}}
h1{{font-size:1.5rem;font-weight:800;margin-bottom:.3rem}}
h2{{font-size:1.15rem;font-weight:700;margin:1.4rem 0 .7rem}}
.muted{{color:var(--muted)}}
.card{{background:var(--surface);border:1px solid var(--border);border-radius:var(--radius);padding:1.2rem 1.4rem;margin-bottom:1.2rem;box-shadow:var(--shadow-sm)}}
.badge{{display:inline-block;padding:.22rem .6rem;border-radius:999px;font-size:.72rem;font-weight:700;letter-spacing:.02em}}
.badge-PASS{{background:var(--success-050);color:var(--success-600)}}
.badge-FAIL{{background:var(--danger-050);color:var(--danger-600)}}
.badge-WARNING{{background:var(--warning-050);color:var(--warning)}}
.badge-SKIPPED{{background:var(--surface-2);color:var(--muted)}}
.score-circle{{width:88px;height:88px;border-radius:50%;display:flex;align-items:center;justify-content:center;font-size:1.4rem;font-weight:800;color:#fff;box-shadow:inset 0 -8px 16px rgba(0,0,0,.12),inset 0 3px 8px rgba(255,255,255,.18)}}
.score-good{{background:linear-gradient(135deg,#22c55e,var(--success-600))}}
.score-mid{{background:linear-gradient(135deg,#f59e0b,#b45309)}}
.score-bad{{background:linear-gradient(135deg,#ef4444,var(--danger-600))}}
.stats-inline{{display:flex;gap:1rem;margin:1rem 0 1.5rem;flex-wrap:wrap}}
.stat-card{{flex:1 1 130px;max-width:170px;background:var(--surface);border:1px solid var(--border);border-radius:var(--radius);padding:.8rem 1rem;text-align:center;box-shadow:var(--shadow-sm)}}
.stat-number{{font-size:1.2rem;font-weight:800}}
.stat-success{{color:var(--success-600)}} .stat-danger{{color:var(--danger-600)}} .stat-warning{{color:var(--warning)}}
.stat-label{{font-size:.75rem;font-weight:600;color:var(--muted);text-transform:uppercase;letter-spacing:.04em;margin-top:.15rem}}
.severity-row{{display:flex;gap:.7rem;flex-wrap:wrap;margin-bottom:1.5rem}}
.sev{{padding:.4rem 1rem;border-radius:999px;font-size:.82rem;font-weight:700;color:#fff}}
.sev-critical{{background:#7f1d1d}} .sev-major{{background:#dc2626}} .sev-minor{{background:#f59e0b}}

/* Module blocks */
.module-block{{border:1px solid var(--border);border-radius:10px;padding:.7rem 1rem;margin-bottom:.8rem;background:var(--surface)}}
.module-block summary{{display:flex;align-items:center;gap:1rem;cursor:pointer;list-style:none;flex-wrap:wrap}}
.module-block summary::-webkit-details-marker{{display:none}}
.module-title{{font-weight:700;flex:1}} .module-counts{{font-size:.8rem}} .module-pct{{font-weight:700;color:var(--brand)}}
.module-bar{{height:7px;background:#eef1f7;border-radius:999px;overflow:hidden;margin-top:.5rem}}
.module-bar-fill{{height:100%;border-radius:999px;background:linear-gradient(90deg,var(--brand),#818cf8)}}

/* Data table */
.data-table{{width:100%;border-collapse:collapse;margin-top:.5rem}}
.data-table th{{text-align:left;font-size:.75rem;font-weight:700;text-transform:uppercase;letter-spacing:.04em;color:var(--text-2);padding:.55rem .6rem;border-bottom:2px solid var(--border)}}
.data-table td{{padding:.5rem .6rem;border-bottom:1px solid var(--border);font-size:.9rem;vertical-align:top}}
.data-table tbody tr{{transition:background .1s}} .data-table tbody tr:hover{{background:var(--surface-2)}}
.data-table td a{{font-weight:550}}
.step-num{{text-align:center;font-weight:700;font-size:.78rem;color:var(--muted)}}
.fail-detail{{color:#991b1b;font-size:.84rem;margin-top:.2rem;font-weight:550}}
.text-small{{font-size:.84rem}} .text-muted{{color:var(--muted)}}
.row-FAIL{{background:rgba(254,226,226,0.25)}}
.row-WARNING{{background:rgba(254,243,199,0.25)}}

/* Gallery */
.gallery-head{{display:flex;align-items:center;justify-content:space-between;gap:1rem;margin-bottom:1rem}}
.gallery-head h2{{margin:0}}
.shot-gallery{{display:grid;grid-template-columns:repeat(auto-fill,minmax(180px,1fr));gap:.7rem}}
.gallery-item{{position:relative;border:none;padding:0;border-radius:12px;overflow:hidden;cursor:pointer;background:var(--surface-2);aspect-ratio:16/10;transition:transform .18s,box-shadow .18s}}
.gallery-item:hover{{transform:translateY(-3px) scale(1.02);box-shadow:0 12px 28px rgba(15,23,42,.18)}}
.gallery-item img{{width:100%;height:100%;object-fit:cover;display:block}}
.gallery-overlay{{position:absolute;bottom:0;left:0;right:0;display:flex;align-items:center;gap:.4rem;padding:.35rem .5rem;background:linear-gradient(transparent,rgba(0,0,0,.65));opacity:0;transition:opacity .18s}}
.gallery-item:hover .gallery-overlay{{opacity:1}}
.gallery-overlay .badge{{font-size:.65rem;padding:.15rem .45rem}}
.gallery-label{{font-size:.72rem;font-weight:600;color:#fff;white-space:nowrap;overflow:hidden;text-overflow:ellipsis}}
.gallery-step{{position:absolute;top:6px;left:6px;width:24px;height:24px;border-radius:50%;background:rgba(0,0,0,.55);color:#fff;font-size:.7rem;font-weight:700;display:flex;align-items:center;justify-content:center}}

/* Report thumbs */
.shot-link{{display:inline-block;line-height:0}}
.report-thumb{{width:150px;aspect-ratio:16/10;object-fit:cover;border-radius:8px;border:1px solid var(--border-strong);transition:transform .15s,box-shadow .15s}}
.report-thumb:hover{{transform:scale(1.05);box-shadow:0 4px 14px rgba(15,23,42,.14)}}

/* Timeline */
.tl-legend{{display:flex;flex-wrap:wrap;gap:.7rem;margin-bottom:1rem;padding:.7rem 1rem;background:var(--surface-2);border-radius:10px;border:1px solid var(--border)}}
.tl-legend-item{{display:flex;align-items:center;gap:.35rem;font-size:.78rem;font-weight:600;color:var(--text-2)}}
.tl-legend-dot{{width:10px;height:10px;border-radius:50%;flex-shrink:0}}
.timeline{{position:relative;padding-left:2rem;margin-top:.8rem}}
.timeline::before{{content:'';position:absolute;left:11px;top:0;bottom:0;width:2px;background:linear-gradient(180deg,var(--brand-100),var(--border));border-radius:999px}}
.timeline-item{{position:relative;display:flex;align-items:flex-start;gap:.9rem;padding:.65rem 0}}
.timeline-dot{{flex-shrink:0;width:24px;height:24px;border-radius:50%;display:flex;align-items:center;justify-content:center;z-index:1;margin-left:-2rem;box-shadow:0 0 0 3px var(--surface)}}
.timeline-pass .timeline-dot{{background:var(--success-050);color:var(--success);border:2px solid #bbf7d0}}
.timeline-fail .timeline-dot{{background:var(--danger-050);color:var(--danger);border:2px solid #fecaca}}
.timeline-warning .timeline-dot{{background:var(--warning-050);color:var(--warning);border:2px solid #fde68a}}
.timeline-skipped .timeline-dot{{background:var(--surface-2);color:var(--muted);border:2px solid var(--border)}}
.timeline-content{{flex:1;min-width:0}}
.timeline-head{{display:flex;align-items:center;gap:.5rem;flex-wrap:wrap}}
.timeline-func{{font-weight:600;font-size:.92rem}}
.timeline-shot{{margin-top:.4rem}}
.timeline-shot img{{width:240px;max-width:100%;aspect-ratio:16/10;object-fit:cover;border-radius:8px;border:1px solid var(--border-strong);cursor:pointer;transition:transform .15s,box-shadow .15s}}
.timeline-shot img:hover{{transform:scale(1.03);box-shadow:0 6px 18px rgba(15,23,42,.14)}}

/* Lightbox */
.lightbox-overlay{{position:fixed;inset:0;z-index:9999;background:rgba(10,15,30,.92);backdrop-filter:blur(8px);display:flex;flex-direction:column;align-items:center;justify-content:center;opacity:0;pointer-events:none;transition:opacity .25s}}
.lightbox-overlay.open{{opacity:1;pointer-events:auto}}
.lightbox-toolbar{{position:absolute;top:0;left:0;right:0;display:flex;align-items:center;justify-content:space-between;padding:1rem 1.4rem;background:linear-gradient(180deg,rgba(10,15,30,.85) 0%,transparent 100%);z-index:2}}
.lightbox-counter{{font-size:.85rem;font-weight:700;color:rgba(255,255,255,.7);letter-spacing:.04em}}
.lightbox-label{{font-size:.88rem;font-weight:600;color:#fff;flex:1;text-align:center;padding:0 1rem;white-space:nowrap;overflow:hidden;text-overflow:ellipsis}}
.lightbox-actions{{display:flex;gap:.4rem}}
.lightbox-btn{{width:40px;height:40px;border-radius:10px;border:1px solid rgba(255,255,255,.15);background:rgba(255,255,255,.08);color:#fff;display:flex;align-items:center;justify-content:center;cursor:pointer;transition:background .15s,border-color .15s}}
.lightbox-btn:hover{{background:rgba(255,255,255,.18);border-color:rgba(255,255,255,.3)}}
.lightbox-close-btn:hover{{background:var(--danger);border-color:var(--danger)}}
.lightbox-img-wrap{{max-width:90vw;max-height:85vh;display:flex;align-items:center;justify-content:center}}
.lightbox-img-wrap img{{max-width:90vw;max-height:85vh;object-fit:contain;border-radius:10px;box-shadow:0 24px 64px rgba(0,0,0,.5)}}

/* Severity badges */
.sev{{display:inline-block;padding:.4rem 1rem;border-radius:99px;font-size:.82rem;font-weight:700;color:#fff}}
.sev-critical{{background:#7f1d1d}} .sev-major{{background:#dc2626}} .sev-minor{{background:#f59e0b}}

/* Print */
@media print {{
  body{{padding:1rem;background:#fff}}
  .lightbox-overlay,.gallery-item button{{display:none !important}}
  .shot-gallery{{grid-template-columns:repeat(4,1fr)}}
  .timeline-shot img{{width:160px}}
}}
@media (max-width:700px) {{
  body{{padding:1rem}}
  .shot-gallery{{grid-template-columns:repeat(auto-fill,minmax(130px,1fr))}}
  .report-thumb{{width:110px !important}}
  .timeline-shot img{{width:180px}}
}}
</style>
</head><body>

<h1>RAPPORT AUTOMATION</h1>

<div class="card" style="display:flex;justify-content:space-between;align-items:center;flex-wrap:wrap;gap:1rem">
  <div>
    <p><b>Projet :</b> {report['project']} &nbsp; <b>Environnement :</b> {report['environment']} &nbsp; <b>Type :</b> {report['run_type']}</p>
    <p><b>Date :</b> {report['date']} &nbsp; <b>Lancé par :</b> {report['launched_by']} &nbsp; <b>Durée :</b> {report['duration']}</p>
    {f'<p><b>Statut :</b> ' + report.get('status', '') + '</p>' if report.get('status') else ''}
    {url_line}
  </div>
  <div style="display:flex;flex-direction:column;align-items:center;gap:.4rem">
    <div class="score-circle {pct_cls}">{pct}%</div>
    <span style="font-size:.82rem;font-weight:600;color:var(--muted)">Réussite</span>
  </div>
</div>

<div class="stats-inline">
  <div class="stat-card"><div class="stat-number">{report['total']}</div><div class="stat-label">Tests</div></div>
  <div class="stat-card"><div class="stat-number stat-success">{report['passed']}</div><div class="stat-label">PASS</div></div>
  <div class="stat-card"><div class="stat-number stat-danger">{report['failed']}</div><div class="stat-label">FAIL</div></div>
  <div class="stat-card"><div class="stat-number stat-warning">{report['warning']}</div><div class="stat-label">WARNING</div></div>
  <div class="stat-card"><div class="stat-number" style="color:var(--muted)">{report['skipped']}</div><div class="stat-label">SKIPPED</div></div>
</div>

<div class="severity-row">
  {''.join(f'<span class="sev sev-{s.lower()}">{s} : {c}</span>' for s, c in report['severity_counts'].items() if c)}
</div>

{timeline_section}

<div class="card">
  <h2>Résultats par module</h2>
  {''.join(rows)}
</div>

{f'''<div class="lightbox-overlay" id="lightbox" onclick="closeLightbox(event)">
  <div class="lightbox-toolbar">
    <span class="lightbox-counter" id="lb-counter"></span>
    <span class="lightbox-label" id="lb-label"></span>
    <div class="lightbox-actions">
      <button class="lightbox-btn" onclick="event.stopPropagation();navLightbox(-1)" title="Precedente">
        <svg width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.5"><path d="M15 18l-6-6 6-6"/></svg>
      </button>
      <button class="lightbox-btn" onclick="event.stopPropagation();navLightbox(1)" title="Suivante">
        <svg width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.5"><path d="M9 18l6-6-6-6"/></svg>
      </button>
      <button class="lightbox-btn lightbox-close-btn" onclick="event.stopPropagation();closeLightbox()" title="Fermer">
        <svg width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.5"><path d="M18 6L6 18M6 6l12 12"/></svg>
      </button>
    </div>
  </div>
  <div class="lightbox-img-wrap" onclick="event.stopPropagation()">
    <img id="lb-img" src="" alt="capture">
  </div>
</div>''' if all_shots else ''}

<script>
var _lbAll={shots_json};var _lbIdx=0;
function openLightbox(i){{_lbIdx=i;_renderLB();document.getElementById('lightbox').classList.add('open');document.body.style.overflow='hidden'}}
function openLightboxBySrc(src){{for(var i=0;i<_lbAll.length;i++){{if(_lbAll[i].src===src){{openLightbox(i);return}}}}document.getElementById('lb-img').src=src;document.getElementById('lightbox').classList.add('open');document.body.style.overflow='hidden'}}
function closeLightbox(e){{if(e&&e.target&&e.target.id!=='lightbox')return;document.getElementById('lightbox').classList.remove('open');document.body.style.overflow=''}}
function navLightbox(d){{if(!_lbAll.length)return;_lbIdx=(_lbIdx+d+_lbAll.length)%_lbAll.length;_renderLB()}}
function _renderLB(){{var s=_lbAll[_lbIdx];if(!s)return;document.getElementById('lb-img').src=s.src;document.getElementById('lb-counter').textContent=(_lbIdx+1)+' / '+_lbAll.length;document.getElementById('lb-label').textContent=s.module+' — '+s.func}}
document.addEventListener('keydown',function(e){{var lb=document.getElementById('lightbox');if(!lb||!lb.classList.contains('open'))return;if(e.key==='Escape')closeLightbox();else if(e.key==='ArrowLeft')navLightbox(-1);else if(e.key==='ArrowRight')navLightbox(1)}});
</script>
</body></html>"""
    if report_path:
        os.makedirs(os.path.dirname(report_path), exist_ok=True)
        with open(report_path, "w", encoding="utf-8") as f:
            f.write(html)
    return html


def write_report_file(project, test_run, results, counters, duration):
    report = build_report(project, test_run, results, counters, duration)
    report["_raw_results"] = [dict(r) if not isinstance(r, dict) else r for r in results]
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
