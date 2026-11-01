"""SQLite store for scans + issues, with new/existing/fixed diff against the previous scan of the same site."""
from __future__ import annotations
import json, sqlite3, datetime as dt
from pathlib import Path

SCHEMA = """
CREATE TABLE IF NOT EXISTS scans (
  id INTEGER PRIMARY KEY AUTOINCREMENT, site TEXT, start_url TEXT, started TEXT, finished TEXT,
  pages INTEGER, status TEXT DEFAULT 'running', out_dir TEXT, summary TEXT, log TEXT DEFAULT ''
);
CREATE TABLE IF NOT EXISTS issues (
  id INTEGER PRIMARY KEY AUTOINCREMENT, scan_id INTEGER, fingerprint TEXT, category TEXT, severity TEXT,
  title TEXT, page TEXT, element TEXT, current TEXT, expected TEXT, impact TEXT, evidence TEXT, fix TEXT, details TEXT,
  state TEXT DEFAULT 'new', first_seen_scan INTEGER
);
CREATE INDEX IF NOT EXISTS idx_issues_scan ON issues(scan_id);
CREATE INDEX IF NOT EXISTS idx_issues_fp ON issues(fingerprint);
"""


class Store:
    def __init__(self, path: str | Path):
        self.path = str(path)
        self.conn = sqlite3.connect(self.path, check_same_thread=False)
        self.conn.row_factory = sqlite3.Row
        self.conn.executescript(SCHEMA)

    def start_scan(self, site: str, start_url: str, out_dir: str) -> int:
        cur = self.conn.execute("INSERT INTO scans(site,start_url,started,out_dir) VALUES(?,?,?,?)", (site, start_url, dt.datetime.now().isoformat(timespec="seconds"), out_dir))
        self.conn.commit(); return cur.lastrowid

    def log(self, scan_id: int, line: str):
        self.conn.execute("UPDATE scans SET log = log || ? WHERE id=?", (line + "\n", scan_id)); self.conn.commit()

    def fail_scan(self, scan_id: int, err: str):
        self.conn.execute("UPDATE scans SET status='failed', finished=?, summary=? WHERE id=?", (dt.datetime.now().isoformat(timespec="seconds"), json.dumps({"error": err}), scan_id)); self.conn.commit()

    def previous_scan(self, site: str, before_id: int):
        return self.conn.execute("SELECT * FROM scans WHERE site=? AND id<? AND status='done' ORDER BY id DESC LIMIT 1", (site, before_id)).fetchone()

    def finish_scan(self, scan_id: int, site: str, issues: list, pages: int) -> dict:
        prev = self.previous_scan(site, scan_id)
        prev_fps = {}
        if prev:
            for r in self.conn.execute("SELECT fingerprint, first_seen_scan FROM issues WHERE scan_id=? AND state!='fixed'", (prev["id"],)):
                prev_fps[r["fingerprint"]] = r["first_seen_scan"] or prev["id"]
        cur_fps = set()
        rows = []
        for i in issues:
            d = i.to_dict(); fp = d["fingerprint"]; cur_fps.add(fp)
            state = "existing" if fp in prev_fps else "new"
            rows.append((scan_id, fp, d["category"], d["severity"], d["title"], d["page"], d["element"], d["current"], d["expected"], d["impact"], d["evidence"], d["fix"], json.dumps(d["details"], ensure_ascii=False), state, prev_fps.get(fp, scan_id)))
        fixed = 0
        if prev:
            for r in self.conn.execute("SELECT * FROM issues WHERE scan_id=? AND state!='fixed'", (prev["id"],)):
                if r["fingerprint"] not in cur_fps:
                    fixed += 1
                    rows.append((scan_id, r["fingerprint"], r["category"], r["severity"], r["title"], r["page"], r["element"], r["current"], r["expected"], r["impact"], r["evidence"], r["fix"], r["details"], "fixed", r["first_seen_scan"]))
        self.conn.executemany("INSERT INTO issues(scan_id,fingerprint,category,severity,title,page,element,current,expected,impact,evidence,fix,details,state,first_seen_scan) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)", rows)
        summary = self.summarize(scan_id, issues, fixed, prev["id"] if prev else None)
        self.conn.execute("UPDATE scans SET status='done', finished=?, pages=?, summary=? WHERE id=?", (dt.datetime.now().isoformat(timespec="seconds"), pages, json.dumps(summary), scan_id))
        self.conn.commit(); return summary

    def summarize(self, scan_id, issues, fixed, prev_id):
        sev = {"critical": 0, "high": 0, "medium": 0, "low": 0}; cat = {}
        new = existing = 0
        for r in self.conn.execute("SELECT severity, category, state FROM issues WHERE scan_id=?", (scan_id,)):
            if r["state"] == "fixed": continue
            sev[r["severity"]] = sev.get(r["severity"], 0) + 1; cat[r["category"]] = cat.get(r["category"], 0) + 1
            if r["state"] == "new": new += 1
            else: existing += 1
        return {"total": sum(sev.values()), "severity": sev, "category": cat, "new": new, "existing": existing, "fixed": fixed, "prev_scan": prev_id}

    def scans(self, site: str | None = None):
        q = "SELECT * FROM scans" + (" WHERE site=?" if site else "") + " ORDER BY id DESC"
        return [dict(r) for r in self.conn.execute(q, (site,) if site else ())]

    def scan(self, scan_id: int):
        r = self.conn.execute("SELECT * FROM scans WHERE id=?", (scan_id,)).fetchone(); return dict(r) if r else None

    def issues(self, scan_id: int):
        order = "CASE severity WHEN 'critical' THEN 0 WHEN 'high' THEN 1 WHEN 'medium' THEN 2 ELSE 3 END"
        return [dict(r) for r in self.conn.execute(f"SELECT * FROM issues WHERE scan_id=? ORDER BY CASE state WHEN 'fixed' THEN 1 ELSE 0 END, {order}, category, page", (scan_id,))]

    def sites(self):
        return [dict(r) for r in self.conn.execute("SELECT site, COUNT(*) n, MAX(started) last, MAX(id) last_id FROM scans GROUP BY site ORDER BY last DESC")]
