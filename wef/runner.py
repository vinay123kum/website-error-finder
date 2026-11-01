"""Orchestrates: crawl -> check -> store -> report. Used by both CLI and dashboard."""
from __future__ import annotations
import json, re, time, traceback
from pathlib import Path
from .crawler import Crawler, host_key
from .checks import Checker
from .siteconfig import SiteConfig
from .store import Store
from .report import render_report
from .alerts import send_alert

ROOT = Path(__file__).resolve().parent.parent
DATA = ROOT / "data"
DB = DATA / "wef.db"


def run_scan(url: str, max_pages: int = 100, config_path: str | None = None, test_forms: bool = True, test_buttons: bool = True,
             headless: bool = True, log=print, store: Store | None = None) -> dict:
    url = url if url.startswith("http") else "https://" + url
    site = host_key(url)
    cfg = SiteConfig.load(config_path) if config_path else SiteConfig.load(str(ROOT / "configs" / f"{site}.json")) if (ROOT / "configs" / f"{site}.json").exists() else SiteConfig()
    if cfg.max_pages and not max_pages: max_pages = cfg.max_pages
    store = store or Store(DB)
    scan_id = store.start_scan(site, url, "")
    out = DATA / "scans" / f"{scan_id}_{re.sub(r'[^a-z0-9.-]', '_', site)}"
    out.mkdir(parents=True, exist_ok=True)
    store.conn.execute("UPDATE scans SET out_dir=? WHERE id=?", (str(out), scan_id)); store.conn.commit()

    def _log(line):
        log(line); store.log(scan_id, line)

    try:
        t0 = time.time()
        crawler = Crawler(url, cfg, out, max_pages=max_pages, headless=headless, test_forms=test_forms, test_buttons=test_buttons, log=_log)
        crawler.run()
        crawl = crawler.save(out / "crawl.json")
        crawl["max_pages"] = max_pages
        _log(f"Crawled {len(crawler.pages)} pages in {time.time()-t0:.0f}s. Running checks...")
        issues = Checker(crawl, cfg).run()
        summary = store.finish_scan(scan_id, site, issues, len(crawler.pages))
        scan = store.scan(scan_id)
        html = render_report(scan, store.issues(scan_id), shot_prefix="")
        (out / "report.html").write_text(html, "utf-8")
        (out / "issues.json").write_text(json.dumps([i.to_dict() for i in issues], indent=1, ensure_ascii=False), "utf-8")
        _log(f"Done: {summary['total']} issues ({summary['new']} new, {summary['fixed']} fixed). Report: {out / 'report.html'}")
        try:
            send_alert(scan, summary, store.issues(scan_id), extra_to=[cfg.alert_email] if cfg.alert_email else None, log=_log)
        except Exception as e:
            _log(f"Alerts: {e}")
        return {"scan_id": scan_id, "summary": summary, "report": str(out / "report.html"), "out_dir": str(out)}
    except Exception as e:
        store.fail_scan(scan_id, f"{e}\n{traceback.format_exc()}")
        _log(f"FAILED: {e}")
        raise
