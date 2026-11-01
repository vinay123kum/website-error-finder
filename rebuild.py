"""Re-run the checks on an existing scan's crawl data (after rule changes): python rebuild.py <scan_id>"""
import json, sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent))
from wef.checks import Checker
from wef.siteconfig import SiteConfig
from wef.store import Store
from wef.runner import DB, ROOT
from wef.report import render_report

sid = int(sys.argv[1]); store = Store(DB); scan = store.scan(sid); out = Path(scan["out_dir"])
crawl = json.loads((out / "crawl.json").read_text("utf-8")); crawl.setdefault("max_pages", scan["pages"] or 100)
cfgp = ROOT / "configs" / f"{scan['site']}.json"
issues = Checker(crawl, SiteConfig.load(str(cfgp)) if cfgp.exists() else SiteConfig()).run()
store.conn.execute("DELETE FROM issues WHERE scan_id=?", (sid,)); store.conn.commit()
summary = store.finish_scan(sid, scan["site"], issues, scan["pages"] or len(crawl["pages"]))
(out / "report.html").write_text(render_report(store.scan(sid), store.issues(sid)), "utf-8")
(out / "issues.json").write_text(json.dumps([i.to_dict() for i in issues], indent=1, ensure_ascii=False), "utf-8")
print(f"Scan #{sid} rebuilt: {summary['total']} issues ({summary['new']} new, {summary['fixed']} fixed)")
