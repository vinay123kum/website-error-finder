"""Email alerts after a scan. Configure once in configs/alerts.json:
{
  "smtp_host": "smtp.gmail.com", "smtp_port": 587, "smtp_user": "you@gmail.com", "smtp_password": "app-password",
  "from": "Website Error Finder <you@gmail.com>", "to": ["team@example.com"],
  "min_severity": "high",          # email only if there are new issues at this severity or above
  "always_send": false,            # true = send a summary after every scan even with nothing new
  "report_base_url": "http://127.0.0.1:8030"
}
Per-site recipients can be added with "alert_email" in configs/<domain>.json.
"""
from __future__ import annotations
import json, smtplib, ssl
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from pathlib import Path
from .models import SEV_ICON, SEVERITIES

ROOT = Path(__file__).resolve().parent.parent
ALERTS = ROOT / "configs" / "alerts.json"
CAT_LABEL = {"functionality": "Broken Functionality", "cta": "CTA / Wrong Links", "forms": "Forms", "responsive": "Responsive", "content": "Content",
             "consistency": "Consistency", "navigation": "Navigation", "visual": "Visual", "tracking": "Tracking", "seo": "SEO", "performance": "Performance"}


def load_alert_config() -> dict | None:
    if not ALERTS.exists(): return None
    try: return json.loads(ALERTS.read_text("utf-8"))
    except Exception: return None


def build_email(scan: dict, summary: dict, issues: list[dict], base_url: str) -> tuple[str, str, str]:
    rank = {s: i for i, s in enumerate(SEVERITIES)}
    live = [i for i in issues if i["state"] != "fixed"]
    new = [i for i in live if i["state"] == "new"]
    sev = summary.get("severity", {})
    subject = f"[{scan['site']}] {summary.get('new', 0)} new issues since last scan · {sev.get('critical',0)} critical, {sev.get('high',0)} high"
    if summary.get("prev_scan") is None:
        subject = f"[{scan['site']}] First scan: {summary.get('total',0)} issues · {sev.get('critical',0)} critical, {sev.get('high',0)} high"
    link = f"{base_url.rstrip('/')}/scan/{scan['id']}"
    top = sorted(new or live, key=lambda i: rank.get(i["severity"], 9))[:15]
    rows = "".join(f"<tr><td style='padding:6px 8px;white-space:nowrap'>{SEV_ICON[i['severity']]} {i['severity']}</td><td style='padding:6px 8px'>{CAT_LABEL.get(i['category'], i['category'])}</td><td style='padding:6px 8px'><b>{i['title']}</b><br><span style='color:#555'>{i['page']}</span>" + (f"<br><span style='color:#777;font-size:12px'>{i['current'][:140]}</span>" if i.get('current') else "") + "</td></tr>" for i in top)
    html = f"""<div style="font:14px/1.5 -apple-system,Segoe UI,sans-serif;color:#1b1f24;max-width:720px">
<h2 style="margin:0 0 4px">Is anything wrong with {scan['site']}?</h2>
<div style="color:#666;margin-bottom:14px">Scan #{scan['id']} · {scan.get('started','')[:16].replace('T',' ')} · {scan.get('pages') or 0} pages</div>
<table style="border-collapse:collapse;margin-bottom:14px"><tr>
<td style="padding:8px 14px;border:1px solid #e5e7eb"><b style="font-size:20px;color:#dc2626">{sev.get('critical',0)}</b><br>Critical</td>
<td style="padding:8px 14px;border:1px solid #e5e7eb"><b style="font-size:20px;color:#ea580c">{sev.get('high',0)}</b><br>High</td>
<td style="padding:8px 14px;border:1px solid #e5e7eb"><b style="font-size:20px;color:#ca8a04">{sev.get('medium',0)}</b><br>Medium</td>
<td style="padding:8px 14px;border:1px solid #e5e7eb"><b style="font-size:20px;color:#2563eb">{sev.get('low',0)}</b><br>Low</td>
<td style="padding:8px 14px;border:1px solid #e5e7eb"><b style="font-size:20px;color:#16a34a">{summary.get('new',0)}</b><br>New</td>
<td style="padding:8px 14px;border:1px solid #e5e7eb"><b style="font-size:20px">{summary.get('existing',0)}</b><br>Existing</td>
<td style="padding:8px 14px;border:1px solid #e5e7eb"><b style="font-size:20px;color:#6b7280">{summary.get('fixed',0)}</b><br>Fixed</td></tr></table>
<h3 style="margin:14px 0 6px">{'New issues' if new else 'Top issues'}</h3>
<table style="border-collapse:collapse;width:100%;border:1px solid #e5e7eb">{rows}</table>
<p><a href="{link}" style="display:inline-block;background:#111;color:#fff;padding:10px 16px;border-radius:8px;text-decoration:none">Open full report</a></p>
<p style="color:#888;font-size:12px">Sent automatically by Website Error Finder.</p></div>"""
    text = subject + "\n\n" + "\n".join(f"[{i['severity']}] {i['title']} — {i['page']}" for i in top) + f"\n\nFull report: {link}"
    return subject, html, text


def send_alert(scan: dict, summary: dict, issues: list[dict], extra_to: list[str] | None = None, log=print) -> bool:
    cfg = load_alert_config()
    if not cfg:
        log("Alerts: configs/alerts.json not found, skipping email."); return False
    to = list(cfg.get("to", [])) + list(extra_to or [])
    if not to:
        log("Alerts: no recipients."); return False
    rank = {s: i for i, s in enumerate(SEVERITIES)}
    thr = rank.get(cfg.get("min_severity", "high"), 1)
    worthy = [i for i in issues if i["state"] == "new" and rank.get(i["severity"], 9) <= thr]
    if not worthy and not cfg.get("always_send") and summary.get("prev_scan") is not None:
        log("Alerts: nothing new at or above threshold, no email sent."); return False
    subject, html, text = build_email(scan, summary, issues, cfg.get("report_base_url", "http://127.0.0.1:8030"))
    msg = MIMEMultipart("alternative"); msg["Subject"] = subject; msg["From"] = cfg.get("from", cfg["smtp_user"]); msg["To"] = ", ".join(to)
    msg.attach(MIMEText(text, "plain", "utf-8")); msg.attach(MIMEText(html, "html", "utf-8"))
    try:
        port = int(cfg.get("smtp_port", 587))
        if port == 465:
            srv = smtplib.SMTP_SSL(cfg["smtp_host"], port, context=ssl.create_default_context(), timeout=30)
        else:
            srv = smtplib.SMTP(cfg["smtp_host"], port, timeout=30); srv.starttls(context=ssl.create_default_context())
        srv.login(cfg["smtp_user"], cfg["smtp_password"]); srv.sendmail(msg["From"], to, msg.as_string()); srv.quit()
        log(f"Alerts: email sent to {', '.join(to)}"); return True
    except Exception as e:
        log(f"Alerts: email failed: {e}"); return False
