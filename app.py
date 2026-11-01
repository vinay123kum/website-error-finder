"""Dashboard: start scans, view reports, compare with previous scan.
Run: python app.py            (http://127.0.0.1:8030)
Env: WEF_PASSWORD=...  enables HTTP basic auth (user: any, password: WEF_PASSWORD) for hosting.
     WEF_HOST / WEF_PORT    bind address (default 127.0.0.1:8030)
"""
import json, os, re, secrets, sys, threading
from pathlib import Path
from fastapi import FastAPI, Form, Request
from fastapi.responses import HTMLResponse, RedirectResponse, JSONResponse, Response
from fastapi.staticfiles import StaticFiles
sys.path.insert(0, str(Path(__file__).resolve().parent))
from wef.runner import run_scan, DB, DATA
from wef.store import Store
from wef.report import render_report, esc, CAT_LABEL, health_score

app = FastAPI(title="Website Error Finder")
(DATA / "scans").mkdir(parents=True, exist_ok=True)
app.mount("/scans", StaticFiles(directory=str(DATA / "scans")), name="scans")
store = Store(DB)
PASSWORD = os.environ.get("WEF_PASSWORD", "")
USER = os.environ.get("WEF_USER", "")


@app.middleware("http")
async def basic_auth(request: Request, call_next):
    if PASSWORD:
        auth = request.headers.get("authorization", "")
        ok = False
        if auth.startswith("Basic "):
            import base64
            try:
                u, pw = base64.b64decode(auth[6:]).decode().split(":", 1); ok = secrets.compare_digest(pw, PASSWORD) and (not USER or secrets.compare_digest(u, USER))
            except Exception: ok = False
        if not ok:
            return Response("Authentication required", 401, headers={"WWW-Authenticate": 'Basic realm="Website Error Finder"'})
    return await call_next(request)


from wef.report import CSS as REPORT_CSS, SEV_ICON
STYLE = REPORT_CSS + """
.wrap{max-width:1180px;margin:0 auto;padding:20px}
.check{padding:22px;margin-bottom:18px}.check h1{margin:0 0 4px;font-size:20px}.check p{margin:0 0 14px;color:var(--muted)}
.check form{display:flex;gap:8px;flex-wrap:wrap}.check input{font:inherit;padding:11px 13px;border:1px solid var(--line);border-radius:10px;background:#fff;color:var(--ink)}
.check input[name=url]{flex:1;min-width:280px;font-size:15px}.check input[name=max_pages]{width:110px}.check input[name=config]{min-width:230px}
button{font:inherit;padding:11px 20px;border:0;border-radius:10px;background:#0f172a;color:#fff;cursor:pointer;font-weight:700}button:hover{background:#000}
.check .hint{color:var(--muted);font-size:12.5px;margin-top:10px}
h2.sec2{font-size:12px;text-transform:uppercase;letter-spacing:.6px;color:var(--muted);margin:18px 0 10px}
.sites{display:grid;grid-template-columns:repeat(auto-fill,minmax(520px,1fr));gap:14px}
.sitecard{padding:18px;display:grid;grid-template-columns:96px 1fr;gap:18px;align-items:start}
.sitecard .name{font-weight:700;font-size:16px;word-break:break-all}.sitecard .when{color:var(--muted);font-size:12.5px;margin-bottom:8px}
.sitecard .stats{margin-top:0}.sitecard .stat{min-width:64px;padding:6px 10px}.sitecard .stat b{font-size:17px}
.top{margin:10px 0 0;padding:0;list-style:none}.top li{display:flex;gap:8px;align-items:center;font-size:12.5px;padding:4px 0;border-top:1px solid var(--soft)}.top li .pg{margin:0;color:var(--muted);font-size:11.5px;overflow:hidden;text-overflow:ellipsis;white-space:nowrap}
.actions{display:flex;gap:8px;align-items:center;margin-top:12px}.btn{font-size:12.5px;font-weight:600;padding:7px 13px;border-radius:8px;background:#0f172a;color:#fff}.btn:hover{background:#000;text-decoration:none}
.btn.ghost{background:var(--soft);color:var(--ink);border:0;cursor:pointer;font:inherit;font-size:12.5px;font-weight:600;padding:7px 13px}.btn.ghost:hover{background:#e2e8f0}
.trend{font-size:12px;color:var(--muted)}.trend.up{color:var(--ok)}.trend.down{color:var(--crit)}
.running{padding:14px 16px;margin-bottom:12px;display:flex;align-items:center;gap:14px;border-left:5px solid var(--med)}
.running .bar{flex:1;height:8px;background:var(--soft);border-radius:999px;overflow:hidden}.running .bar i{display:block;height:100%;background:var(--med);border-radius:999px}
table{border-collapse:collapse;width:100%}th{font-size:11px;text-transform:uppercase;letter-spacing:.5px;color:var(--muted);text-align:left;padding:8px;border-bottom:1px solid var(--line)}
td{padding:9px 8px;border-bottom:1px solid #f0f1f3;font-size:13px}tr:hover td{background:#fafafa}
.pill{display:inline-block;padding:2px 9px;border-radius:999px;font-size:11px;font-weight:700;text-transform:uppercase;letter-spacing:.4px}.pill.done{background:#dcfce7;color:#166534}.pill.running{background:#fef3c7;color:#92400e}.pill.failed{background:#fee2e2;color:#991b1b}
.empty{color:var(--muted);padding:30px;text-align:center}pre{font-size:12px;white-space:pre-wrap;background:#0b1020;color:#d1d5db;padding:14px;border-radius:8px;max-height:60vh;overflow:auto}
details.hist summary{cursor:pointer;font-weight:700;font-size:14px;padding:14px 16px;list-style:none}details.hist summary::-webkit-details-marker{display:none}details.hist .in{padding:0 16px 16px}
.legend{margin:12px 0 0}
@media(max-width:700px){.sitecard{grid-template-columns:1fr}}
"""


def page(title, body, refresh=0):
    meta = f'<meta http-equiv="refresh" content="{refresh}">' if refresh else ""
    return f"""<!doctype html><html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">{meta}<title>{esc(title)} · Website Error Finder</title><style>{STYLE}</style></head>
<body><div class="hdr"><span class="brand">Website Error Finder</span><span class="site">Is anything wrong with my website?</span><span class="sp"></span><a href="/">Overview</a></div><div class="wrap">{body}</div></body></html>"""


def ring_color(score): return "#16a34a" if score >= 80 else "#d97706" if score >= 50 else "#dc2626"


def progress_of(log: str, max_pages: int = 60):
    m = re.findall(r"\[(\d+)/(\d+)\]", log or "")
    if m: done, total = int(m[-1][0]), int(m[-1][1]); return done, total, f"Crawling page {done} of {total}"
    if "Checking" in (log or ""): return 1, 1, "Checking links…"
    if "Re-verifying" in (log or ""): return 1, 1, "Re-verifying links in the browser…"
    if "Capturing" in (log or ""): return 1, 1, "Capturing screenshots of broken links…"
    if "Running checks" in (log or ""): return 1, 1, "Running checks…"
    return 0, 1, "Starting browser…"


@app.get("/", response_class=HTMLResponse)
def home():
    scans = store.scans()
    latest, prev = {}, {}
    for s in scans:
        if s["status"] != "done": continue
        if s["site"] not in latest: latest[s["site"]] = s
        elif s["site"] not in prev: prev[s["site"]] = s
    running = [s for s in scans if s["status"] == "running"]
    run_html = ""
    for s in running:
        done, total, label = progress_of(s["log"] or "")
        pct = int(100 * done / max(total, 1))
        run_html += f'<div class="card running"><div><b>{esc(s["site"])}</b><div class="trend">{label}</div></div><div class="bar"><i style="width:{pct}%"></i></div><a class="btn ghost" href="/scan/{s["id"]}/log">Live log</a></div>'
    rank = {sv: k for k, sv in enumerate(("critical", "high", "medium", "low"))}
    cards = ""
    for site, s in latest.items():
        summ = json.loads(s["summary"] or "{}"); sv = summ.get("severity", {})
        score = health_score(sv, s["pages"] or 1)
        trend = ""
        if site in prev:
            ps = json.loads(prev[site]["summary"] or "{}"); d = score - health_score(ps.get("severity", {}), prev[site]["pages"] or 1)
            trend = f'<span class="trend {"up" if d > 0 else "down" if d < 0 else ""}">{"▲" if d > 0 else "▼" if d < 0 else "•"} {abs(d)} since previous scan</span>'
        issues = [i for i in store.issues(s["id"]) if i["state"] != "fixed"]
        top = sorted(issues, key=lambda i: (rank.get(i["severity"], 9), 0 if i["state"] == "new" else 1))[:3]
        top_html = "".join(f'<li>{SEV_ICON[i["severity"]]} <b>{esc(i["title"])}</b><span class="pg">{esc(i["page"])}</span></li>' for i in top)
        stats = "".join(f'<div class="stat {x}"><b>{sv.get(x,0)}</b><span>{x.title()}</span></div>' for x in ("critical", "high", "medium", "low"))
        stats += f'<div class="stat new"><b>{summ.get("new",0)}</b><span>New</span></div><div class="stat"><b>{summ.get("fixed",0)}</b><span>Fixed</span></div>'
        cards += f"""<div class="card sitecard"><div class="score" style="background:{ring_color(score)}">{score}</div><div>
<div class="name">{esc(site)}</div><div class="when">Scan #{s['id']} · {esc((s['started'] or '')[:16].replace('T',' '))} · {s['pages'] or 0} pages · {summ.get('total',0)} open issues {trend}</div>
<div class="stats">{stats}</div>
<ul class="top">{top_html}</ul>
<div class="actions"><a class="btn" href="/scan/{s['id']}">Open full report</a><form method="post" action="/scan" style="margin:0"><input type="hidden" name="url" value="{esc(s['start_url'])}"><input type="hidden" name="max_pages" value="{s['pages'] or 60}"><button class="btn ghost">Rescan</button></form></div></div></div>"""
    rows = ""
    for s in scans:
        summ = json.loads(s["summary"] or "{}"); sv = summ.get("severity", {})
        link = f'<a href="/scan/{s["id"]}">Report</a>' if s["status"] == "done" else f'<a href="/scan/{s["id"]}/log">Log</a>'
        rows += f"""<tr><td>#{s['id']}</td><td><b>{esc(s['site'])}</b></td><td>{esc((s['started'] or '')[:16].replace('T',' '))}</td><td><span class="pill {s['status']}">{s['status']}</span></td><td>{s['pages'] or ''}</td><td>{summ.get('total','')}</td>
<td><span class="tag critical">{sv.get('critical','')}</span> <span class="tag high">{sv.get('high','')}</span> <span class="tag medium">{sv.get('medium','')}</span> <span class="tag low">{sv.get('low','')}</span></td><td>{summ.get('new','')} new · {summ.get('fixed','')} fixed</td><td>{link}</td></tr>"""
    body = f"""<div class="card check"><h1>Is anything wrong with your website?</h1><p>Enter a URL. Every page is crawled and tested in a real browser on desktop, tablet and mobile. You get a prioritised list of exactly what to fix, with screenshots.</p>
<form method="post" action="/scan"><input name="url" placeholder="https://example.com" required><input name="max_pages" type="number" value="60" min="1" max="500" title="Max pages"><input name="config" placeholder="Site config (optional)"><button>Check my website</button></form>
<div class="hint">About 20 seconds per page. Subdomains and booking/store pages (e.g. ROLLER) are included. Forms are tested safely: nothing is ever submitted.</div></div>
{run_html}
{('<h2 class="sec2">Your websites · health score out of 100</h2><div class="sites">' + cards + '</div>') if cards else '<div class="card empty">No scans yet. Enter a URL above to start.</div>'}
<div class="legend"><b>Score:</b><span>80–100 healthy</span><span>50–79 needs attention</span><span>below 50 fix now</span><span style="margin-left:auto"></span><b>Critical</b><span>losing customers now</span><b>High</b><span>visibly broken</span><b>Medium</b><span>quality</span><b>Low</b><span>housekeeping</span></div>
<details class="card hist" style="margin-top:16px"><summary>Scan history ({len(scans)}) ▾</summary><div class="in"><table><tr><th>#</th><th>Site</th><th>Started</th><th>Status</th><th>Pages</th><th>Issues</th><th>Crit / High / Med / Low</th><th>vs previous</th><th></th></tr>{rows or '<tr><td colspan=9 class=empty>No scans yet.</td></tr>'}</table></div></details>"""
    return page("Overview", body, refresh=15 if running else 0)


def _bg(url, max_pages, config):
    try: run_scan(url, max_pages=max_pages, config_path=config or None, store=store, log=lambda s: None)
    except Exception: pass


@app.post("/scan")
def start(url: str = Form(...), max_pages: int = Form(60), config: str = Form("")):
    threading.Thread(target=_bg, args=(url, max_pages, config.strip()), daemon=True).start()
    return RedirectResponse("/", status_code=303)


@app.get("/scan/{scan_id}", response_class=HTMLResponse)
def report(scan_id: int):
    s = store.scan(scan_id)
    if not s: return HTMLResponse("not found", 404)
    if s["status"] != "done": return RedirectResponse(f"/scan/{scan_id}/log")
    return render_report(s, store.issues(scan_id), shot_prefix=f"/scans/{Path(s['out_dir']).name}/", nav_home="/")


@app.get("/scan/{scan_id}/log", response_class=HTMLResponse)
def log(scan_id: int):
    s = store.scan(scan_id)
    if not s: return HTMLResponse("not found", 404)
    done, total, label = progress_of(s["log"] or "")
    body = f"""<div class="card" style="padding:18px"><h2>Scan #{scan_id} · {esc(s['site'])} · <span class="pill {s['status']}">{s['status']}</span></h2>
{'<div class="trend" style="margin-bottom:10px">' + label + '</div>' if s['status']=='running' else ''}<pre>{esc(s['log'] or 'Starting…')}</pre>
{'<a class="btn" href="/scan/'+str(scan_id)+'">Open report</a>' if s['status']=='done' else '<div class="trend">This page refreshes every 10 seconds.</div>'}</div>"""
    return page(f"Scan #{scan_id}", body, refresh=10 if s["status"] == "running" else 0)


@app.get("/api/scan/{scan_id}")
def api(scan_id: int):
    return JSONResponse({"scan": store.scan(scan_id), "issues": store.issues(scan_id)})


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host=os.environ.get("WEF_HOST", "127.0.0.1"), port=int(os.environ.get("WEF_PORT", "8030")))
