"""Optional per-site ground truth. Without it the tool flags inconsistencies; with it, wrongness."""
from __future__ import annotations
import json, re
from pathlib import Path


def norm_phone(p: str) -> str:
    d = re.sub(r"\D", "", p or "")
    if len(d) == 11 and d.startswith("1"):
        d = d[1:]
    return d


class SiteConfig:
    def __init__(self, data: dict | None = None):
        self.data = data or {}
        self.locations = self.data.get("locations", [])
        self.phones = {norm_phone(p) for p in self.data.get("phones", [])}
        self.emails = {e.lower() for e in self.data.get("emails", [])}
        self.staging_patterns = self.data.get("staging_patterns", [
            r"staging\.", r"\bstage\.", r"\bdev\.", r"\btest\.", r"localhost", r"127\.0\.0\.1",
            r"\.local\b", r"webflow\.io", r"wixsite\.com", r"\.netlify\.app", r"\.vercel\.app",
            r"\.pages\.dev", r"wpengine\.com", r"\.myshopify\.com", r"\.herokuapp\.com"])
        self.allowed_external = self.data.get("allowed_external_domains", [])
        self.spelling_whitelist = {w.lower() for w in self.data.get("spelling_whitelist", [])}
        self.max_pages = self.data.get("max_pages")
        self.exclude = self.data.get("exclude_patterns", [])
        # hosts outside the main domain that belong to the site (booking/store platforms such as ROLLER)
        self.extra_hosts = [h.lower() for h in self.data.get("extra_hosts", [])]
        # always-follow hosts: external CTA destinations are crawled one level deep (capped per host)
        self.follow_external_ctas = self.data.get("follow_external_ctas", True)
        self.external_page_cap = int(self.data.get("external_page_cap", 8))
        self.alert_email = self.data.get("alert_email", "")
        for loc in self.locations:
            loc["_phones"] = {norm_phone(p) for p in loc.get("phones", [])}
            loc["_slugs"] = [s.lower() for s in loc.get("slugs", [loc["name"].lower().replace(" ", "-")])]
            self.phones |= loc["_phones"]
            self.emails |= {e.lower() for e in loc.get("emails", [])}

    @classmethod
    def load(cls, path: str | None):
        if not path:
            return cls()
        return cls(json.loads(Path(path).read_text("utf-8")))

    def location_for(self, url_or_text: str):
        """Return location name whose slug/name appears in the string."""
        s = (url_or_text or "").lower()
        hits = []
        for loc in self.locations:
            for slug in loc["_slugs"] + [loc["name"].lower()]:
                if slug and slug in s:
                    hits.append(loc["name"])
                    break
        return hits

    def is_staging(self, url: str) -> bool:
        return any(re.search(p, url or "", re.I) for p in self.staging_patterns)
