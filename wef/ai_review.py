"""AI judgement layer.

Free mode (default): builds a compact review pack (CTAs with text/page/destination title, location mentions,
per-page content snippets) and writes it to <scan>/ai_review_pack.md so it can be pasted into a Claude Code session.
API mode: if ANTHROPIC_API_KEY is set, sends the pack to Claude and merges the returned issues into the scan.

Usage: python -m wef.ai_review <scan_id> [--api]
"""
from __future__ import annotations
import json, os, re, sys
from pathlib import Path
from urllib.parse import urlparse

from .checks import CTA_RE, path_of, host_of
from .models import Issue
from .store import Store
from .runner import DB
from .report import render_report

PROMPT = """You are a website QA reviewer. Below is data crawled from one website. Find errors a crawler cannot:
- CTA text or page context that does not match the destination (e.g. "Book Now" on the Downtown page goes to the Uptown booking)
- Wrong location references, content copied from another location/page, contradictory information between pages
- Suspicious, inconsistent or outdated information (events, offers, hours, prices)
- Links whose text promises one thing and whose destination title/URL says another
Return ONLY a JSON array. Each item: {"title": str, "severity": "critical|high|medium|low", "category": "cta|content|consistency",
"page": "/path", "element": str, "current": str, "expected": str, "impact": str, "fix": str}. Be precise; no speculation without evidence in the data.
"""


def build_pack(crawl: dict, max_pages: int = 60) -> str:
    host = crawl["host"]; pages = crawl["pages"]; ls = crawl["link_status"]
    title_of = {u: p.get("title", "") for u, p in pages.items()}
    out = [f"# Site: {host}\n", "## CTA table (page → link text → destination → destination title)\n", "| Page | Page title | CTA text | Region | Destination | Destination title/status |", "|---|---|---|---|---|---|"]
    seen = set()
    for u, p in list(pages.items())[:max_pages]:
        if p.get("error") or p.get("status", 0) >= 400: continue
        for l in p.get("links", []):
            t = (l.get("text") or "").strip()
            if not t or not CTA_RE.search(t) or not l.get("visible"): continue
            key = (path_of(u), t, l["href"])
            if key in seen: continue
            seen.add(key)
            dest = l["href"]; dt = title_of.get(dest) or title_of.get(dest.split("#")[0]) or (str(ls.get(dest, {}).get("status", "")) if dest in ls else "")
            out.append(f"| {path_of(u)} | {p.get('title','')[:50]} | {t[:40]} | {l['region']} | {dest if host_of(dest)!=host else path_of(dest)} | {str(dt)[:50]} |")
    out.append("\n## Page content (first 900 chars of main text, plus phones/emails found)\n")
    for u, p in list(pages.items())[:max_pages]:
        if p.get("error") or p.get("status", 0) >= 400: continue
        mt = (p.get("perf", {}).get("main_text") or p.get("text") or "")[:900]
        out.append(f"### {path_of(u)} — \"{p.get('title','')}\"\nH1: {' | '.join(h['text'] for h in p.get('headings', []) if h['tag']=='h1')}\nPhones: {', '.join(p.get('phones', [])[:5])}  Emails: {', '.join(p.get('emails', [])[:3])}\n{mt}\n")
    return "\n".join(out)


def run(scan_id: int, use_api: bool = False):
    store = Store(DB); scan = store.scan(scan_id)
    out = Path(scan["out_dir"]); crawl = json.loads((out / "crawl.json").read_text("utf-8"))
    pack = build_pack(crawl)
    (out / "ai_review_pack.md").write_text(PROMPT + "\n\n" + pack, "utf-8")
    print(f"Review pack written: {out / 'ai_review_pack.md'} ({len(pack)//1000} KB)")
    if not use_api:
        print("Paste it into a Claude Code session, save the JSON answer as ai_issues.json in the scan folder, then run: python -m wef.ai_review", scan_id, "--merge")
        return
    key = os.environ.get("ANTHROPIC_API_KEY")
    if not key:
        print("ANTHROPIC_API_KEY not set; free mode only."); return
    import anthropic
    client = anthropic.Anthropic(api_key=key)
    msg = client.messages.create(model="claude-sonnet-5-5", max_tokens=8000, messages=[{"role": "user", "content": PROMPT + "\n\n" + pack}])
    text = "".join(b.text for b in msg.content if getattr(b, "type", "") == "text")
    (out / "ai_issues.json").write_text(text, "utf-8")
    merge(scan_id)


def merge(scan_id: int):
    store = Store(DB); scan = store.scan(scan_id); out = Path(scan["out_dir"])
    raw = (out / "ai_issues.json").read_text("utf-8")
    m = re.search(r"\[.*\]", raw, re.S)
    items = json.loads(m.group(0)) if m else []
    rows = []
    for it in items:
        i = Issue(category=it.get("category", "cta"), severity=it.get("severity", "medium"), title="[AI] " + it.get("title", ""), page=it.get("page", "/"), element=it.get("element", ""), current=it.get("current", ""), expected=it.get("expected", ""), impact=it.get("impact", ""), fix=it.get("fix", ""))
        d = i.to_dict()
        rows.append((scan_id, d["fingerprint"], d["category"], d["severity"], d["title"], d["page"], d["element"], d["current"], d["expected"], d["impact"], "", d["fix"], "{}", "new", scan_id))
    store.conn.execute("DELETE FROM issues WHERE scan_id=? AND title LIKE '[AI] %'", (scan_id,))
    store.conn.executemany("INSERT INTO issues(scan_id,fingerprint,category,severity,title,page,element,current,expected,impact,evidence,fix,details,state,first_seen_scan) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)", rows)
    summ = store.summarize(scan_id, [], json.loads(scan["summary"] or "{}").get("fixed", 0), json.loads(scan["summary"] or "{}").get("prev_scan"))
    store.conn.execute("UPDATE scans SET summary=? WHERE id=?", (json.dumps(summ), scan_id)); store.conn.commit()
    (out / "report.html").write_text(render_report(store.scan(scan_id), store.issues(scan_id)), "utf-8")
    print(f"Merged {len(rows)} AI findings into scan #{scan_id}; report regenerated.")


if __name__ == "__main__":
    sid = int(sys.argv[1])
    if "--merge" in sys.argv: merge(sid)
    else: run(sid, use_api="--api" in sys.argv)
