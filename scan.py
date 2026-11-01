"""CLI: python scan.py https://example.com [--max-pages 100] [--config configs/example.com.json] [--no-forms] [--no-buttons] [--headed]"""
import argparse, sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent))
from wef.runner import run_scan

ap = argparse.ArgumentParser(description="Website Error Finder")
ap.add_argument("url")
ap.add_argument("--max-pages", type=int, default=100)
ap.add_argument("--config", default=None, help="site config JSON (locations, phones, emails...)")
ap.add_argument("--no-forms", action="store_true", help="skip safe-mode form tests")
ap.add_argument("--no-buttons", action="store_true", help="skip button click tests")
ap.add_argument("--headed", action="store_true", help="show the browser")
a = ap.parse_args()
r = run_scan(a.url, max_pages=a.max_pages, config_path=a.config, test_forms=not a.no_forms, test_buttons=not a.no_buttons, headless=not a.headed)
s = r["summary"]
print(f"\nScan #{r['scan_id']}: {s['total']} issues  critical={s['severity']['critical']} high={s['severity']['high']} medium={s['severity']['medium']} low={s['severity']['low']}")
print(f"New {s['new']} | Existing {s['existing']} | Fixed {s['fixed']}")
print("Report:", r["report"])
