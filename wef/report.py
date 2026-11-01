"""Self-contained HTML report: sidebar filters, issues grouped by category, severity-coded cards, inline evidence."""
from __future__ import annotations
import html, json, re
from urllib.parse import urlparse
from .models import SEV_ICON, CATEGORIES, SEVERITIES

CAT_LABEL = {"functionality": "Broken Functionality", "cta": "CTA & Wrong Links", "forms": "Forms", "responsive": "Responsive", "content": "Content",
             "consistency": "Consistency", "navigation": "Navigation", "visual": "Visual", "tracking": "Tracking", "seo": "SEO", "performance": "Performance"}
CAT_ICON = {"functionality": "⚠", "cta": "➜", "forms": "✎", "responsive": "▭", "content": "¶", "consistency": "≠", "navigation": "☰", "visual": "◫", "tracking": "◉", "seo": "⌕", "performance": "⏱"}
CAT_BLURB = {"functionality": "Things that are broken: pages, links, images, videos, scripts, buttons.",
             "cta": "Buttons and links that go to the wrong place, nowhere, or an untracked destination.",
             "forms": "Forms that do not submit, validate wrongly, or post to the wrong endpoint.",
             "responsive": "Layout problems on tablet and mobile.", "content": "Outdated, placeholder, misspelled or contradictory text.",
             "consistency": "Phone numbers, emails, addresses and hours that differ across pages.", "navigation": "Menus, logo, breadcrumbs and anchors.",
             "visual": "Overlaps, empty sections, distorted images, stuck modals on desktop.", "tracking": "GA4 / GTM / pixels / UTMs / third-party widgets.",
             "seo": "Titles, headings, canonicals, sitemap, schema.", "performance": "Slow pages, heavy files, Core Web Vitals."}
SEV_WEIGHT = {"critical": 10, "high": 4, "medium": 1, "low": 0.25}

CSS = """
:root{--bg:#f6f7f9;--panel:#fff;--ink:#0f172a;--muted:#64748b;--line:#e2e8f0;--soft:#f1f5f9;--crit:#dc2626;--high:#ea580c;--med:#d97706;--low:#2563eb;--ok:#16a34a;--r:12px}
*{box-sizing:border-box}html{scroll-behavior:smooth}body{margin:0;font:14px/1.5 Inter,-apple-system,"Segoe UI",Roboto,sans-serif;background:var(--bg);color:var(--ink)}
a{color:#2563eb;text-decoration:none}a:hover{text-decoration:underline}code{font:12.5px/1.5 ui-monospace,SFMono-Regular,Menlo,Consolas,monospace}
.hdr{background:#0f172a;color:#fff;padding:0 24px;height:52px;display:flex;align-items:center;gap:16px;position:sticky;top:0;z-index:20}
.hdr .brand{font-weight:700;letter-spacing:.2px}.hdr .site{color:#94a3b8}.hdr a{color:#cbd5e1}.hdr .sp{flex:1}
.layout{display:grid;grid-template-columns:260px 1fr;gap:20px;max-width:1360px;margin:0 auto;padding:20px}
.side{position:sticky;top:68px;align-self:start;max-height:calc(100vh - 84px);overflow:auto}
.card{background:var(--panel);border:1px solid var(--line);border-radius:var(--r)}
.side .card{padding:14px;margin-bottom:12px}.side h4{margin:0 0 8px;font-size:11px;text-transform:uppercase;letter-spacing:.6px;color:var(--muted)}
.f{display:flex;align-items:center;gap:8px;padding:6px 8px;border-radius:8px;cursor:pointer;user-select:none;font-size:13px}.f:hover{background:var(--soft)}.f.on{background:#0f172a;color:#fff}
.f .n{margin-left:auto;font-variant-numeric:tabular-nums;color:var(--muted);font-size:12px}.f.on .n{color:#cbd5e1}
.dot{width:9px;height:9px;border-radius:50%;flex:none}.dot.critical{background:var(--crit)}.dot.high{background:var(--high)}.dot.medium{background:var(--med)}.dot.low{background:var(--low)}
.hero{padding:20px 22px;margin-bottom:16px;display:grid;grid-template-columns:auto 1fr;gap:22px;align-items:center}
.score{width:96px;height:96px;border-radius:50%;display:grid;place-items:center;font-size:30px;font-weight:800;color:#fff}
.hero h1{margin:0 0 2px;font-size:20px}.hero .meta{color:var(--muted);font-size:13px}
.stats{display:flex;gap:10px;flex-wrap:wrap;margin-top:12px}.stat{border:1px solid var(--line);border-radius:10px;padding:8px 12px;min-width:92px;background:#fff}
.stat b{display:block;font-size:20px;line-height:1.1}.stat span{font-size:11.5px;color:var(--muted)}.stat.critical b{color:var(--crit)}.stat.high b{color:var(--high)}.stat.medium b{color:var(--med)}.stat.low b{color:var(--low)}.stat.new b{color:var(--ok)}
.tools{display:flex;gap:10px;align-items:center;margin-bottom:14px;flex-wrap:wrap}.q{flex:1;min-width:240px;padding:10px 12px;border:1px solid var(--line);border-radius:10px;font:inherit;background:#fff}
.tog{display:flex;border:1px solid var(--line);border-radius:10px;overflow:hidden;background:#fff}.tog span{padding:8px 12px;cursor:pointer;font-size:13px}.tog span.on{background:#0f172a;color:#fff}
.count{color:var(--muted);font-size:13px}
.sec{margin:22px 0 10px;display:flex;align-items:baseline;gap:10px}.sec h2{margin:0;font-size:16px}.sec .ic{width:26px;height:26px;border-radius:8px;background:#0f172a;color:#fff;display:grid;place-items:center;font-size:13px}
.sec .b{color:var(--muted);font-size:12.5px}.sec .n{margin-left:auto;color:var(--muted);font-size:12.5px}
.issue{border:1px solid var(--line);border-radius:var(--r);background:#fff;margin-bottom:8px;overflow:hidden}
.issue summary{display:grid;grid-template-columns:6px 1fr;cursor:pointer;list-style:none}.issue summary::-webkit-details-marker{display:none}.issue summary:hover .bd{background:#fafbfc}
.issue .stripe{background:var(--low)}.issue.critical .stripe{background:var(--crit)}.issue.high .stripe{background:var(--high)}.issue.medium .stripe{background:var(--med)}
.issue.fixed{opacity:.5}.issue .bd{padding:12px 14px}.issue .more{padding-top:0;padding-left:20px}.chev{margin-left:auto;color:var(--muted);transition:transform .15s}.issue[open] .chev{transform:rotate(180deg)}
.short{color:var(--muted);font-size:12.5px;font-family:ui-monospace,Menlo,Consolas,monospace;white-space:nowrap;overflow:hidden;text-overflow:ellipsis}.issue[open] .short{display:none}
.start{padding:16px 18px;margin-bottom:16px;border-left:5px solid var(--crit)}.start h3{margin:0 0 4px;font-size:15px}.start p{margin:0 0 10px;color:var(--muted);font-size:13px}
.start ol{margin:0;padding-left:20px}.start li{margin:4px 0}.start li a{color:var(--ink);font-weight:600}.start li .w{color:var(--muted);font-weight:400}
.legend{display:flex;gap:16px;flex-wrap:wrap;color:var(--muted);font-size:12.5px;margin:0 0 14px}.legend b{color:var(--ink)}
.tools .x{font-size:12.5px;color:var(--muted);cursor:pointer}
.row1{display:flex;gap:8px;align-items:center;flex-wrap:wrap}.row1 .t{font-weight:650;font-size:14.5px}
.tag{font-size:10.5px;padding:2px 7px;border-radius:999px;background:var(--soft);color:#334155;text-transform:uppercase;letter-spacing:.4px;font-weight:600}
.tag.critical{background:#fee2e2;color:#991b1b}.tag.high{background:#ffedd5;color:#9a3412}.tag.medium{background:#fef3c7;color:#92400e}.tag.low{background:#dbeafe;color:#1e40af}.tag.new{background:#dcfce7;color:#166534}.tag.fixed{background:#e2e8f0;color:#475569}
.pg{margin:6px 0 8px;font-size:12.5px}.pg a{color:#0f172a;font-weight:500}.pg .el{color:var(--muted)}.pg .loc{margin-left:10px;font-size:12px;color:#fff;background:#2563eb;padding:2px 9px;border-radius:999px;font-weight:600}.pg .loc:hover{background:#1d4ed8;text-decoration:none}.pl summary{display:inline;font-size:11px;color:var(--muted);cursor:pointer;margin-left:6px}
.grid{display:grid;grid-template-columns:1fr 1fr;gap:8px 14px}.cell{background:var(--soft);border-radius:8px;padding:8px 10px}.cell label{display:block;font-size:10.5px;text-transform:uppercase;letter-spacing:.5px;color:var(--muted);margin-bottom:2px}
.cell.fix{grid-column:1/-1;background:#ecfdf5}.cell code{word-break:break-all;color:#0f172a}
.ev{margin-top:8px}.ev summary{cursor:pointer;font-size:12px;color:var(--muted)}.ev img{max-width:100%;border:1px solid var(--line);border-radius:8px;margin-top:6px}
.empty{padding:50px;text-align:center;color:var(--muted)}
@media(max-width:900px){.layout{grid-template-columns:1fr}.side{position:static;max-height:none}.grid{grid-template-columns:1fr}.hero{grid-template-columns:1fr}}
"""

JS = """
const S={sev:new Set(),cat:new Set(),st:new Set(['new','existing']),q:'',group:'cat'};
const all=[...document.querySelectorAll('.issue')];
function match(el){const q=S.q;return (!S.sev.size||S.sev.has(el.dataset.sev))&&(!S.cat.size||S.cat.has(el.dataset.cat))&&(!S.st.size||S.st.has(el.dataset.state))&&(!q||el.textContent.toLowerCase().includes(q));}
function render(){const list=document.getElementById('list');list.innerHTML='';
 const vis=all.filter(match);const n=vis.length;
 const key=S.group==='cat'?e=>e.dataset.cat:e=>e.dataset.page;const order=S.group==='cat'?CATS:[...new Set(vis.map(e=>e.dataset.page))].sort();
 for(const k of order){const items=vis.filter(e=>key(e)===k);if(!items.length)continue;const h=document.createElement('div');h.className='sec';
  h.innerHTML=S.group==='cat'?`<span class="ic">${ICON[k]||'•'}</span><h2>${LABEL[k]||k}</h2><span class="b">${BLURB[k]||''}</span><span class="n">${items.length}</span>`:`<h2><code>${k}</code></h2><span class="n">${items.length}</span>`;
  list.appendChild(h);items.forEach(i=>list.appendChild(i));}
 document.getElementById('count').textContent=n;document.getElementById('empty').style.display=n?'none':'';
 document.querySelectorAll('[data-fcat]').forEach(f=>{const c=f.dataset.fcat;f.querySelector('.n').textContent=all.filter(e=>e.dataset.cat===c&&(!S.sev.size||S.sev.has(e.dataset.sev))&&(!S.st.size||S.st.has(e.dataset.state))).length;});}
function tg(set,v,el){set.has(v)?set.delete(v):set.add(v);el.classList.toggle('on');render();}
document.querySelectorAll('[data-fsev]').forEach(el=>el.onclick=()=>tg(S.sev,el.dataset.fsev,el));
document.querySelectorAll('[data-fcat]').forEach(el=>el.onclick=()=>tg(S.cat,el.dataset.fcat,el));
document.querySelectorAll('[data-fst]').forEach(el=>el.onclick=()=>tg(S.st,el.dataset.fst,el));
document.querySelectorAll('[data-g]').forEach(el=>el.onclick=()=>{S.group=el.dataset.g;document.querySelectorAll('[data-g]').forEach(x=>x.classList.toggle('on',x===el));render();});
document.getElementById('q').oninput=e=>{S.q=e.target.value.toLowerCase();render();};
document.getElementById('clear').onclick=()=>{S.sev.clear();S.cat.clear();S.st=new Set(['new','existing']);S.q='';document.getElementById('q').value='';document.querySelectorAll('.f').forEach(f=>f.classList.toggle('on',f.dataset.fst==='new'||f.dataset.fst==='existing'));render();};
document.querySelectorAll('[data-open]').forEach(a=>a.onclick=()=>{const d=document.getElementById(a.dataset.open);if(d){d.open=true;d.style.display='';}});
document.getElementById('expand').onclick=()=>all.forEach(e=>{if(e.style.display!=='none')e.open=true;});document.getElementById('collapse').onclick=()=>all.forEach(e=>e.open=false);
render();
"""

URL_RE = re.compile(r"https?://[^\s\"'<>|)]+")


def esc(s): return html.escape(str(s) if s is not None else "")


def linkify(text: str) -> str:
    out, last = [], 0
    for m in URL_RE.finditer(text or ""):
        out.append(esc(text[last:m.start()])); u = m.group(0).rstrip(".,;")
        out.append(f'<a href="{esc(u)}" target="_blank" rel="noopener">{esc(u)}</a>'); last = m.start() + len(u)
    out.append(esc(text[last:])); return "".join(out)


def page_link(page: str, base: str) -> str:
    if not page: return ""
    if page.startswith("http"): return f'<a href="{esc(page)}" target="_blank" rel="noopener">{esc(page)}</a>'
    if page.startswith("site-wide"): return esc(page)
    full = base.rstrip("/") + page
    return f'<a href="{esc(full)}" target="_blank" rel="noopener">{esc(full)}</a>'


def locate_link(page: str, element: str, base: str) -> str:
    """Link that opens the page scrolled to the element text (Chrome/Edge text fragment)."""
    if not page or page.startswith("site-wide"): return ""
    m = re.search(r'"([^"]{3,80})"', element or "")
    if not m: return ""
    text = m.group(1).strip()
    if text.startswith("[img:") or len(text) < 3: return ""
    from urllib.parse import quote
    full = page if page.startswith("http") else base.rstrip("/") + page
    return f'<a class="loc" href="{esc(full.split("#")[0])}#:~:text={quote(text)}" target="_blank" rel="noopener" title="Open the page scrolled to this element">Locate on page ↗</a>'


def health_score(sev: dict, pages: int) -> int:
    pts = sum(SEV_WEIGHT[s] * sev.get(s, 0) for s in SEVERITIES)
    return max(0, min(100, round(100 - (pts / max(pages, 1)) * 2)))


def render_report(scan: dict, issues: list[dict], shot_prefix: str = "", nav_home: str = "") -> str:
    summ = json.loads(scan.get("summary") or "{}")
    sev = summ.get("severity", {}); cat = summ.get("category", {})
    su = urlparse(scan.get("start_url") or ("https://" + scan.get("site", ""))); base = f"{su.scheme}://{su.netloc}"
    score = health_score(sev, scan.get("pages") or 1)
    sc_col = "#16a34a" if score >= 80 else "#d97706" if score >= 50 else "#dc2626"
    sev_f = "".join(f'<div class="f" data-fsev="{s}"><span class="dot {s}"></span>{s.title()}<span class="n">{sev.get(s,0)}</span></div>' for s in SEVERITIES)
    cat_f = "".join(f'<div class="f" data-fcat="{c}"><span>{CAT_ICON[c]}</span>{CAT_LABEL[c]}<span class="n">{cat.get(c,0)}</span></div>' for c in CATEGORIES if cat.get(c, 0))
    st_f = f'<div class="f on" data-fst="new">New<span class="n">{summ.get("new",0)}</span></div><div class="f on" data-fst="existing">Existing<span class="n">{summ.get("existing",0)}</span></div><div class="f" data-fst="fixed">Fixed since last scan<span class="n">{summ.get("fixed",0)}</span></div>'
    stats = "".join(f'<div class="stat {s}"><b>{sev.get(s,0)}</b><span>{SEV_ICON[s]} {s.title()}</span></div>' for s in SEVERITIES)
    stats += f'<div class="stat new"><b>{summ.get("new",0)}</b><span>New</span></div><div class="stat"><b>{summ.get("existing",0)}</b><span>Existing</span></div><div class="stat"><b>{summ.get("fixed",0)}</b><span>Fixed</span></div>'
    cards = []
    for n, i in enumerate(issues):
        ev = i.get("evidence") or ""; evh = ""
        if ev:
            src = ev if ev.startswith("http") else shot_prefix + ev
            evh = f'<details class="ev"><summary>Screenshot evidence</summary><a href="{esc(src)}" target="_blank"><img loading="lazy" src="{esc(src)}"></a></details>'
        pages_in = []
        try: pages_in = json.loads(i.get("details") or "{}").get("pages") or []
        except Exception: pass
        more = (f'<details class="pl"><summary>all {len(pages_in)} pages</summary><div style="margin-top:4px">' + "<br>".join(page_link(x, base) for x in pages_in) + "</div></details>") if pages_in else ""
        el = f' <span class="el">· {esc(i["element"])}</span>' if i.get("element") else ""
        cells = "".join(f'<div class="cell"><label>{lab}</label><code>{linkify(v)}</code></div>' for lab, v in (("What we found", i["current"]), ("What it should be", i["expected"]), ("Impact", i["impact"])) if v)
        fix = f'<div class="cell fix"><label>Recommended fix</label><code>{linkify(i["fix"])}</code></div>' if i.get("fix") else ""
        short = esc((i.get("current") or "")[:110])
        cards.append(f'''<details class="issue {i["severity"]} {i["state"]}" data-sev="{i["severity"]}" data-cat="{i["category"]}" data-state="{i["state"]}" data-page="{esc(i["page"])}" data-i="{n}" id="i{n}"><summary><div class="stripe"></div><div class="bd">
<div class="row1"><span class="t">{esc(i["title"])}</span><span class="tag {i["severity"]}">{i["severity"]}</span><span class="tag">{CAT_LABEL.get(i["category"], i["category"])}</span><span class="tag {i["state"]}">{i["state"]}</span><span class="chev">▾</span></div>
<div class="pg">{page_link(i["page"], base)}{more}{el} {locate_link(i["page"], i.get("element", ""), base)}</div><div class="short">{short}</div></div></summary>
<div class="bd more"><div class="grid">{cells}{fix}</div>{evh}</div></details>''')
    prev = f' · vs scan #{summ["prev_scan"]}' if summ.get("prev_scan") else ""
    rank = {sv: k for k, sv in enumerate(SEVERITIES)}
    live = [(n, i) for n, i in enumerate(issues) if i["state"] != "fixed"]
    top = sorted(live, key=lambda t: (rank.get(t[1]["severity"], 9), 0 if t[1]["state"] == "new" else 1, t[0]))[:6]
    start = ""
    if top:
        items = "".join(f'<li><a href="#i{n}" data-open="i{n}">{esc(i["title"])}</a> <span class="w">— {esc(i["page"] if len(i["page"]) < 70 else i["page"][:67] + "…")}</span></li>' for n, i in top)
        start = f'<div class="card start"><h3>Start here</h3><p>The {len(top)} most important things to fix, most urgent first.</p><ol>{items}</ol></div>' 
    consts = f"const CATS={json.dumps(CATEGORIES)};const LABEL={json.dumps(CAT_LABEL)};const ICON={json.dumps(CAT_ICON)};const BLURB={json.dumps(CAT_BLURB)};"
    home = f'<a href="{esc(nav_home)}">← All scans</a>' if nav_home else ""
    return f"""<!doctype html><html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>{esc(scan.get('site'))} · Website Error Report</title><style>{CSS}</style></head><body>
<div class="hdr"><span class="brand">Website Error Finder</span><span class="site">{esc(scan.get('site'))}</span><span class="sp"></span>{home}</div>
<div class="layout"><aside class="side">
<div class="card"><h4>Severity</h4>{sev_f}</div><div class="card"><h4>Category</h4>{cat_f}</div><div class="card"><h4>Status</h4>{st_f}</div>
<div class="card" style="text-align:center"><a id="clear" href="javascript:void(0)">Reset filters</a></div></aside>
<main>
<div class="card hero"><div class="score" style="background:{sc_col}">{score}</div><div><h1>Is anything wrong with {esc(scan.get('site'))}?</h1><div class="meta">Health score out of 100 · Scan #{scan.get('id')} · {esc((scan.get('started') or '')[:16].replace('T',' '))} · {scan.get('pages') or 0} pages · {summ.get('total',0)} open issues{prev}</div><div class="stats">{stats}</div></div></div>
{start}
<div class="legend"><b>What the colours mean:</b><span><b>Critical</b> — losing customers or traffic right now</span><span><b>High</b> — visibly broken for users</span><span><b>Medium</b> — quality and trust</span><span><b>Low</b> — housekeeping</span></div>
<div class="tools"><input class="q" id="q" placeholder="Search issues, pages, URLs…"><span class="x" id="expand">Expand all</span><span class="x" id="collapse">Collapse all</span><div class="tog"><span class="on" data-g="cat">By category</span><span data-g="page">By page</span></div><span class="count">Showing <b id="count">0</b></span></div>
<div id="list">{"".join(cards)}</div><div class="empty" id="empty">Nothing matches these filters.</div>
</main></div><script>{consts}{JS}</script></body></html>"""
