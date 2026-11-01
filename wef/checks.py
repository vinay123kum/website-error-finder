"""Rule-based checks. Input: crawl dict (as saved by Crawler.save) + SiteConfig. Output: list[Issue]."""
from __future__ import annotations
import json, re, datetime as dt
from collections import Counter, defaultdict
from urllib.parse import urlparse

from .models import Issue
from .siteconfig import SiteConfig, norm_phone

CTA_RE = re.compile(r"\b(book|buy|sign ?up|get started|contact|call|schedule|reserve|order|register|subscribe|download|try|demo|quote|apply|join|shop|learn more|get a|start|waiver|tickets?|pricing|plan|party|visit|enroll|donate|request)\b", re.I)
CONTACT_RE = re.compile(r"contact|reach|get[- ]in[- ]touch|talk to|enquir|inquir", re.I)
BOOK_RE = re.compile(r"\b(book|reserve|schedule|tickets?|register|waiver|sign ?up|buy|order|checkout)\b", re.I)
PLACEHOLDER_RE = re.compile(r"lorem ipsum|dolor sit amet|consectetur|\bipsum\b|placeholder text|sample text|dummy text|\[insert|\{\{|lorem\b|\btodo\b|\bTBD\b|your text here|add text here|click here to add", re.I)
WEATHER_RE = re.compile(r"(snow|weather|inclement|closed (today|due to)|closure|delayed opening|opening late|closing early|holiday hours|storm)", re.I)
MONTHS = "january|february|march|april|may|june|july|august|september|october|november|december|jan|feb|mar|apr|jun|jul|aug|sept?|oct|nov|dec"
DATE_RE = re.compile(rf"(?:(monday|tuesday|wednesday|thursday|friday|saturday|sunday)[,\s]+)?\b({MONTHS})\.?\s+(\d{{1,2}})(?:st|nd|rd|th)?,?\s*(\d{{4}})?", re.I)
NUM_DATE_RE = re.compile(r"\b(\d{1,2})/(\d{1,2})/(\d{2,4})\b")
EXPIRY_RE = re.compile(r"(valid (through|until|till|thru)|expires?( on)?|offer ends|ends on|through|until|deadline|last day|book by|register by|sale ends|promo(tion)? ends)", re.I)
EVENT_RE = re.compile(r"\b(event|party|camp|workshop|night|session|class|tournament|open house|festival|celebration|join us|save the date|register|tickets|rsvp|happening|this (week|weekend|month)|upcoming)\b", re.I)
ADDRESS_RE = re.compile(r"\b\d{1,6}\s+(?:[A-Z][\w'.-]*\s+){0,4}(?:Street|St|Avenue|Ave|Road|Rd|Boulevard|Blvd|Drive|Dr|Lane|Ln|Way|Pike|Highway|Hwy|Parkway|Pkwy|Court|Ct|Place|Pl|Circle|Cir|Terrace|Ter|Trail|Plaza|Square|Sq|Route|Rte)\b\.?(?:,?\s*(?:Suite|Ste|Unit|#)\s*[\w-]+)?(?:,?\s*[A-Z][a-z]+(?:\s[A-Z][a-z]+)?,?\s*[A-Z]{2}\s*\d{5})?")
HOURS_RE = re.compile(r"\b(mon|tue|wed|thu|fri|sat|sun)[a-z]*\.?\s*(?:-|–|to|through|thru)?\s*(?:(mon|tue|wed|thu|fri|sat|sun)[a-z]*\.?)?\s*[:\-–]?\s*(\d{1,2}(?::\d{2})?\s*(?:am|pm|a\.m\.|p\.m\.))\s*(?:-|–|to)\s*(\d{1,2}(?::\d{2})?\s*(?:am|pm|a\.m\.|p\.m\.))", re.I)
TRACKING_URL_RE = re.compile(r"google-analytics|googletagmanager|doubleclick|facebook\.com/tr|facebook\.net|licdn|bing\.com|clarity\.ms|hotjar|adsrvr|criteo|taboola|outbrain|quantserve|scorecardresearch|tiktok|snapchat|pinterest|udify|analytics", re.I)
IGNORABLE_FAIL = re.compile(r"ERR_BLOCKED_BY_CLIENT|ERR_ABORTED|net::ERR_BLOCKED_BY_ORB|ERR_BLOCKED_BY_RESPONSE", re.I)
TODAY = dt.date.today()


SITE_HOST = ""   # set by Checker; pages on other hosts keep their full URL


def path_of(url: str) -> str:
    p = urlparse(url)
    h = (p.hostname or "").lower(); h = h[4:] if h.startswith("www.") else h
    if SITE_HOST and h and h != SITE_HOST:
        return url.split("#")[0]
    return (p.path or "/") + (("?" + p.query) if p.query else "")


def host_of(url: str) -> str:
    h = (urlparse(url).hostname or "").lower()
    return h[4:] if h.startswith("www.") else h


class Checker:
    def __init__(self, crawl: dict, config: SiteConfig):
        self.c = crawl
        self.cfg = config
        self.pages: dict = crawl["pages"]
        self.ok_pages = {u: p for u, p in self.pages.items() if p["status"] and p["status"] < 400 and not p["error"]}
        self.site_pages = {u: p for u, p in self.ok_pages.items() if not p.get("is_external_landing")}   # own pages only (SEO/tracking/nav)
        self.ext_pages = {u: p for u, p in self.ok_pages.items() if p.get("is_external_landing")}
        self.host = crawl["host"]
        global SITE_HOST; SITE_HOST = self.host
        self.ls = crawl["link_status"]
        self.issues: list[Issue] = []
        self.shot_base = ""

    def add(self, **kw):
        self.issues.append(Issue(**kw))

    def _live_broken(self, url: str) -> bool:
        """Fresh GET to confirm a link is really dead before calling it broken."""
        try:
            import httpx
            r = httpx.get(url, timeout=45, follow_redirects=True, verify=False, headers={"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) Chrome/125.0"})
            return r.status_code >= 400
        except Exception:
            return True

    def link_shot(self, u: str, href: str, p: dict) -> str:
        ev = (self.c.get("link_evidence") or {}).get(u, {})
        return ev.get(href) or ev.get(href.split("#")[0]) or self.shot(p)

    def shot(self, p, vp="desktop"):
        return self.any_shot(p, vp)

    def run(self) -> list[Issue]:
        for name in ("functionality", "cta", "forms", "responsive", "content", "consistency", "navigation", "tracking", "seo", "performance", "popups", "urls", "contact"):
            try:
                getattr(self, "check_" + name)()
            except Exception as e:
                import traceback; traceback.print_exc()
                self.add(category="functionality", severity="low", title=f"Internal: {name} checker crashed", page=self.c["start_url"], current=str(e)[:200], impact="Audit coverage")
        # every finding carries a screenshot: fall back to the page capture (mobile/tablet for responsive findings)
        by_path = {}
        for u, p in self.pages.items():
            if p.get("screenshots"): by_path[path_of(u)] = p; by_path[u] = p
        for i in self.issues:
            if i.evidence: continue
            p = by_path.get(i.page) or by_path.get((i.details or {}).get("pages", [""])[0] if i.details else "")
            if not p and i.page.startswith("site-wide"):
                for cand in (i.details or {}).get("pages", []):
                    if cand in by_path: p = by_path[cand]; break
            if not p: continue
            vp = "mobile" if "[mobile]" in i.title else "tablet" if "[tablet]" in i.title else "mobile" if i.category == "responsive" else "desktop"
            i.evidence = self.any_shot(p, vp)
        # de-dup identical fingerprints, then collapse findings repeated on 3+ pages into one site-wide issue
        seen = set(); uniq = []
        for i in self.issues:
            if i.fingerprint in seen: continue
            seen.add(i.fingerprint); uniq.append(i)
        groups = defaultdict(list)
        for i in uniq:
            groups[(i.category, i.title, i.element, i.current)].append(i)
        out = []
        for key, items in groups.items():
            pages = sorted({i.page for i in items})
            if len(pages) >= 3 and not key[1].startswith(("Missing page title", "Missing meta description", "Missing H1", "Duplicate")):
                first = items[0]
                first.details = dict(first.details or {}, pages=pages[:50], page_count=len(pages))
                first.page = f"site-wide ({len(pages)} pages, e.g. {pages[0]})"
                out.append(first)
            else:
                out.extend(items)
        for i in out:
            if not i.evidence and i.details.get("pages"):
                for cand in i.details["pages"]:
                    p = by_path.get(cand)
                    if p: i.evidence = self.any_shot(p, "desktop"); break
        return out

    @staticmethod
    def any_shot(p: dict, vp: str = "desktop") -> str:
        sh = p.get("screenshots") or {}
        return sh.get(vp) or sh.get("desktop") or sh.get("tablet") or sh.get("mobile") or ""

    # ======================= 1. Broken functionality =======================
    def check_functionality(self):
        for u, p in self.pages.items():
            pth = path_of(u)
            if p["error"] and re.search(r"Timeout|TIMED_OUT", p["error"]):
                self.add(category="performance", severity="high", title="Page timed out (did not load within 30-60 s, even on retry)", page=pth, current=p["error"], expected="Page loads within a few seconds", impact="Visitors give up; may also be a hosting outage", fix="Check hosting/server health and page weight.", details={"found_on": p["found_on"]})
                continue
            if p["error"] and not p["error"].startswith("non-html"):
                self.add(category="functionality", severity="critical", title="Page fails to load", page=pth, current=p["error"], expected="Page loads with HTTP 200",
                         impact="Users and search engines cannot access this page", fix="Check server/DNS/SSL and that the URL exists.", details={"found_on": p["found_on"]})
                continue
            if p["status"] >= 500:
                self.add(category="functionality", severity="critical", title=f"Server error {p['status']}", page=pth, current=str(p["status"]), expected="200", impact="Page is down", fix="Check server logs for this URL.", details={"found_on": p["found_on"]})
            elif p["status"] >= 400:
                self.add(category="functionality", severity="high", title=f"Broken page ({p['status']})", page=pth, current=str(p["status"]), expected="200",
                         impact="Linked page does not exist", fix="Restore the page or fix/remove links pointing to it.", details={"found_on": p["found_on"], "linked_from": path_of(p["found_on"]) if p["found_on"] else ""})
            if len(p.get("redirect_chain", [])) > 3:
                self.add(category="functionality", severity="medium", title="Redirect chain", page=pth, current=" → ".join(path_of(x) for x in p["redirect_chain"]), expected="Single redirect or none", impact="Slower loads, wasted crawl budget", fix="Point links directly at the final URL.")
            if p.get("final_url") and host_of(p["final_url"]) != self.host and not p.get("is_external_landing") and not host_of(p["final_url"]).endswith("." + self.host):
                self.add(category="functionality", severity="high", title="Page redirects to a different website", page=pth, current=p["final_url"], expected="Stays on " + self.host, impact="Users leave the site unexpectedly", fix="Verify this redirect is intentional.")
            # unexpected navigation after load (popup/auto redirect)
            for n in p.get("unexpected_navigations", [])[:2]:
                self.add(category="functionality", severity="high", title="Page redirects itself after loading", page=pth, current=n["url"], expected="No automatic navigation", impact="Popup/redirect script sends users elsewhere", fix="Find the script or meta-refresh that triggers this navigation.")
            # console errors
            for ce in p.get("console_errors", []):
                t = ce["text"]
                if ce["type"] == "pageerror":
                    self.add(category="functionality", severity="high", title="JavaScript error", page=pth, element="window", current=t[:200], expected="No uncaught JS errors", impact="Interactive components may silently fail", fix="Open DevTools console on this page and fix the thrown error.", evidence=self.shot(p))
                elif "Failed to load resource" in t:
                    continue  # covered by bad_responses
                elif t.startswith("Refused to"):
                    um = re.search(r"from '([^']+)'", t)
                    if um and any(um.group(1) == br["url"] for br in p.get("bad_responses", [])):
                        continue  # the 404 is already reported
                    self.add(category="functionality", severity="high", title="Asset served with wrong MIME type", page=pth, current=t[:200], expected="Correct Content-Type", impact="Script/stylesheet is ignored by the browser", fix="Fix the server content-type or the file path.")
                else:
                    self.add(category="functionality", severity="medium", title="Console error", page=pth, current=t[:200], expected="Clean console", impact="Something on the page is misconfigured", fix="Inspect the console message on this page.")
            # failed / 4xx resources
            for br in p.get("bad_responses", []):
                if TRACKING_URL_RE.search(br["url"]) and br["type"] in ("xhr", "fetch", "ping", "image", "script"):
                    kind = "Tracking/third-party request failing"; sev = "low"
                else:
                    kind = {"image": "Broken image (HTTP error)", "script": "Missing script file", "stylesheet": "Missing stylesheet", "font": "Missing font file", "media": "Broken media file", "document": "Broken iframe/document", "xhr": "Failed API/XHR request", "fetch": "Failed API/fetch request"}.get(br["type"], "Missing asset")
                    sev = "high" if br["type"] in ("script", "stylesheet", "document") else "medium"
                    if br["type"] == "image": sev = "high"
                self.add(category="functionality", severity=sev, title=kind, page=pth, element=br["type"], current=f"{br['status']} {br['url']}", expected="200", impact="Page renders without this asset", fix="Fix the path or restore the file.", details={"status": br["status"]})
            for fr in p.get("failed_requests", []):
                if IGNORABLE_FAIL.search(fr["error"]) : continue
                if TRACKING_URL_RE.search(fr["url"]): continue
                self.add(category="functionality", severity="medium", title="Network request failed", page=pth, element=fr["type"], current=f"{fr['url']} ({fr['error']})", expected="Request completes", impact="Asset or data did not load", fix="Check the URL, CORS and DNS for this resource.")
            # images
            for im in p.get("images", []):
                if im["complete"] and im["natural_w"] == 0 and im["src"] and not im["src"].startswith("data:"):
                    self.add(category="functionality", severity="high", title="Broken image", page=pth, element=im["sel"], current=im["src"], expected="Image renders", impact="Visible broken image", fix="Replace the image source.", evidence=self.shot(p))
                elif im["src"] in self.ls and self.ls[im["src"]]["status"] >= 400:
                    self.add(category="functionality", severity="high", title="Broken image", page=pth, element=im["sel"], current=f"{self.ls[im['src']]['status']} {im['src']}", expected="200", impact="Visible broken image", fix="Replace the image source.")
                elif not im["src"]:
                    self.add(category="functionality", severity="medium", title="Image with empty src", page=pth, element=im["sel"], current="", expected="Image URL", impact="Missing image", fix="Set the src attribute or remove the tag.")
            for m in p.get("media", []):
                st = self.ls.get(m["src"], {})
                if m["type"] == "video" and m["src"] and (m.get("error") or st.get("status", 0) >= 400):
                    self.add(category="functionality", severity="high", title="Video fails to load", page=pth, element="video", current=m["src"], expected="Video plays", impact="Broken video element", fix="Check the video file URL and encoding.", evidence=self.shot(p))
                if m["type"] == "iframe" and m["src"] and st.get("status", 0) >= 400:
                    self.add(category="functionality", severity="high", title="Broken embed (iframe)", page=pth, element="iframe", current=f"{st['status']} {m['src']}", expected="Embed loads", impact="Map/video/widget missing", fix="Update the embed URL.")
                if m["type"] == "iframe" and m["src"] and self.cfg.is_staging(m["src"]):
                    self.add(category="functionality", severity="high", title="Embed points to staging/test environment", page=pth, element="iframe", current=m["src"], expected="Production URL", impact="Embed may break or expose test data", fix="Point to the production embed URL.")
            # slow / huge media
            for r in p.get("resources", []):
                if r["type"] in ("img", "video", "media", "css", "script") and r["ms"] > 4000 and r["bytes"] > 50000:
                    self.add(category="functionality", severity="medium", title="Slow-loading media/asset", page=pth, element=r["type"], current=f"{r['url'][:120]} ({r['ms']} ms, {r['bytes']//1024} KB)", expected="Under 2 s", impact="Image/video appears late or stalls", fix="Compress, resize, lazy-load or move to a CDN.")
            # sliders
            for s in p.get("sliders", []):
                if s["slides"] == 0 and s["images"] == 0:
                    continue
                if s["broken_images"]:
                    self.add(category="functionality", severity="high", title="Slider/carousel has broken images", page=pth, element=s["sel"], current=f"{s['broken_images']} broken of {s['images']}", expected="All slides load", impact="Empty slides in carousel", fix="Fix slide image URLs.", evidence=self.shot(p))
                if s["slides"] == 1 and s["has_controls"]:
                    self.add(category="functionality", severity="low", title="Carousel with a single slide", page=pth, element=s["sel"], current="1 slide", expected="2+ slides or no carousel controls", impact="Arrows/dots do nothing", fix="Add slides or remove the carousel.")
            # dead buttons
            for bt in p.get("button_tests", []):
                if bt.get("error") or bt.get("changed") is None: continue
                if not bt["changed"]:
                    self.add(category="functionality", severity="high" if CTA_RE.search(bt["text"]) else "medium", title="Button does nothing when clicked", page=pth, element=f"{bt['sel']} \"{bt['text']}\"", current="No navigation, modal, scroll or DOM change after click", expected="Button triggers an action", impact="Dead UI element; conversion blocker if it is a CTA", fix="Attach the missing handler or link, or remove the button.", evidence=self.shot(p))
            # URL structure
            self.check_url_structure(u, pth)

    def check_url_structure(self, u, pth):
        probs = []
        if re.search(r"[A-Z]", urlparse(u).path): probs.append("uppercase characters")
        if "%20" in u or " " in u: probs.append("spaces")
        if re.search(r"(?<!:)//", urlparse(u).path): probs.append("double slashes")
        if re.search(r"/index\.(php|html?)$", u): probs.append("explicit index file (duplicate of directory URL)")
        if re.search(r"[^\x00-\x7F]", u): probs.append("non-ASCII characters")
        if len(u) > 150: probs.append("very long URL")
        if re.search(r"[_]{1}", urlparse(u).path) and "-" not in urlparse(u).path: probs.append("underscores instead of hyphens")
        if u.startswith("http://"): probs.append("not HTTPS")
        if probs:
            self.add(category="seo", severity="low", title="URL structure issue", page=pth, current=", ".join(probs), expected="Lowercase, hyphenated, https, no index file", impact="Duplicate URLs, ugly links", fix="Normalize the URL and 301 the old version.")

    # ======================= 2. CTA / wrong links =======================
    def check_cta(self):
        cfg = self.cfg
        for u, p in self.ok_pages.items():
            pth = path_of(u)
            page_locs = set(cfg.location_for(u)) or set(cfg.location_for(p.get("title", "")))
            seen_here = set()
            for l in p.get("links", []):
                href, text = l["href"], l["text"] or ""
                key = (href, text)
                if key in seen_here: continue
                seen_here.add(key)
                st = self.ls.get(href) or self.ls.get(href.split("#")[0]) or {}
                is_cta = bool(CTA_RE.search(text)) and l["visible"]
                raw = (l.get("raw") or "").strip()
                # dead CTA href
                if is_cta and (raw in ("#", "", "javascript:void(0)", "javascript:;", "javascript:void(0);") or raw.lower().startswith("javascript:")) and l["region"] == "main":
                    self.add(category="cta", severity="high", title="CTA link has no destination", page=pth, element=f"\"{text}\" ({l['sel']})", current=raw or "(empty href)", expected="A real URL", impact="Clicking the CTA does nothing unless JS handles it", fix="Point the link at the intended page.", evidence=self.shot(p))
                # broken link
                if href.startswith("http") and st:
                    if st.get("loop"):
                        self.add(category="functionality", severity="high", title="Redirect loop", page=pth, element=f"\"{text}\"", current=href, expected="Resolves to a page", impact="Link never loads", fix="Fix the redirect rules for this URL.")
                    elif st.get("status", 1) >= 400 or st.get("status") == 0:
                        inte = host_of(href) == self.host
                        sev = "critical" if (is_cta and inte) else ("high" if inte else "medium")
                        self.add(category="cta" if is_cta else ("navigation" if l["region"] in ("nav", "footer", "breadcrumb") else "functionality"), severity=sev,
                                 title=("Broken CTA link" if is_cta else "Broken link") + ("" if inte else " (external)"), page=pth, element=f"\"{text}\" in {l['region']}", current=f"{st.get('status') or st.get('error')} {href}", expected="200", impact="Dead end for users" + (" at a conversion point" if is_cta else ""), fix="Update or remove the link.", evidence=self.link_shot(u, href, p))
                    elif st.get("redirects", 0) >= 2:
                        self.add(category="functionality", severity="low", title="Link goes through multiple redirects", page=pth, element=f"\"{text}\"", current=f"{href} → {st['final']} ({st['redirects']} hops)", expected="Direct link", impact="Slower navigation", fix="Link directly to the final URL.")
                    elif st.get("final") and host_of(st["final"]) != host_of(href) and host_of(href) == self.host:
                        self.add(category="cta", severity="high", title="Internal link redirects off-site", page=pth, element=f"\"{text}\"", current=f"{href} → {st['final']}", expected="Stays on site or intentional", impact="Users leave the site unexpectedly", fix="Verify this redirect is intentional.")
                elif href.startswith("http") and host_of(href) == self.host:
                    tp = self.pages.get(href) or self.pages.get(href.split("#")[0])
                    if tp and (tp["status"] >= 400 or (tp["error"] and not tp["error"].startswith("non-html") and not re.search(r"Timeout|TIMED_OUT", tp["error"]))):
                        self.add(category="cta" if is_cta else "functionality", severity="critical" if is_cta else "high", title="Broken CTA link" if is_cta else "Broken link", page=pth, element=f"\"{text}\" in {l['region']}", current=f"{tp['status'] or tp['error']} {href}", expected="200", impact="Dead end for users", fix="Update or remove the link.", evidence=self.link_shot(u, href, p))
                # staging
                if cfg.is_staging(href) and host_of(href) != self.host:
                    self.add(category="cta", severity="critical", title="Link points to staging/test environment", page=pth, element=f"\"{text}\"", current=href, expected="Production URL", impact="Users land on a test site", fix="Replace with the live URL.")
                # tel / mailto correctness
                if href.startswith("tel:"):
                    num = norm_phone(href[4:])
                    if len(num) < 10 or len(num) > 15:
                        self.add(category="cta", severity="high", title="Malformed phone number in call link", page=pth, element=f"\"{text}\"", current=href, expected="tel:+1XXXXXXXXXX", impact="Call button fails on mobile", fix="Use a full phone number with country code.")
                    elif cfg.phones and num not in cfg.phones:
                        self.add(category="cta", severity="critical", title="Call button uses a phone number not in the approved list", page=pth, element=f"\"{text}\"", current=href, expected="One of: " + ", ".join(sorted(cfg.phones)), impact="Calls go to the wrong number", fix="Update the tel: link.")
                    elif page_locs:
                        owner = [loc["name"] for loc in cfg.locations if num in loc["_phones"]]
                        if owner and not (set(owner) & page_locs):
                            self.add(category="cta", severity="critical", title="Call button points to a different location's phone", page=pth, element=f"\"{text}\"", current=f"{href} ({owner[0]} number)", expected=f"{'/'.join(page_locs)} phone number", impact="Customers call the wrong location", fix="Use this location's phone number.", evidence=self.shot(p))
                    vt = norm_phone(text)
                    if vt and len(vt) >= 10 and vt != num:
                        self.add(category="cta", severity="critical", title="Call button text and tel: link show different numbers", page=pth, element=f"\"{text}\"", current=href, expected=f"tel link matching {text}", impact="Users dial a number different from what they read", fix="Make the tel: link match the visible number.")
                if href.startswith("mailto:"):
                    em = href[7:].split("?")[0].lower()
                    if not re.match(r"^[\w.+-]+@[\w-]+\.[\w.-]+$", em):
                        self.add(category="cta", severity="high", title="Malformed email link", page=pth, element=f"\"{text}\"", current=href, expected="mailto:name@domain", impact="Email button fails", fix="Fix the mailto address.")
                    elif cfg.emails and em not in cfg.emails:
                        self.add(category="cta", severity="high", title="Email link uses an address not in the approved list", page=pth, element=f"\"{text}\"", current=href, expected="One of: " + ", ".join(sorted(cfg.emails)), impact="Enquiries go to the wrong inbox", fix="Update the mailto: link.")
                    if "@" in text and text.strip().lower() != em and re.match(r"^[\w.+-]+@[\w-]+\.[\w.-]+$", text.strip()):
                        self.add(category="cta", severity="high", title="Email link text and mailto address differ", page=pth, element=f"\"{text}\"", current=href, expected=f"mailto:{text.strip()}", impact="Users email a different address than shown", fix="Make the mailto match the visible address.")
                # location mismatch (config)
                if cfg.locations and href.startswith("http") and l["visible"]:
                    link_locs = set(cfg.location_for(href))
                    text_locs = set(cfg.location_for(text))
                    src = text_locs or (page_locs if (is_cta and l["region"] == "main") else set())
                    if src and link_locs and not (src & link_locs):
                        self.add(category="cta", severity="critical", title="CTA leads to the wrong location", page=pth, element=f"\"{text}\" button", current=href, expected=f"{'/'.join(sorted(src))} destination", impact="Conversion", fix="Update the CTA destination to this location's page.", evidence=self.shot(p),
                                 details={"page_location": sorted(page_locs), "link_location": sorted(link_locs), "text_location": sorted(text_locs)})
                # text vs destination semantic checks
                hp = urlparse(href); hpath = (hp.path + "?" + hp.query).lower()
                if is_cta and href.startswith("http") and l["region"] == "main":
                    if CONTACT_RE.search(text) and not (CONTACT_RE.search(hpath) or "mailto:" in href or "tel:" in href or hp.fragment) and host_of(href) == self.host and hpath.strip("/") in ("", "index.php", "index.html"):
                        self.add(category="cta", severity="high", title="\"Contact\" CTA goes to the homepage", page=pth, element=f"\"{text}\"", current=href, expected="Contact page or form", impact="Users cannot find how to contact you", fix="Link to the contact page.")
                    if BOOK_RE.search(text) and host_of(href) == self.host and hpath.strip("/") in ("", "index.php", "index.html"):
                        self.add(category="cta", severity="critical", title="Booking/purchase CTA goes to the homepage", page=pth, element=f"\"{text}\"", current=href, expected="Booking flow", impact="Conversion dead end", fix="Point the CTA at the booking page.")
                    if BOOK_RE.search(text) and cfg.locations and page_locs:
                        for loc in cfg.locations:
                            if loc["name"] in page_locs and loc.get("booking_url") and host_of(href) == self.host and "book" in text.lower():
                                bu = loc["booking_url"]
                                if bu and bu.rstrip("/") not in href and not cfg.location_for(href):
                                    self.add(category="cta", severity="medium", title="Booking CTA does not use this location's configured booking URL", page=pth, element=f"\"{text}\"", current=href, expected=bu, impact="May route bookings to a generic or wrong flow", fix="Verify the booking destination.")
                # social/brand text vs destination
                for brand in ("facebook", "instagram", "twitter", "linkedin", "youtube", "tiktok", "yelp", "google", "pinterest"):
                    tl = text.lower() + " " + l["sel"].lower()
                    if brand in tl and href.startswith("http") and brand not in host_of(href) and not (brand == "twitter" and "x.com" in host_of(href)) and not (brand == "google" and ("goo.gl" in href or "g.page" in href or "maps" in href)):
                        if host_of(href) != self.host:
                            self.add(category="cta", severity="high", title=f"\"{brand.title()}\" link goes to a different site", page=pth, element=f"\"{text or l['sel']}\" in {l['region']}", current=href, expected=f"{brand}.com profile", impact="Wrong destination", fix="Fix the social link.")
                # external link to unexpected site
                if href.startswith("http") and host_of(href) != self.host and cfg.allowed_external and l["visible"] and l["region"] == "main":
                    if not any(d in host_of(href) for d in cfg.allowed_external):
                        self.add(category="cta", severity="low", title="External link to a domain not on the allow-list", page=pth, element=f"\"{text}\"", current=href, expected="Allowed external domain", impact="Verify this is intended", fix="Add to allow-list or remove.")
                # external link without target or with target=_blank missing rel
                if href.startswith("http") and host_of(href) != self.host and l.get("target") == "_blank" and "noopener" not in (l.get("rel") or "") and False:
                    pass

    # ======================= 3. Forms =======================
    def check_forms(self):
        for u, p in self.ok_pages.items():
            pth = path_of(u)
            for f in p.get("forms", []):
                if not f["visible"] or not f["fields"]: continue
                act = f.get("action") or ""
                if act and self.cfg.is_staging(act):
                    self.add(category="forms", severity="critical", title="Form submits to staging/test endpoint", page=pth, element=f["sel"], current=act, expected="Production endpoint", impact="Leads lost", fix="Update the form action.")
                if act.startswith("http://") and u.startswith("https://"):
                    self.add(category="forms", severity="high", title="Form submits over insecure HTTP", page=pth, element=f["sel"], current=act, expected="https action", impact="Browser warning, data exposed", fix="Use https in the form action.")
                if not f["submits"]:
                    self.add(category="forms", severity="high", title="Form has no submit button", page=pth, element=f["sel"], current=f"{len(f['fields'])} fields, 0 submit buttons", expected="A submit button", impact="Form cannot be sent", fix="Add a submit button.", evidence=self.shot(p))
                elif all(s["disabled"] for s in f["submits"]):
                    self.add(category="forms", severity="medium", title="Submit button disabled on load", page=pth, element=f["sel"], current="disabled", expected="Enabled once fields are valid", impact="Users may not be able to submit", fix="Verify the enable logic works.")
                for fld in f["fields"]:
                    if fld["tag"] == "select" and fld["options"] <= 1 and fld["visible"]:
                        self.add(category="forms", severity="high", title="Dropdown has no options", page=pth, element=f"{f['sel']} select[name={fld['name']}]", current=f"{fld['options']} option(s)", expected="Selectable options", impact="Users cannot complete the form", fix="Populate the select options.", evidence=self.shot(p))
            for t in p.get("form_tests", []):
                if t.get("error"): continue
                el = t["sel"]
                if t.get("required", 0) > 0 and t.get("empty_submit_request", 0) > 0:
                    self.add(category="forms", severity="high", title="Form submits with required fields empty", page=pth, element=el, current=f"Empty submit sent a request to {t.get('request_target') or t['action']}", expected="Validation blocks empty submit", impact="Junk submissions, broken validation", fix="Enforce required-field validation.")
                if t.get("filled") and not t.get("captcha"):
                    if t.get("filled_submit_request", 0) == 0 and t.get("filled_invalid_count", 0) == 0 and not t.get("error_text"):
                        self.add(category="forms", severity="critical", title="Form does not submit", page=pth, element=el, current="Filled every field and clicked submit: no request sent, no validation message shown", expected="Submission request or validation feedback", impact="Leads silently lost", fix="Debug the submit handler.", evidence=self.shot(p))
                    elif t.get("filled_submit_request", 0) == 0 and (t.get("filled_invalid_count", 0) > 0 or t.get("error_text")):
                        self.add(category="forms", severity="medium", title="Form rejected valid-looking test data", page=pth, element=el, current=f"invalid fields: {t.get('filled_invalid_count')} — {t.get('error_text','')[:100]}", expected="Standard test data accepted", impact="Overly strict validation may block real users", fix="Review validation rules (phone/email formats).", evidence=self.shot(p))
                    elif t.get("request_target") and self.cfg.is_staging(t["request_target"]):
                        self.add(category="forms", severity="critical", title="Form posts to staging/test endpoint", page=pth, element=el, current=t["request_target"], expected="Production endpoint", impact="Leads lost", fix="Update the endpoint.")
                elif t.get("captcha") and t.get("filled_submit_request", 0) == 0:
                    pass  # cannot assess behind captcha
        # thank-you / confirmation pages
        for u, p in self.pages.items():
            if re.search(r"thank|confirm|success", u, re.I) and (p["status"] >= 400 or p["error"]):
                self.add(category="forms", severity="high", title="Confirmation/thank-you page broken", page=path_of(u), current=str(p["status"] or p["error"]), expected="200", impact="Post-submit experience broken; conversion tracking may fail", fix="Restore the page.")
        for href, st in self.ls.items():
            if host_of(href) == self.host and re.search(r"thank|confirm", href, re.I) and (st["status"] >= 400 or st["status"] == 0):
                self.add(category="forms", severity="high", title="Confirmation/thank-you page broken", page=path_of(href), current=str(st["status"] or st["error"]), expected="200", impact="Post-submit experience broken", fix="Restore the page.")

    # ======================= 4. Responsive + Visual =======================
    def check_responsive(self):
        for u, p in self.ok_pages.items():
            pth = path_of(u)
            vps = p.get("viewports", {})
            if not vps: continue
            if not (p.get("meta", {}).get("viewport") or vps.get("mobile", {}).get("viewport_meta")):
                self.add(category="responsive", severity="critical", title="Missing viewport meta tag", page=pth, current="none", expected='<meta name="viewport" content="width=device-width, initial-scale=1">', impact="Site renders as desktop on phones", fix="Add the viewport meta tag.", evidence=self.shot(p, "mobile"))
            for vp, m in vps.items():
                if "error" in m: continue
                cat = "visual" if vp == "desktop" else "responsive"
                ev = self.shot(p, vp); tag = f"[{vp}]"
                if m.get("horizontal_scroll"):
                    self.add(category="responsive", severity="high" if vp != "desktop" else "medium", title=f"Horizontal scrolling {tag}", page=pth, current=f"content width {m['scroll_w']}px > viewport {m['vw']}px", expected="No horizontal overflow", impact="Layout broken, content cut off", fix="Find the overflowing element (see details) and constrain its width.", evidence=ev, details={"overflow": m.get("overflow", [])[:5]})
                for o in m.get("overflow", [])[:3]:
                    if o.get("text") or vp != "desktop":
                        self.add(category=cat, severity="medium", title=f"Element extends outside the screen {tag}", page=pth, element=o["sel"], current=f"right edge at {o['right']}px (viewport {m['vw']}px) \"{o.get('text','')[:40]}\"", expected="Within viewport", impact="Content hidden/cut", fix="Constrain width or wrap.", evidence=ev)
                for c in m.get("cutoff", [])[:3]:
                    self.add(category=cat, severity="medium", title=f"Text cut off {tag}", page=pth, element=c["sel"], current=f"\"{c['text']}\"", expected="Full text visible", impact="Unreadable text", fix="Allow wrapping or widen the container.", evidence=ev)
                for o in m.get("overlaps", [])[:3]:
                    self.add(category=cat, severity="medium", title=f"Overlapping elements {tag}", page=pth, element=f"{o['sel_a']} / {o['sel_b']}", current=f"\"{o['a']}\" overlaps \"{o['b']}\"", expected="No overlap", impact="Unreadable or unclickable content", fix="Fix positioning/z-index or spacing.", evidence=ev)
                for h in m.get("hidden_ctas", [])[:3]:
                    self.add(category="responsive" if vp != "desktop" else "cta", severity="high", title=f"CTA not visible {tag}", page=pth, element=f"\"{h['text']}\" ({h['sel']})", current="hidden / zero size", expected="CTA visible", impact="Conversion blocked", fix="Make the CTA visible at this breakpoint.", evidence=ev)
                for h in m.get("offscreen_ctas", [])[:3]:
                    self.add(category=cat, severity="high", title=f"CTA positioned off-screen {tag}", page=pth, element=f"\"{h['text']}\" ({h['sel']})", current="outside viewport", expected="On screen", impact="Conversion blocked", fix="Fix positioning.", evidence=ev)
                for h in m.get("tiny_ctas", [])[:2]:
                    if vp == "mobile":
                        self.add(category="responsive", severity="low", title="CTA too small to tap [mobile]", page=pth, element=f"\"{h['text']}\" ({h['sel']})", current="< 24px", expected="≥ 44px tap target", impact="Hard to tap", fix="Increase padding.", evidence=ev)
                for fx in m.get("fixed", [])[:2]:
                    if fx["pct"] >= 35 and vp == "mobile":
                        self.add(category="responsive", severity="high", title="Sticky element covers much of the mobile screen", page=pth, element=fx["sel"], current=f"{fx['pct']}% of viewport", expected="< 25%", impact="Content hidden behind sticky bar/header", fix="Shrink or hide the sticky element on mobile.", evidence=ev)
                if m.get("small_targets", 0) >= 8:
                    self.add(category="responsive", severity="low", title="Many tiny tap targets [mobile]", page=pth, current=f"{m['small_targets']} links/buttons under 24px tall", expected="≥ 44px tap targets", impact="Mis-taps on phones", fix="Increase spacing/padding of links.", evidence=ev)
                if m.get("small_text", 0) >= 5 and vp == "mobile":
                    self.add(category="responsive", severity="low", title="Text below 11px [mobile]", page=pth, current=f"{m['small_text']} elements", expected="≥ 14px body text", impact="Unreadable on phones", fix="Increase font size.", evidence=ev)
                for es in m.get("empty_sections", [])[:2]:
                    self.add(category=cat, severity="medium", title=f"Empty section {tag}", page=pth, element=es["sel"], current=f"{es['h']}px tall, no content", expected="Content or removal", impact="Blank area on the page", fix="Fill or remove the section.", evidence=ev)
                if m.get("max_gap", 0) > 500:
                    self.add(category=cat, severity="low", title=f"Unusually large whitespace {tag}", page=pth, current=f"{m['max_gap']}px gap at y={m['gap_at']}", expected="< 300px between sections", impact="Looks broken / content missing", fix="Check for a failed element or excessive margin.", evidence=ev)
                for d in m.get("distorted", [])[:2]:
                    self.add(category=cat, severity="medium", title=f"Image stretched/squashed {tag}", page=pth, element=d["src"][-80:], current=f"natural {d['natural']}, rendered {d['rendered']}", expected="Aspect ratio preserved", impact="Distorted image", fix="Use object-fit or correct dimensions.", evidence=ev)
                for md in m.get("modals", [])[:1]:
                    if not md["has_close"]:
                        self.add(category=cat, severity="high", title=f"Pop-up/modal with no close button {tag}", page=pth, element=md["sel"], current=f"covers {md['pct']}% of screen", expected="Closable", impact="Blocks content", fix="Add a close control.", evidence=ev)
                    elif md["pct"] >= 60 and vp == "mobile":
                        self.add(category="responsive", severity="medium", title="Pop-up covers most of the mobile screen", page=pth, element=md["sel"], current=f"{md['pct']}%", expected="Smaller or dismissible", impact="Blocks content", fix="Reduce popup size on mobile.", evidence=ev)
                if vp == "mobile":
                    hb = m.get("hamburger")
                    if hb and hb.get("found") and not hb.get("works"):
                        self.add(category="navigation", severity="critical", title="Hamburger menu does not open", page=pth, element=p["nav"].get("hamburger_sel", ""), current=f"visible nav links before: {hb['before']}, after click: {hb['after']}", expected="Menu opens", impact="Mobile users cannot navigate", fix="Fix the menu toggle script.", evidence=ev)
                    if not p["nav"].get("hamburger_sel") and p["nav"].get("nav_link_count", 0) >= 5 and m.get("cta_count", 0) >= 0:
                        pass  # some sites keep a visible nav; not flagged

    # ======================= 5. Content =======================
    def check_content(self):
        from spellchecker import SpellChecker
        sp = SpellChecker()
        site_words = Counter()
        page_words = {}
        for u, p in self.ok_pages.items():
            mt = p.get("perf", {}).get("main_text", "") or p.get("text", "")
            words = re.findall(r"\b[a-z]{4,}\b", mt)
            page_words[u] = words
            site_words.update(set(words))
        brand = {w for w, c in site_words.items() if c >= 3}  # recurring site terms treated as vocabulary
        brand |= self.cfg.spelling_whitelist | {w.lower() for w in re.findall(r"[a-z]+", self.host)}
        titles = Counter(); hashes = defaultdict(list); h1s = Counter()
        for u, p in self.ok_pages.items():
            pth = path_of(u); mt = p.get("perf", {}).get("main_text", "") or p.get("text", ""); full = p.get("text", "")
            # placeholders
            m = PLACEHOLDER_RE.search(full)
            if m:
                self.add(category="content", severity="high", title="Placeholder / unfinished content", page=pth, current=full[max(0, m.start()-40):m.end()+40], expected="Real content", impact="Looks unprofessional", fix="Replace placeholder text.", evidence=self.shot(p))
            # encoding / mojibake
            mj = re.search(r"�|Ã©|Ã¨|Ã¡|â€™|â€œ|â€|â€“|â€”|Â£|Â®|Ã‚", full)
            if mj:
                self.add(category="content", severity="medium", title="Broken characters (encoding error)", page=pth, current=full[max(0, mj.start()-40):mj.end()+40], expected="Correct symbol (e.g. ©, ’, –)", impact="Garbled text visible to users", fix="Fix the file/database charset (serve as UTF-8).", evidence=self.shot(p))
            # spelling
            cands = [w for w in set(page_words.get(u, [])) if w not in brand and site_words[w] <= 2]
            miss = sorted(sp.unknown(cands))[:10] if cands else []
            miss = [w for w in miss if not re.search(r"(.)\1{2,}", w) and sp.correction(w) not in (None, w)]
            if miss:
                self.add(category="content", severity="low", title="Possible spelling mistakes", page=pth, current=", ".join(f"{w} → {sp.correction(w)}" for w in miss), expected="Correct spelling", impact="Credibility", fix="Review and correct the listed words.", details={"words": miss})
            # double words / double spaces style
            dbl = re.findall(r"\b([a-z]{3,})\s+\1\b", mt)
            dbl = [d for d in dbl if d not in ("that", "had", "very", "bye", "no")][:5]
            if dbl:
                self.add(category="content", severity="low", title="Repeated word", page=pth, current=", ".join(f"\"{d} {d}\"" for d in dbl), expected="Single word", impact="Typo", fix="Remove the duplicate word.")
            # dates
            self.check_dates(pth, p, full)
            # weather / closure alerts
            wm = WEATHER_RE.search(full[:3000])
            if wm and re.search(r"alert|notice|closed|closure|delayed|update", full[max(0, wm.start()-150):wm.end()+150], re.I):
                self.add(category="content", severity="medium", title="Weather/closure alert is live — verify it is still current", page=pth, current=full[max(0, wm.start()-60):wm.end()+80], expected="Alert removed once resolved", impact="Customers think you are closed", fix="Remove or update the alert banner.", evidence=self.shot(p))
            # copyright year
            cy = re.search(r"(?:©|&copy;|copyright)\s*(?:\(c\)\s*)?(\d{4})(?:\s*[-–]\s*(\d{4}))?", full, re.I)
            if cy:
                yr = int(cy.group(2) or cy.group(1))
                if yr < TODAY.year:
                    self.add(category="content", severity="low", title="Outdated copyright year", page=pth, element="footer", current=cy.group(0), expected=str(TODAY.year), impact="Site looks unmaintained", fix="Update the year (or generate it dynamically).")
            # duplicates
            titles[p.get("title", "").strip()] += 1
            for h in p.get("headings", []):
                if h["tag"] == "h1": h1s[h["text"].strip().lower()] += 1
            if p.get("perf", {}).get("word_count", 0) > 80:
                hashes[p["content_hash"]].append(u)
            # thin content
            wc = p.get("perf", {}).get("word_count", 0)
            if wc < 40 and p.get("status") == 200 and not re.search(r"thank|login|cart|checkout|search", u, re.I):
                self.add(category="content", severity="medium", title="Page has almost no content", page=pth, current=f"{wc} words", expected="Meaningful content", impact="Empty/placeholder page live", fix="Add content or unpublish.", evidence=self.shot(p))
            # wrong-location content (config)
            if self.cfg.locations:
                page_locs = set(self.cfg.location_for(u))
                if page_locs:
                    for loc in self.cfg.locations:
                        if loc["name"] in page_locs: continue
                        if loc.get("address") and loc["address"].split(",")[0].lower() in mt.lower():
                            self.add(category="content", severity="high", title="Page shows another location's address", page=pth, current=loc["address"], expected=f"{'/'.join(page_locs)} address", impact="Customers go to the wrong place", fix="Replace with this location's address.", evidence=self.shot(p))
                        for ph in loc["_phones"]:
                            if ph and ph in {norm_phone(x) for x in p.get("phones", [])} and not (set(self.cfg.location_for(" ".join(p.get("phones", [])))) ):
                                self.add(category="content", severity="high", title="Page shows another location's phone number", page=pth, current=f"{ph} ({loc['name']})", expected=f"{'/'.join(page_locs)} phone", impact="Customers call the wrong location", fix="Replace with this location's phone.", details={"location": loc["name"]})
        for h, urls in hashes.items():
            if len(urls) > 1:
                paths = [path_of(x) for x in urls]
                if all(re.sub(r"/(index\.(php|html?))?/?$", "", x) == re.sub(r"/(index\.(php|html?))?/?$", "", paths[0]) for x in paths):
                    self.add(category="seo", severity="medium", title="Same page reachable at multiple URLs", page=paths[0], current=", ".join(paths), expected="One canonical URL with redirects", impact="Duplicate content", fix="301 the variants to one URL.")
                else:
                    self.add(category="content", severity="medium", title="Duplicate content across pages", page=paths[0], current="Identical body text: " + ", ".join(paths[:5]), expected="Unique content per page", impact="Copied page not customised (e.g. location clone)", fix="Rewrite or canonicalise.")

    def check_dates(self, pth, p, full):
        found = []
        for m in DATE_RE.finditer(full):
            wd, mon, day, yr = m.group(1), m.group(2), int(m.group(3)), m.group(4)
            if not yr and not wd and not EVENT_RE.search(full[max(0, m.start()-120):m.end()+120]): continue
            try:
                mi = {"jan": 1, "feb": 2, "mar": 3, "apr": 4, "may": 5, "jun": 6, "jul": 7, "aug": 8, "sep": 9, "oct": 10, "nov": 11, "dec": 12}[mon.lower()[:3]]
                y = int(yr) if yr else TODAY.year
                d = dt.date(y, mi, day)
            except Exception:
                continue
            ctx = full[max(0, m.start()-120):m.end()+120]
            if wd and d.strftime("%A").lower() != wd.lower():
                self.add(category="content", severity="high", title="Day of week does not match the date", page=pth, current=m.group(0), expected=f"{d.strftime('%A')}, {d.strftime('%B %d, %Y')}", impact="Customers show up on the wrong day", fix="Correct the weekday or the date.", details={"context": ctx})
            found.append((d, m.group(0), ctx))
        for m in NUM_DATE_RE.finditer(full):
            try:
                mo, da, y = int(m.group(1)), int(m.group(2)), int(m.group(3)); y = y + 2000 if y < 100 else y
                d = dt.date(y, mo, da); found.append((d, m.group(0), full[max(0, m.start()-120):m.end()+120]))
            except Exception: pass
        for d, s, ctx in found[:40]:
            if d < TODAY:
                if EXPIRY_RE.search(ctx):
                    self.add(category="content", severity="high", title="Expired offer/deadline still shown", page=pth, current=f"\"{s}\" — {ctx.strip()[:140]}", expected="Removed or updated after expiry", impact="Customers see a dead offer", fix="Remove or update the promotion.", evidence=self.shot(p))
                elif EVENT_RE.search(ctx) and (TODAY - d).days < 400 and not re.search(r"posted|published|blog|article|ago|news|since|founded|established|opened in|©", ctx, re.I):
                    is_article = p.get("perf", {}).get("word_count", 0) > 500 and len([x for x in urlparse(p.get("url", pth)).path.split("/")[-1].split("-") if x]) >= 4
                    self.add(category="content", severity="medium" if is_article else "high", title="Past event date in a blog article (verify)" if is_article else "Outdated event still listed", page=pth, current=f"\"{s}\" — {ctx.strip()[:140]}", expected="Upcoming events only", impact="Outdated event promotion", fix="Remove or archive the event.", evidence=self.shot(p))
                elif (TODAY - d).days > 365 * 2 and re.search(r"new|now open|latest|current|this year|upcoming", ctx, re.I):
                    self.add(category="content", severity="low", title="Old date in 'current' context", page=pth, current=f"\"{s}\" — {ctx.strip()[:120]}", expected="Current information", impact="Stale content", fix="Refresh the content.")

    # ======================= 6. Cross-page consistency =======================
    def check_consistency(self):
        cfg = self.cfg
        phone_pages = defaultdict(set); email_pages = defaultdict(set); addr_pages = defaultdict(set); hours_pages = defaultdict(set)
        loc_phone = defaultdict(lambda: defaultdict(set)); loc_addr = defaultdict(lambda: defaultdict(set)); loc_hours = defaultdict(lambda: defaultdict(set))
        n = len(self.ok_pages)
        for u, p in self.ok_pages.items():
            full = p.get("text", "")
            locs = set(cfg.location_for(u)) if cfg.locations else set()
            for ph in p.get("phones", []):
                k = norm_phone(ph)
                if len(k) == 10: phone_pages[k].add(u); [loc_phone[l][k].add(u) for l in locs]
            for em in p.get("emails", []):
                e = em.lower().strip(".")
                if not re.search(r"\.(png|jpg|gif|svg|webp|js|css)$", e) and "example" not in e and "@sentry" not in e and "@2x" not in e: email_pages[e].add(u)
            for a in ADDRESS_RE.findall(full):
                if isinstance(a, tuple): a = a[0]
            for m in ADDRESS_RE.finditer(full):
                a = re.sub(r"\s+", " ", m.group(0)).strip(" ,.")
                if len(a) > 12: addr_pages[a].add(u); [loc_addr[l][a].add(u) for l in locs]
            for m in HOURS_RE.finditer(full):
                h = re.sub(r"\s+", " ", m.group(0)).lower()
                hours_pages[h].add(u); [loc_hours[l][h].add(u) for l in locs]
        # phones: not in config
        if cfg.phones:
            for k, pages in phone_pages.items():
                if k not in cfg.phones:
                    self.add(category="consistency", severity="high", title="Phone number not in the approved list", page=path_of(sorted(pages)[0]), current=k, expected="One of: " + ", ".join(sorted(cfg.phones)), impact="Wrong number published", fix="Replace with the correct number.", details={"pages": [path_of(x) for x in sorted(pages)][:10]})
        # site-wide vs stray numbers (no config)
        if not cfg.locations and n >= 4:
            common = {k for k, pg in phone_pages.items() if len(pg) >= max(2, n * 0.5)}
            for k, pg in phone_pages.items():
                if common and k not in common and len(pg) <= 2:
                    self.add(category="consistency", severity="medium", title="Phone number inconsistency detected", page=path_of(sorted(pg)[0]), current=k, expected="Site-wide number(s): " + ", ".join(sorted(common)), impact="Customers may call a wrong/old number", fix="Confirm which number is correct and align all pages.", details={"pages": [path_of(x) for x in sorted(pg)]})
            common_e = {k for k, pg in email_pages.items() if len(pg) >= max(2, n * 0.5)}
            for k, pg in email_pages.items():
                if common_e and k not in common_e and len(pg) <= 2 and host_of("http://" + k.split("@")[-1]) == self.host:
                    self.add(category="consistency", severity="medium", title="Email address inconsistency detected", page=path_of(sorted(pg)[0]), current=k, expected="Site-wide: " + ", ".join(sorted(common_e)), impact="Enquiries may go to a wrong inbox", fix="Align the address across pages.", details={"pages": [path_of(x) for x in sorted(pg)]})
        if cfg.emails:
            for k, pg in email_pages.items():
                if k not in cfg.emails and host_of("http://" + k.split("@")[-1]) == self.host:
                    self.add(category="consistency", severity="medium", title="Email address not in the approved list", page=path_of(sorted(pg)[0]), current=k, expected=", ".join(sorted(cfg.emails)), impact="Wrong inbox", fix="Replace the address.", details={"pages": [path_of(x) for x in sorted(pg)][:10]})
        # per-location conflicts
        for loc, phs in loc_phone.items():
            cfg_loc = next((l for l in cfg.locations if l["name"] == loc), None)
            if len(phs) > 1 and not (cfg_loc and all(k in cfg_loc["_phones"] for k in phs)):
                self.add(category="consistency", severity="high", title=f"Conflicting phone numbers for {loc}", page=path_of(sorted(list(phs.values())[0])[0]), current="; ".join(f"{k} on {', '.join(path_of(x) for x in sorted(pg)[:3])}" for k, pg in phs.items()), expected=("Config: " + ", ".join(cfg_loc["_phones"])) if cfg_loc and cfg_loc["_phones"] else "One number per location", impact="Customers call the wrong number", fix="Align all pages for this location.")
        for loc, ads in loc_addr.items():
            cfg_loc = next((l for l in cfg.locations if l["name"] == loc), None)
            if cfg_loc and cfg_loc.get("address"):
                for a, pg in ads.items():
                    if a.split(",")[0].lower() not in cfg_loc["address"].lower():
                        self.add(category="consistency", severity="high", title=f"Address on {loc} page does not match configured address", page=path_of(sorted(pg)[0]), current=a, expected=cfg_loc["address"], impact="Customers go to the wrong place", fix="Correct the address.")
            elif len(ads) > 1:
                self.add(category="consistency", severity="medium", title=f"Multiple different addresses shown for {loc}", page=path_of(sorted(list(ads.values())[0])[0]), current=" | ".join(list(ads)[:3]), expected="One address", impact="Confusing directions", fix="Align the address.")
        for loc, hrs in loc_hours.items():
            if len(hrs) > 1:
                self.add(category="consistency", severity="medium", title=f"Business hours differ across {loc} pages", page=path_of(sorted(list(hrs.values())[0])[0]), current=" | ".join(f"{h} ({', '.join(path_of(x) for x in sorted(pg)[:2])})" for h, pg in list(hrs.items())[:4]), expected="Consistent hours", impact="Customers arrive when closed", fix="Update hours everywhere (ideally from one source).")
        # site-wide hours conflicts without config: same weekday-range with different times
        if not cfg.locations and len(hours_pages) > 1:
            by_days = defaultdict(set)
            for h in hours_pages:
                days = re.match(r"([a-z]+)[^\d]*?(?:[a-z]+)?", h).group(0).strip(" :-–") if re.match(r"([a-z]+)", h) else h
                by_days[h.split()[0][:3]].add(h)
            for dkey, variants in by_days.items():
                if len(variants) > 1:
                    self.add(category="consistency", severity="medium", title="Business hours stated differently across pages", page=path_of(sorted(hours_pages[list(variants)[0]])[0]), current=" | ".join(f"{v} ({path_of(sorted(hours_pages[v])[0])})" for v in list(variants)[:4]), expected="Consistent hours", impact="Customers arrive when closed", fix="Align hours across pages.")
        # booking URL consistency: CTA text "book" going to different destinations across pages (without location context)
        book_dest = defaultdict(lambda: defaultdict(set))
        for u, p in self.ok_pages.items():
            for l in p.get("links", []):
                if re.search(r"\bbook (now|online|a party|tickets?)\b", l["text"], re.I) and l["href"].startswith("http") and l["region"] in ("nav", "footer"):
                    book_dest[host_of(u)][l["text"].strip().lower()].add(l["href"])
        for hk, m in book_dest.items():
            for t, dests in m.items():
                if len(dests) > 1 and not cfg.locations:
                    self.add(category="consistency", severity="medium", title=f"\"{t}\" nav/footer CTA points to different URLs on different pages", page=f"https://{hk}/" if hk != self.host else "/", current=" | ".join(sorted(dests)[:4]), expected="One booking destination", impact="Inconsistent booking flow", fix="Standardise the CTA destination.")

    # ======================= 7. Navigation =======================
    def check_navigation(self):
        for u, p in self.site_pages.items():
            pth = path_of(u); nav = p.get("nav", {})
            pu = urlparse(u); home = f"{pu.scheme}://{pu.netloc}"
            if nav.get("has_logo") and nav.get("logo_href"):
                lh = nav["logo_href"].split("#")[0].rstrip("/")
                if host_of(lh) != host_of(u) or re.sub(r"/index(\.(php|html?))?$", "", lh) not in (home, home.replace("https://www.", "https://"), home.replace("https://", "https://www.")):
                    self.add(category="navigation", severity="medium", title="Logo does not link to the homepage", page=pth, element="logo", current=nav["logo_href"], expected=home + "/", impact="Users expect the logo to go home", fix="Link the logo to /.")
            elif nav.get("has_logo") is False and nav.get("nav_link_count", 0) > 0:
                self.add(category="navigation", severity="low", title="No clickable logo/home link detected", page=pth, current="", expected="Logo links to /", impact="Harder navigation", fix="Wrap the logo in a link to /.")
            for a in [x for x in nav.get("missing_anchors", []) if x not in ("0", "!", "top")][:5]:
                self.add(category="navigation", severity="medium", title="Anchor link target missing", page=pth, element=f"#{a}", current=f"No element with id \"{a}\"", expected="Target section exists", impact="Link does nothing", fix="Add the id or fix the href.")
            for b in nav.get("breadcrumbs", []):
                st = self.ls.get(b["href"], {})
                tp = self.pages.get(b["href"], {})
                if (st and st.get("status", 0) >= 400) or (tp and tp.get("status", 0) >= 400):
                    self.add(category="navigation", severity="medium", title="Broken breadcrumb link", page=pth, element=f"\"{b['text']}\"", current=b["href"], expected="200", impact="Broken navigation trail", fix="Fix the breadcrumb URL.", evidence=self.link_shot(u, b["href"], p))
            for pg in nav.get("pagination", []):
                st = self.ls.get(pg["href"], {})
                if st and st.get("status", 0) >= 400:
                    self.add(category="navigation", severity="medium", title="Broken pagination link", page=pth, element=f"\"{pg['text']}\"", current=pg["href"], expected="200", impact="Cannot browse further pages", fix="Fix pagination.", evidence=self.link_shot(u, pg["href"], p))
            if nav.get("nav_link_count", 0) == 0 and p.get("perf", {}).get("word_count", 0) > 100 and len(p.get("links", [])) > 5:
                self.add(category="navigation", severity="low", title="No header navigation detected", page=pth, current="0 links inside header/nav", expected="Main navigation", impact="May be a detection miss or a missing menu", fix="Verify the page has a menu.")
        # consistency of main nav across pages
        by_host = defaultdict(dict)
        for u, p in self.site_pages.items(): by_host[host_of(u)][u] = p
        for hk, pages in by_host.items():
          navs = Counter()
          for u, p in pages.items():
            key = tuple(sorted({(l["text"].lower(), l["href"]) for l in p.get("links", []) if l["region"] == "nav" and l["text"]}))
            if key: navs[key] += 1
          if len(navs) > 2 and len(pages) >= 5:
            main = navs.most_common(1)[0][0]
            for u, p in pages.items():
                key = tuple(sorted({(l["text"].lower(), l["href"]) for l in p.get("links", []) if l["region"] == "nav" and l["text"]}))
                if key and key != main and len(set(key) ^ set(main)) >= 4 and len(key) >= 3:
                    diff = [t for t, h in set(main) - set(key)][:5]
                    if diff:
                        self.add(category="navigation", severity="low", title="Header navigation differs from the rest of the site", page=path_of(u), current=f"missing: {', '.join(diff)}", expected="Consistent main menu", impact="Inconsistent navigation", fix="Use one shared header template.")

    # ======================= 8. Tracking =======================
    def check_tracking(self):
        pages = list(self.site_pages.items())
        if not pages: return
        hosts_with = defaultdict(bool); hosts_all = defaultdict(list)
        for u, p in pages:
            hosts_all[host_of(u)].append(u)
            if p.get("tracking", {}).get("ga4_ids") or p.get("tracking", {}).get("gtm_ids"): hosts_with[host_of(u)] = True
        for hk, us in hosts_all.items():
            if not hosts_with[hk]:
                self.add(category="tracking", severity="high", title="No GA4 or Google Tag Manager found on any page of this site", page=f"https://{hk}/" if hk != self.host else "/", current=f"No G-XXXX / GTM-XXXX tag on {len(us)} crawled page(s)", expected="GA4 (via GTM) on every page", impact="No analytics or conversion data", fix="Install GTM/GA4 site-wide.", details={"pages": [path_of(x) for x in us][:30]})
        has_any = True
        all_ga = defaultdict(Counter); all_gtm = defaultdict(Counter)
        for u, p in pages:
            t = p.get("tracking", {}); pth = path_of(u)
            all_ga[host_of(u)].update(t.get("ga4_ids", [])); all_gtm[host_of(u)].update(t.get("gtm_ids", []))
            if hosts_with[host_of(u)] and not (t.get("ga4_ids") or t.get("gtm_ids")):
                self.add(category="tracking", severity="high", title="Tracking code missing from this page", page=pth, current="No GA4/GTM tag", expected="Same tag as other pages", impact="Visits/conversions on this page are not tracked", fix="Add the tag to this template.")
            if t.get("gtm_loads", 0) > 1 or len(t.get("gtm_ids", [])) > 1:
                self.add(category="tracking", severity="medium", title="Duplicate Google Tag Manager installation", page=pth, current=f"{t.get('gtm_loads')} GTM loads, IDs {t.get('gtm_ids')}", expected="One container", impact="Double-counted events", fix="Remove the extra container.")
            if t.get("gtag_loads", 0) > 1 and len(t.get("ga4_ids", [])) > 1:
                self.add(category="tracking", severity="medium", title="Multiple GA4 properties / duplicate gtag loads", page=pth, current=f"{t.get('gtag_loads')} gtag loads, IDs {t.get('ga4_ids')}", expected="One GA4 property", impact="Double-counted sessions", fix="Keep one measurement ID.")
            if t.get("fb_pixel_loads", 0) > (1 if "popups" in p else 2):
                self.add(category="tracking", severity="medium", title="Meta pixel loaded more than once", page=pth, current=f"{t['fb_pixel_loads']} loads", expected="1", impact="Duplicate pixel events", fix="Remove the duplicate pixel snippet.")
            if t.get("ua_ids") and not t.get("ga4_ids") and not t.get("gtm_ids"):
                self.add(category="tracking", severity="high", title="Only legacy Universal Analytics tag present", page=pth, current=", ".join(t["ua_ids"]), expected="GA4", impact="UA stopped collecting data in 2023", fix="Migrate to GA4.")
            # widgets failing
            for br in p.get("bad_responses", []):
                if re.search(r"intercom|drift|tawk|crisp|hubspot|zendesk|tidio|livechat|freshchat|udify|chatbot|maps\.google|google\.com/maps|youtube|vimeo|calendly|acuity|booking|bookeo|roller|mindbody|square|typeform|jotform", br["url"], re.I):
                    self.add(category="tracking", severity="medium", title="Third-party widget/integration request failing", page=pth, current=f"{br['status']} {br['url']}", expected="200", impact="Chat/booking/map/video widget may be broken", fix="Check the widget's configuration, API key or domain allow-list.")
            for m in p.get("media", []):
                if m["type"] == "iframe" and m["src"] and re.search(r"google\.com/maps|maps\.google|youtube|vimeo", m["src"]) and self.ls.get(m["src"], {}).get("status", 200) >= 400:
                    self.add(category="tracking", severity="medium", title="Embedded map/video not loading", page=pth, current=m["src"], expected="Embed loads", impact="Broken embed", fix="Update the embed URL.")
            # CTA events not firing
            if t.get("has_dataLayer") or t.get("has_gtag"):
                silent = [b for b in p.get("button_tests", []) if b.get("changed") and b.get("tracking_events", 0) == 0 and CTA_RE.search(b["text"])]
                for b in silent[:2]:
                    self.add(category="tracking", severity="low", title="No tracking request fired on CTA click", page=pth, element=f"\"{b['text']}\"", current="Click produced no analytics request", expected="Event sent to GA4/GTM", impact="CTA clicks not measured", fix="Add a click trigger/event for this CTA.")
        for hk, c in all_ga.items():
            if len(c) > 1:
                self.add(category="tracking", severity="medium", title="Different GA4 IDs used across the site", page=f"https://{hk}/" if hk != self.host else "/", current=", ".join(f"{k} ({v} pages)" for k, v in c.items()), expected="One measurement ID per site", impact="Split data", fix="Standardise the tag.")
        for hk, c in all_gtm.items():
            if len(c) > 1:
                self.add(category="tracking", severity="medium", title="Different GTM containers used across the site", page=f"https://{hk}/" if hk != self.host else "/", current=", ".join(f"{k} ({v} pages)" for k, v in c.items()), expected="One container per site", impact="Split data", fix="Standardise the container.")

    # ======================= 9. SEO =======================
    def check_seo(self):
        titles = defaultdict(list); descs = defaultdict(list); inlinks = Counter()
        crawled = set(self.pages)
        for u, p in self.site_pages.items():
            for l in p.get("links", []):
                if host_of(l["href"]) == self.host:
                    inlinks[l["href"].split("#")[0]] += 1
        robots = self.c.get("robots", "")
        if not robots:
            self.add(category="seo", severity="medium", title="robots.txt missing or not plain text", page="/robots.txt", current="not found", expected="Valid robots.txt", impact="Crawlers use defaults; sitemap not advertised", fix="Add robots.txt with a Sitemap: line.")
        else:
            if re.search(r"^\s*Disallow:\s*/\s*$", robots, re.M) and re.search(r"User-agent:\s*\*", robots, re.I):
                self.add(category="seo", severity="critical", title="robots.txt blocks the whole site", page="/robots.txt", current="Disallow: /", expected="Allow crawling of public pages", impact="Site may vanish from Google", fix="Remove the blanket Disallow.")
            if "sitemap" not in robots.lower():
                self.add(category="seo", severity="low", title="robots.txt does not reference the sitemap", page="/robots.txt", current="", expected="Sitemap: https://.../sitemap.xml", impact="Slower discovery", fix="Add a Sitemap line.")
        sm = set(self.c.get("sitemap_urls", []))
        if not sm:
            self.add(category="seo", severity="medium", title="XML sitemap missing or empty", page="/sitemap.xml", current=self.c.get("sitemap_error") or "not found", expected="Valid sitemap", impact="Slower indexing", fix="Generate a sitemap and reference it in robots.txt.")
        else:
            not_linked = [u for u in sm if u not in crawled and host_of(u) == self.host]
            if not_linked and len(self.pages) >= self.c.get("max_pages", 10**9) * 0 + 1:
                for u in not_linked[:15]:
                    self.add(category="seo", severity="low", title="Orphan page (in sitemap, not reachable by internal links)", page=path_of(u), current="0 internal links found", expected="Linked from navigation or content", impact="Hard for users and crawlers to find", fix="Link to it, or remove from sitemap if obsolete.")
            missing = [u for u in self.site_pages if u not in sm and not re.search(r"\?|/index\.(php|html?)$", u)]
            if sm and missing and len(missing) <= len(self.site_pages) * 0.6:
                for u in missing[:15]:
                    self.add(category="seo", severity="low", title="Page not in XML sitemap", page=path_of(u), current="", expected="Listed in sitemap", impact="Slower discovery", fix="Add to sitemap.")
        for u, p in self.site_pages.items():
            pth = path_of(u); meta = p.get("meta", {}); t = (p.get("title") or "").strip(); d = (meta.get("description") or "").strip()
            titles[t.lower()].append(pth); descs[d.lower()].append(pth) if d else None
            robots_meta = (meta.get("robots") or "") + " " + (meta.get("googlebot") or "")
            if "noindex" in robots_meta.lower():
                self.add(category="seo", severity="high", title="Page is set to noindex", page=pth, current=robots_meta.strip(), expected="index (unless intentional)", impact="Page excluded from Google", fix="Remove noindex if this page should rank.")
            if not t: self.add(category="seo", severity="high", title="Missing page title", page=pth, current="", expected="Unique descriptive <title>", impact="Poor rankings/CTR", fix="Add a title tag.")
            elif len(t) > 65: self.add(category="seo", severity="low", title="Title too long", page=pth, current=f"{len(t)} chars: {t[:80]}", expected="≤ 60 chars", impact="Truncated in SERP", fix="Shorten the title.")
            elif len(t) < 15: self.add(category="seo", severity="low", title="Title too short", page=pth, current=t, expected="15–60 chars", impact="Weak SERP snippet", fix="Expand the title.")
            if not d: self.add(category="seo", severity="medium", title="Missing meta description", page=pth, current="", expected="120–160 char description", impact="Google writes its own snippet", fix="Add a meta description.")
            elif len(d) > 170: self.add(category="seo", severity="low", title="Meta description too long", page=pth, current=f"{len(d)} chars", expected="≤ 160", impact="Truncated snippet", fix="Shorten.")
            h1 = [h["text"] for h in p.get("headings", []) if h["tag"] == "h1"]
            if not h1: self.add(category="seo", severity="medium", title="Missing H1", page=pth, current="0 H1", expected="One H1", impact="Weak on-page structure", fix="Add an H1.")
            elif len(h1) > 1: self.add(category="seo", severity="low", title="Multiple H1 tags", page=pth, current=f"{len(h1)}: " + " | ".join(x[:40] for x in h1[:3]), expected="One H1", impact="Diluted heading hierarchy", fix="Keep one H1.")
            levels = [int(h["tag"][1]) for h in p.get("headings", [])]
            for i in range(1, len(levels)):
                if levels[i] > levels[i-1] + 1:
                    self.add(category="seo", severity="low", title="Heading hierarchy skips a level", page=pth, current=f"h{levels[i-1]} → h{levels[i]}", expected="No skipped levels", impact="Accessibility/SEO", fix="Use sequential heading levels."); break
            can = meta.get("canonical", "")
            if not can: self.add(category="seo", severity="low", title="Missing canonical tag", page=pth, current="", expected=f"<link rel=canonical href={u}>", impact="Duplicate URL risk", fix="Add a self-referencing canonical.")
            else:
                if host_of(can) != host_of(u):
                    sib = host_of(can).endswith(".".join(self.host.split(".")[-2:]))
                    self.add(category="seo", severity="high", title="Canonical points to another location's site" if sib else "Canonical points to a different domain", page=pth, current=can, expected=u, impact="Google indexes the other location's page instead of this one (copied template not updated)", fix="Set a self-referencing canonical on this page.")
                elif can.split("#")[0].rstrip("/") != u.split("#")[0].rstrip("/") and not re.sub(r"/index\.(php|html?)$", "", can.rstrip("/")) == re.sub(r"/index\.(php|html?)$", "", u.rstrip("/")):
                    st = self.ls.get(can, {}); tp = self.pages.get(can, {})
                    if (st and st.get("status", 0) >= 400) or (tp and tp.get("status", 0) >= 400):
                        self.add(category="seo", severity="high", title="Canonical points to a broken URL", page=pth, current=can, expected="Live URL", impact="Indexing confusion", fix="Fix the canonical.")
                    elif not (p.get("perf", {}).get("word_count", 0) and p["content_hash"] == self.pages.get(can, {}).get("content_hash")):
                        self.add(category="seo", severity="medium", title="Canonical points to a different page", page=pth, current=can, expected=u + " (self) unless intentional", impact="This page may not be indexed", fix="Verify the canonical target.")
            if not meta.get("lang"): self.add(category="seo", severity="low", title="Missing html lang attribute", page=pth, current="", expected='<html lang="en">', impact="Accessibility/SEO", fix="Set the lang attribute.")
            noalt = [im for im in p.get("images", []) if im["visible"] and (im["alt"] is None) and im["natural_w"] > 50]
            if noalt: self.add(category="seo", severity="low", title="Images missing alt text", page=pth, current=f"{len(noalt)} images, e.g. {noalt[0]['src'][-60:]}", expected="Descriptive alt on content images", impact="Accessibility, image SEO", fix="Add alt attributes.", details={"images": [i["src"] for i in noalt[:10]]})
            if u.startswith("https://"):
                mixed = [r["url"] for r in p.get("resources", []) if r["url"].startswith("http://")]
                if mixed: self.add(category="seo", severity="medium", title="Mixed content (http resources on https page)", page=pth, current=mixed[0][:120] + (f" (+{len(mixed)-1})" if len(mixed) > 1 else ""), expected="All https", impact="Browser warnings, blocked assets", fix="Update resource URLs to https.")
            # schema
            for raw in p.get("jsonld", []):
                try:
                    data = json.loads(raw)
                except Exception as e:
                    self.add(category="seo", severity="medium", title="Invalid JSON-LD schema", page=pth, current=str(e)[:100], expected="Valid JSON", impact="Rich results lost", fix="Fix the JSON syntax."); continue
                items = data if isinstance(data, list) else [data]
                stack = list(items)
                while stack:
                    it = stack.pop()
                    if not isinstance(it, dict): continue
                    if "@graph" in it: stack.extend(it["@graph"]); continue
                    tel = it.get("telephone")
                    if tel and norm_phone(str(tel)) and norm_phone(str(tel)) not in {norm_phone(x) for x in p.get("phones", [])} and len(norm_phone(str(tel))) == 10:
                        self.add(category="seo", severity="medium", title="Schema phone number not shown on the page", page=pth, current=str(tel), expected="Matches on-page phone", impact="Conflicting info for Google", fix="Align schema with page content.")
                    if self.cfg.phones and tel and norm_phone(str(tel)) not in self.cfg.phones:
                        self.add(category="seo", severity="high", title="Schema phone number not in approved list", page=pth, current=str(tel), expected=", ".join(sorted(self.cfg.phones)), impact="Wrong number in rich results", fix="Fix schema telephone.")
                    surl = it.get("url")
                    if isinstance(surl, str) and surl.startswith("http") and host_of(surl) != host_of(u):
                        self.add(category="seo", severity="medium", title="Schema url points to another domain", page=pth, current=surl, expected=u, impact="Wrong entity signals", fix="Fix schema url.")
                    nm = it.get("name")
                    if isinstance(nm, str) and self.cfg.locations:
                        pl = set(self.cfg.location_for(u)); nl = set(self.cfg.location_for(nm))
                        if pl and nl and not (pl & nl):
                            self.add(category="seo", severity="high", title="Schema describes a different location than the page", page=pth, current=nm, expected=f"{'/'.join(pl)} entity", impact="Wrong location in rich results", fix="Fix the LocalBusiness schema.")
        for t, pths in titles.items():
            if t and len(pths) > 1 and len({re.sub(r"/(index\.(php|html?))?/?$", "", x) for x in pths}) > 1:
                self.add(category="seo", severity="medium", title="Duplicate page title", page=pths[0], current=f"\"{t[:70]}\" on {len(pths)} pages: " + ", ".join(pths[:5]), expected="Unique titles", impact="Pages compete with each other", fix="Write unique titles.")
        for d, pths in descs.items():
            if d and len(pths) > 1 and len({re.sub(r"/(index\.(php|html?))?/?$", "", x) for x in pths}) > 1:
                self.add(category="seo", severity="low", title="Duplicate meta description", page=pths[0], current=f"on {len(pths)} pages: " + ", ".join(pths[:5]), expected="Unique descriptions", impact="Weak snippets", fix="Write unique descriptions.")
        # weak internal linking
        for u in self.site_pages:
            if inlinks.get(u, 0) <= 1 and u != self.c["start_url"] and len(self.site_pages) > 5 and self.pages[u]["depth"] > 1:
                self.add(category="seo", severity="low", title="Page has only one internal link pointing to it", page=path_of(u), current=f"{inlinks.get(u, 0)} inlink(s)", expected="Several internal links", impact="Weak page authority / discoverability", fix="Link to this page from related content.")

    # ======================= 11. Popups (must have a working URL that carries UTMs) =======================
    def check_popups(self):
        for u, p in self.ok_pages.items():
            pth = path_of(u); pops = p.get("popups") or []
            if isinstance(pops, dict): continue
            for pop in pops:
                kind = pop.get("kind")
                if not kind:   # older crawls: classify from the popup text
                    t = (pop.get("text") or "").lower()
                    kind = "consent" if re.search(r"cookie|consent|privacy|gdpr|accept all", t) else "auth" if re.search(r"(log ?in|sign ?in|password)", t) else "promo"
                if kind == "promo" and not (pop.get("text") or "").strip() and not pop.get("links") and not pop.get("imgs"):
                    continue   # bare backdrop with no content captured (older crawls)
                if kind != "promo":
                    continue   # cookie consent, login, age gate, newsletter and chat dialogs are not promotions
                el = f"{pop['sel']} \"{pop['text'][:50]}\""; ev = self.shot(p)
                links = pop.get("links", [])
                if not links and not pop.get("clickable"):
                    self.add(category="cta", severity="high", title="Popup has no link/destination", page=pth, element=el, current="No clickable URL inside the popup", expected="Popup links to a landing page with UTM parameters", impact="Popup promotion cannot convert or be tracked", fix="Add a destination URL (with UTMs) to the popup.", evidence=ev)
                real = [l for l in links if not re.search(r"close|dismiss|no thanks|later", l.get("text", ""), re.I) and not re.search(r"#\w*$", l["href"]) and l["href"].startswith("http")]
                if links and not real and not pop.get("clickable"):
                    self.add(category="cta", severity="high", title="Popup has no link/destination", page=pth, element=el, current="Only a close control inside the popup", expected="Popup links to a landing page with UTM parameters", impact="Popup promotion cannot convert or be tracked", fix="Add a destination URL (with UTMs) to the popup.", evidence=ev)
                for l in real:
                    href = l["href"]; st = self.ls.get(href) or self.ls.get(href.split("#")[0]) or {}
                    tp = self.pages.get(href) or self.pages.get(href.split("#")[0]) or {}
                    status = st.get("status") if st else tp.get("status")
                    if tp and tp.get("error") and re.search(r"Timeout|TIMED_OUT", tp["error"]):
                        self.add(category="performance", severity="high", title="Popup destination is very slow to load", page=pth, element=el, current=f"{href} timed out", expected="Loads within a few seconds", impact="Popup clicks lead to a page that may never load", fix="Check the destination's performance.", evidence=ev)
                    elif st.get("loop") or (status is not None and (status >= 400 or (status == 0 and st))):
                        if not st.get("loop") and not self._live_broken(href):
                            continue   # re-verified live: it works (earlier failure was transient)
                        self.add(category="cta", severity="critical", title="Popup link is broken", page=pth, element=el, current=f"{status or st.get('error')} {href}", expected="Working destination", impact="Popup sends users to a dead page", fix="Fix the popup URL.", evidence=ev)
                    if href.startswith("http") and "utm_" not in href.lower():
                        self.add(category="tracking", severity="high", title="Popup link has no UTM parameters", page=pth, element=el, current=href, expected="URL with utm_source/utm_medium/utm_campaign", impact="Popup traffic and revenue cannot be attributed", fix="Append UTM parameters to the popup link.", evidence=ev)
                    elif href.startswith("http"):
                        missing = [k for k in ("utm_source", "utm_medium", "utm_campaign") if k not in href.lower()]
                        if missing:
                            self.add(category="tracking", severity="medium", title="Popup link has incomplete UTM parameters", page=pth, element=el, current=href, expected="utm_source, utm_medium and utm_campaign all present", impact="Partial attribution", fix=f"Add {', '.join(missing)}.", evidence=ev)
                for im in pop.get("imgs", []):
                    if im.get("broken"):
                        self.add(category="functionality", severity="high", title="Popup image is broken", page=pth, element=el, current=im["src"], expected="Image renders", impact="Empty popup", fix="Fix the popup image URL.", evidence=ev)
                if not pop.get("has_close") and pop.get("pct", 0) >= 30:
                    self.add(category="visual", severity="high", title="Popup has no close button", page=pth, element=el, current=f"covers {pop['pct']}% of the screen", expected="Closable popup", impact="Blocks content", fix="Add a close control.", evidence=ev)

    # ======================= 12. URL slugs: spelling + content/intent mismatch =======================
    def check_urls(self):
        from spellchecker import SpellChecker
        sp = SpellChecker()
        vocab = Counter()
        for u, p in self.site_pages.items():
            vocab.update(set(re.findall(r"[a-z]{3,}", (p.get("text") or "").lower())))
        site_vocab = {w for w, c in vocab.items() if c >= 2} | self.cfg.spelling_whitelist | {w.lower() for w in re.findall(r"[a-z]+", self.host)}
        for u, p in self.site_pages.items():
            pth = path_of(u); path = urlparse(u).path
            segs = [sg for sg in path.split("/") if sg]
            if not segs: continue
            last = re.sub(r"\.(php|html?|aspx?)$", "", segs[-1])
            words = [w for w in re.split(r"[-_.+%20 ]+", last.lower()) if w.isalpha() and len(w) >= 3]
            if not words: continue
            bad = []
            for w in words:
                if w in site_vocab or w in sp: continue
                corr = sp.correction(w)
                if corr and corr != w:
                    bad.append(f"{w} → {corr}")
            if bad:
                self.add(category="seo", severity="medium", title="URL slug looks misspelled", page=pth, current=path, expected="Correctly spelled slug: " + ", ".join(bad), impact="Unprofessional URL, weak keyword match, hard to share", fix="Rename the slug and 301-redirect the old URL.")
            title = (p.get("title") or "").lower(); h1 = " ".join(h["text"] for h in p.get("headings", []) if h["tag"] == "h1").lower()
            body = (p.get("perf", {}).get("main_text") or p.get("text") or "").lower()
            stop = {"the", "and", "for", "with", "our", "your", "page", "index", "home", "new", "php", "html", "www"}
            keys = [w for w in words if w not in stop and len(w) >= 4]
            if keys:
                in_head = [w for w in keys if w in title or w in h1 or w[:-1] in title or w[:-1] in h1]
                in_body = [w for w in keys if w in body or w[:-1] in body]
                if not in_head and not in_body:
                    self.add(category="content", severity="medium", title="URL does not match the page content", page=pth, current=f"slug words {keys} appear nowhere in the title, H1 or body", expected="URL describes the page topic", impact="Wrong page behind the URL, or copied template", fix="Fix the slug or the page content.", evidence=self.shot(p))
                elif not in_head and len(keys) >= 2:
                    self.add(category="seo", severity="low", title="URL topic missing from title and H1", page=pth, current=f"slug words {keys}; title \"{title[:60]}\"", expected="Title/H1 reflect the URL topic", impact="Weak intent match", fix="Align title/H1 with the URL.")

    # ======================= 13. Contact page: one phone, one email =======================
    def check_contact(self):
        for u, p in self.site_pages.items():
            if not re.search(r"contact|reach-us|get-in-touch", u + " " + (p.get("title") or ""), re.I): continue
            pth = path_of(u)
            phones = {norm_phone(x) for x in p.get("phones", []) if len(norm_phone(x)) == 10}
            emails = {e.lower() for e in p.get("emails", []) if not re.search(r"\.(png|jpg|gif|svg|webp|js|css)$", e) and "example" not in e}
            if len(phones) > 1 and not (self.cfg.locations and len(phones) <= len(self.cfg.locations)):
                self.add(category="consistency", severity="medium", title="Multiple phone numbers on the Contact page", page=pth, current=", ".join(sorted(phones)), expected="One phone number (or one per location, clearly labelled)", impact="Customers do not know which to call", fix="Keep one number, or label each by location.", evidence=self.shot(p))
            if len(emails) > 1 and not (self.cfg.locations and len(emails) <= len(self.cfg.locations)):
                self.add(category="consistency", severity="medium", title="Multiple email addresses on the Contact page", page=pth, current=", ".join(sorted(emails)), expected="One contact email (or one per location)", impact="Enquiries scattered across inboxes", fix="Keep one address, or label each by location.", evidence=self.shot(p))

    # ======================= 10. Performance =======================
    def check_performance(self):
        for u, p in self.ok_pages.items():
            pth = path_of(u); pf = p.get("perf", {})
            lm = p.get("load_ms", 0); ev = self.shot(p)
            if lm > 8000: self.add(category="performance", severity="high", title="Very slow page", page=pth, current=f"{lm/1000:.1f} s to load (network idle)", expected="< 3 s", impact="Visitors leave before it loads", fix="See large assets and render-blocking findings on this page.")
            elif lm > 4500: self.add(category="performance", severity="medium", title="Slow page", page=pth, current=f"{lm/1000:.1f} s to load", expected="< 3 s", impact="Bounce rate", fix="Optimise images and scripts.")
            if pf.get("ttfb", 0) > 1500: self.add(category="performance", severity="medium", title="Slow server response (TTFB)", page=pth, current=f"{pf['ttfb']} ms", expected="< 800 ms", impact="Every visit starts late", fix="Enable caching / check hosting.")
            tb = pf.get("transfer_bytes", 0)
            if tb > 5_000_000: self.add(category="performance", severity="high", title="Excessive page weight", page=pth, current=f"{tb/1e6:.1f} MB transferred", expected="< 2 MB", impact="Slow on mobile data", fix="Compress images/video, remove unused scripts.")
            elif tb > 3_000_000: self.add(category="performance", severity="medium", title="Heavy page", page=pth, current=f"{tb/1e6:.1f} MB transferred", expected="< 2 MB", impact="Slow on mobile", fix="Compress assets.")
            big = sorted([r for r in p.get("resources", []) if r["bytes"] > 500_000 and r["type"] in ("img", "css", "script", "video", "media", "other", "link")], key=lambda r: -r["bytes"])[:5]
            for r in big:
                self.add(category="performance", severity="medium" if r["bytes"] < 2_000_000 else "high", title=f"Large {'image' if r['type']=='img' else r['type']} file", page=pth, element=r["type"], current=f"{r['bytes']//1024} KB {r['url'][:120]}", expected="< 300 KB per image, < 1 MB video poster", impact="Slow load", fix="Compress / convert to WebP / lazy-load.")
            over = [im for im in p.get("images", []) if im["visible"] and im["natural_w"] > 0 and im["rendered_w"] > 0 and im["natural_w"] >= im["rendered_w"] * 2.5 and im["natural_w"] > 1200][:3]
            for im in over:
                self.add(category="performance", severity="low", title="Image much larger than displayed size", page=pth, element=im["sel"], current=f"{im['natural_w']}x{im['natural_h']} shown at {im['rendered_w']}x{im['rendered_h']}: {im['src'][-80:]}", expected="Serve resized images (srcset)", impact="Wasted bandwidth", fix="Resize or use srcset.")
            if pf.get("render_blocking", 0) >= 8: self.add(category="performance", severity="low", title="Many render-blocking resources", page=pth, current=f"{pf['render_blocking']} blocking scripts/stylesheets in <head>", expected="< 5", impact="Delayed first paint", fix="Defer/async scripts, inline critical CSS.")
            if pf.get("resource_count", 0) > 150: self.add(category="performance", severity="low", title="Very many requests", page=pth, current=f"{pf['resource_count']} resources", expected="< 100", impact="Slow load", fix="Bundle and remove unused assets.")
            cls = pf.get("cls", 0) or 0
            if cls > 0.25: self.add(category="performance", severity="high", title="Major layout shift (CLS)", page=pth, current=f"CLS {cls:.2f}", expected="< 0.1", impact="Content jumps; mis-clicks; Core Web Vitals fail", fix="Reserve space for images/ads/embeds.", evidence=ev)
            elif cls > 0.1: self.add(category="performance", severity="medium", title="Layout shift (CLS)", page=pth, current=f"CLS {cls:.2f}", expected="< 0.1", impact="Core Web Vitals", fix="Set width/height on images and embeds.")
            lcp = pf.get("lcp", 0) or 0
            if lcp > 4000: self.add(category="performance", severity="high", title="Slow Largest Contentful Paint", page=pth, current=f"LCP {lcp/1000:.1f} s", expected="< 2.5 s", impact="Core Web Vitals fail", fix="Optimise the hero image/video and server response.")
            elif lcp > 2500: self.add(category="performance", severity="medium", title="LCP needs improvement", page=pth, current=f"LCP {lcp/1000:.1f} s", expected="< 2.5 s", impact="Core Web Vitals", fix="Optimise hero media.")
            if pf.get("html_len", 0) > 1_500_000: self.add(category="performance", severity="medium", title="Huge HTML document", page=pth, current=f"{pf['html_len']//1024} KB of HTML", expected="< 300 KB", impact="Slow parse", fix="Remove inline data/scripts.")
