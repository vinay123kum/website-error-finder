"""Playwright crawler: loads every page, captures errors, tests buttons/forms safely, shoots 3 viewports."""
from __future__ import annotations
import hashlib, json, re, time, sys
from collections import deque
from pathlib import Path
from urllib.parse import urlparse, urljoin, urldefrag
import httpx
from playwright.sync_api import sync_playwright, TimeoutError as PWTimeout

from .models import PageData
from .siteconfig import SiteConfig
from . import extract_js as js

VIEWPORTS = {"desktop": (1440, 900), "tablet": (768, 1024), "mobile": (390, 844)}
SKIP_EXT = re.compile(r"\.(pdf|jpe?g|png|gif|webp|svg|ico|zip|rar|mp4|mp3|avi|mov|docx?|xlsx?|pptx?|css|js|xml|json|txt|woff2?|ttf)(\?|$)", re.I)
TRACKING_HOSTS = ("google-analytics.com", "analytics.google.com", "googletagmanager.com", "facebook.com/tr", "doubleclick.net",
                  "linkedin.com/px", "px.ads.linkedin.com", "bat.bing.com", "clarity.ms", "hotjar.com", "/g/collect", "/collect?")
CTA_LINK_RE = re.compile(r"(book|buy|tickets?|reserve|schedule|register|waiver|party|pass(es)?|order|shop|checkout|sign ?up|gift ?card|membership|camp)", re.I)
DESTRUCTIVE_BTN = re.compile(r"delete|remove|cancel|pay|checkout|purchase|unsubscribe|logout|log out|sign out|submit|send|buy now|confirm|place order", re.I)


def host_key(url: str) -> str:
    h = (urlparse(url).hostname or "").lower()
    return h[4:] if h.startswith("www.") else h


def norm_url(url: str) -> str:
    url, _ = urldefrag(url)
    p = urlparse(url)
    path = p.path or "/"
    if path != "/" and path.endswith("/"):
        path = path  # keep trailing slash as-is: both forms tracked separately for canonical checks
    q = p.query
    # drop common tracking params
    if q:
        keep = [kv for kv in q.split("&") if not re.match(r"^(utm_|fbclid|gclid|ref=|mc_)", kv)]
        q = "&".join(keep)
    return f"{p.scheme}://{p.netloc}{path}" + (f"?{q}" if q else "")


class Crawler:
    def __init__(self, start_url: str, config: SiteConfig, out_dir: Path, max_pages: int = 100,
                 headless: bool = True, test_forms: bool = True, test_buttons: bool = True, log=print):
        self.start_url = start_url if start_url.startswith("http") else "https://" + start_url
        self.config = config
        self.out = Path(out_dir); (self.out / "shots").mkdir(parents=True, exist_ok=True)
        self.max_pages = max_pages
        self.headless = headless
        self.test_forms = test_forms
        self.test_buttons = test_buttons
        self.log = log
        self.host = host_key(self.start_url)
        self.pages: dict[str, PageData] = {}
        self.link_status: dict[str, dict] = {}   # url -> {status, final, error, redirects, loop}
        self.robots = ""; self.sitemap_urls: set[str] = set(); self.sitemap_error = ""
        self.tracking_hits: list = []
        self.external_counts: dict[str, int] = {}

    # ---------- helpers ----------
    def is_internal(self, url: str) -> bool:
        h = host_key(url)
        if not h: return False
        if h == self.host or h.endswith("." + self.host): return True   # subdomains (onlinestore.columbia.site.com)
        return any(h == x or h.endswith("." + x) for x in self.config.extra_hosts)

    def is_external_landing(self, url: str, text: str) -> bool:
        """External CTA destination (e.g. ecom.roller.app) worth crawling one level deep."""
        if not self.config.follow_external_ctas or not url.startswith("http") or self.is_internal(url): return False
        h = host_key(url)
        if any(b in h for b in ("facebook.", "instagram.", "twitter.", "x.com", "linkedin.", "youtube.", "tiktok.", "google.", "goo.gl", "apple.com", "yelp.", "pinterest.", "maps.", "wa.me", "whatsapp.")): return False
        if SKIP_EXT.search(url): return False
        return bool(CTA_LINK_RE.search(text or "")) or "roller" in h or "book" in h or "store" in h


    def should_crawl(self, url: str) -> bool:
        if not url.startswith("http") or not self.is_internal(url) or SKIP_EXT.search(url):
            return False
        return not any(pat in url for pat in self.config.exclude)

    def shot_path(self, url: str, vp: str) -> Path:
        h = hashlib.md5(url.encode()).hexdigest()[:10]
        return self.out / "shots" / f"{h}_{vp}.png"

    # ---------- main ----------
    def run(self):
        self.fetch_robots_sitemap()
        with sync_playwright() as p:
            browser = p.chromium.launch(headless=self.headless)
            ctx = browser.new_context(viewport={"width": 1440, "height": 900}, ignore_https_errors=True,
                                      user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/125.0 Safari/537.36 WEF-QA-Bot/0.1")
            ctx.set_default_timeout(20000)
            queue = deque([(norm_url(self.start_url), 0, "")])
            seen = {norm_url(self.start_url)}
            while queue and len(self.pages) < self.max_pages:
                url, depth, found_on = queue.popleft()
                self.log(f"[{len(self.pages)+1}/{self.max_pages}] {url}")
                pd = self.crawl_page(ctx, url, depth, found_on)
                self.pages[url] = pd
                if pd.final_url and norm_url(pd.final_url) != url and self.is_internal(pd.final_url):
                    seen.add(norm_url(pd.final_url))
                for l in pd.links:
                    u = norm_url(l["href"])
                    if u in seen: continue
                    if self.should_crawl(u):
                        seen.add(u); queue.append((u, depth + 1, url))
                    elif not pd.is_external_landing and self.is_external_landing(u, l.get("text", "")):
                        hk = host_key(u)
                        if self.external_counts.get(hk, 0) < self.config.external_page_cap:
                            self.external_counts[hk] = self.external_counts.get(hk, 0) + 1
                            seen.add(u); queue.append((u, depth + 1, url))
            browser.close()
        self.check_links()
        self.capture_link_evidence()
        return self

    def fetch_robots_sitemap(self):
        base = f"{urlparse(self.start_url).scheme}://{urlparse(self.start_url).netloc}"
        sitemaps = [base + "/sitemap.xml", base + "/sitemap_index.xml", base + "/wp-sitemap.xml"]
        try:
            r = httpx.get(base + "/robots.txt", timeout=15, follow_redirects=True)
            if r.status_code == 200 and "<html" not in r.text[:300].lower():
                self.robots = r.text
                sitemaps = [l.split(":", 1)[1].strip() for l in r.text.splitlines() if l.lower().startswith("sitemap:")] + sitemaps
        except Exception as e:
            self.robots = ""
        seen = set()
        def load(sm, depth=0):
            if sm in seen or depth > 3: return
            seen.add(sm)
            try:
                r = httpx.get(sm, timeout=20, follow_redirects=True)
                if r.status_code != 200: return
                locs = re.findall(r"<loc>\s*([^<]+?)\s*</loc>", r.text)
                if "<sitemapindex" in r.text:
                    for l in locs: load(l.strip(), depth + 1)
                else:
                    self.sitemap_urls |= {norm_url(l.strip()) for l in locs}
            except Exception as e:
                self.sitemap_error = str(e)
        for sm in sitemaps:
            load(sm)
            if self.sitemap_urls: break

    # ---------- per page ----------
    def crawl_page(self, ctx, url: str, depth: int, found_on: str) -> PageData:
        pd = PageData(url=url, depth=depth, found_on=found_on)
        pd.is_external_landing = not self.is_internal(url)
        page = ctx.new_page()
        page.add_init_script(js.INSTALL_OBSERVERS)
        bad, failed, console, navs = [], [], [], []
        loaded = {"done": False}
        page.on("console", lambda m: console.append({"type": m.type, "text": m.text[:300]}) if m.type in ("error",) else None)
        page.on("pageerror", lambda e: console.append({"type": "pageerror", "text": str(e)[:300]}))
        page.on("requestfailed", lambda r: failed.append({"url": r.url[:300], "type": r.resource_type, "error": (r.failure or "")[:120]}) if r.resource_type != "ping" else None)
        page.on("response", lambda r: bad.append({"url": r.url[:300], "status": r.status, "type": r.request.resource_type}) if r.status >= 400 else None)
        page.on("framenavigated", lambda f: navs.append({"url": f.url, "t": time.time()}) if f == page.main_frame and loaded["done"] else None)
        t0 = time.time()
        goto_timeout = 60000 if pd.is_external_landing else 30000
        try:
            try:
                resp = page.goto(url, wait_until="domcontentloaded", timeout=goto_timeout)
            except Exception as first:
                if re.search(r"Timeout|ERR_CONNECTION_TIMED_OUT|ERR_CONNECTION_RESET|ERR_NETWORK_CHANGED|ERR_EMPTY_RESPONSE", str(first)):
                    page.wait_for_timeout(3000)
                    resp = page.goto(url, wait_until="domcontentloaded", timeout=goto_timeout + 15000)   # one retry
                else:
                    raise
            try: page.wait_for_load_state("networkidle", timeout=12000)
            except PWTimeout: pass
            page.wait_for_timeout(600)
            pd.load_ms = int((time.time() - t0) * 1000)
            if resp is None:
                pd.error = "no response"; pd.status = 0
            else:
                pd.status = resp.status; pd.final_url = page.url
                chain = []; req = resp.request
                while req.redirected_from:
                    req = req.redirected_from; chain.insert(0, req.url)
                pd.redirect_chain = chain + ([page.url] if chain else [])
                pd.ok = resp.status < 400
            if pd.status == 0 and not pd.error:
                pd.error = "blocked/no status"
        except Exception as e:
            pd.error = str(e).splitlines()[0][:200]; pd.status = 0
            page.close(); return pd
        loaded["done"] = True
        ctype = (resp.headers.get("content-type", "") if resp else "")
        if "text/html" not in ctype and ctype:
            pd.error = f"non-html: {ctype}"; page.close(); return pd
        try:
            page.wait_for_timeout(1200)
            pd.popups = page.evaluate(js.POPUPS)
            if any(x.get("kind") == "consent" for x in pd.popups if isinstance(x, dict)):
                clicked = page.evaluate(js.DISMISS_CONSENT)   # clear cookie banners so screenshots/metrics show the real page
                if clicked: page.wait_for_timeout(800)
        except Exception as e:
            pd.popups = {"error": str(e)[:100]}
        # scroll to trigger lazy loading
        try:
            page.evaluate("async () => { for (let y = 0; y < Math.min(document.body.scrollHeight, 12000); y += 700) { scrollTo(0, y); await new Promise(r => setTimeout(r, 120)); } scrollTo(0,0); }")
            page.wait_for_timeout(800)
        except Exception: pass
        # detect popup/auto redirection: navigations after load without interaction
        page.wait_for_timeout(1500)
        pd.unexpected_navigations = [n for n in navs if norm_url(n["url"]) != norm_url(page.url) or n["url"] != page.url]
        try:
            data = page.evaluate(js.EXTRACT_PAGE)
        except Exception as e:
            pd.error = f"extract failed: {str(e)[:150]}"; page.close(); return pd
        for k in ("title", "meta", "headings", "links", "images", "media", "forms", "buttons", "scripts", "jsonld", "tracking", "sliders", "nav", "phones", "emails", "perf", "resources"):
            setattr(pd, k, data[k])
        pd.text = data["text"]; pd.html = ""  # html not stored (size); main_text used for content checks
        pd.perf["word_count"] = data["word_count"]; pd.perf["html_len"] = data["html_len"]
        pd.perf["main_text"] = data["main_text"]
        pd.content_hash = hashlib.md5(re.sub(r"\d", "", data["main_text"].lower()).encode()).hexdigest()
        pd.console_errors = console[:40]; pd.failed_requests = failed[:60]; pd.bad_responses = bad[:60]
        # viewports
        for vp, (w, h) in VIEWPORTS.items():
            try:
                page.set_viewport_size({"width": w, "height": h}); page.wait_for_timeout(500)
                m = page.evaluate(js.VIEWPORT_METRICS)
                if vp == "mobile" and pd.nav.get("hamburger_sel"):
                    try:
                        m["hamburger"] = page.evaluate(js.HAMBURGER_TEST, pd.nav["hamburger_sel"])
                        page.keyboard.press("Escape")
                        try: page.evaluate(js.HAMBURGER_TEST, pd.nav["hamburger_sel"])  # toggle back
                        except Exception: pass
                    except Exception as e:
                        m["hamburger"] = {"found": False, "error": str(e)[:100]}
                pd.viewports[vp] = m
                sp = self.shot_path(url, vp)
                try:
                    page.screenshot(path=str(sp), full_page=True, timeout=20000)
                except Exception:
                    page.screenshot(path=str(sp), full_page=False, timeout=15000)   # huge/slow page: viewport-only capture
                pd.screenshots[vp] = str(sp.relative_to(self.out)).replace("\\", "/")
            except Exception as e:
                pd.viewports[vp] = {"error": str(e)[:150]}
        try: page.set_viewport_size({"width": 1440, "height": 900}); page.wait_for_timeout(300)
        except Exception: pass
        # interaction tests
        if self.test_buttons:
            try: pd.button_tests = self.run_button_tests(page, pd)
            except Exception as e: pd.button_tests = [{"error": str(e)[:150]}]
        if self.test_forms and pd.forms:
            try: pd.form_tests = self.run_form_tests(page, pd)
            except Exception as e: pd.form_tests = [{"error": str(e)[:150]}]
        page.close()
        return pd

    # ---------- buttons ----------
    def run_button_tests(self, page, pd: PageData) -> list:
        results = []
        cands = [b for b in pd.buttons if not b["href"] and not b["in_form"] and not b["in_slider"] and not b["disabled"]
                 and b["text"] and not DESTRUCTIVE_BTN.search(b["text"]) and not b.get("is_submit")][:12]
        sel = "button, [role=button], input[type=button], .btn, .button, a.btn, a.button"
        start_url = page.url
        for b in cands:
            tracking = []
            handler = lambda r: tracking.append(r.url[:120]) if any(t in r.url for t in TRACKING_HOSTS) else None
            page.on("request", handler)
            try:
                before = page.evaluate(js.DOM_SIGNATURE)
                clicked = page.evaluate("""([sel, i, text]) => { const els = Array.from(document.querySelectorAll(sel)).filter(e => { const r = e.getBoundingClientRect(); return r.width>0 && r.height>0; }); const el = els[i] && (els[i].innerText||els[i].value||'').trim().slice(0,100) === text ? els[i] : els.find(e => (e.innerText||e.value||'').trim().slice(0,100) === text); if (!el) return false; el.scrollIntoView({block:'center'}); el.click(); return true; }""", [sel, b["i"], b["text"]])
                if not clicked:
                    continue
                page.wait_for_timeout(900)
                popup_opened = len(page.context.pages) > 1
                for extra in page.context.pages[1:]:
                    try: extra.close()
                    except Exception: pass
                after = page.evaluate(js.DOM_SIGNATURE)
                changed = (after["url"] != before["url"] or after["n"] != before["n"] or after["vis"] != before["vis"]
                           or abs(after["scroll"] - before["scroll"]) > 50 or after["bodyClass"] != before["bodyClass"] or popup_opened)
                results.append({"text": b["text"], "sel": b["sel"], "changed": changed, "navigated_to": after["url"] if after["url"] != before["url"] else "",
                                "popup": popup_opened, "tracking_events": len(tracking), "has_handler": b["has_onclick"]})
                if after["url"] != before["url"]:
                    page.goto(start_url, wait_until="domcontentloaded"); page.wait_for_timeout(800)
                else:
                    page.keyboard.press("Escape"); page.wait_for_timeout(200)
            except Exception as e:
                results.append({"text": b["text"], "sel": b["sel"], "error": str(e)[:120]})
                try: page.goto(start_url, wait_until="domcontentloaded")
                except Exception: pass
            finally:
                page.remove_listener("request", handler)
        return results

    # ---------- forms (safe mode: every non-GET request is aborted) ----------
    def run_form_tests(self, page, pd: PageData) -> list:
        results = []
        forms = [f for f in pd.forms if f["visible"] and f["fields"] and not any(k in (f["id"] + f["name"] + f["sel"] + f["action"]).lower() for k in ("search", "newsletter-search", "login", "cart"))][:2]
        if not forms: return results
        start_url = page.url
        attempted = []
        def route_handler(route, request):
            is_sub = request.method != "GET" or (request.is_navigation_request() and request.url != start_url and "?" in request.url)
            if is_sub and request.resource_type in ("document", "fetch", "xhr", "other") and not any(t in request.url for t in TRACKING_HOSTS) and not re.search(r"google\.com/ccm|/collect|/pagead/|analytics|clarity|hotjar|tidio|intercom|facebook", request.url):
                attempted.append({"url": request.url[:200], "method": request.method})
                return route.abort()
            return route.continue_()
        page.route("**/*", route_handler)
        try:
            for f in forms:
                r = {"sel": f["sel"], "action": f["action"], "method": f["method"], "fields": len(f["fields"]), "required": sum(1 for x in f["fields"] if x["required"]),
                     "submit_present": bool(f["submits"]), "submit_disabled": all(s["disabled"] for s in f["submits"]) if f["submits"] else False}
                if not f["submits"]:
                    results.append(r); continue
                # 1) empty submit: validation expected if required fields exist
                attempted.clear()
                page.evaluate("""(idx) => { const f = document.querySelectorAll('form')[idx]; const b = f.querySelector('button[type=submit],input[type=submit],button:not([type])'); if (b) { b.scrollIntoView({block:'center'}); b.click(); } }""", f["idx"])
                page.wait_for_timeout(1200)
                r["empty_submit_request"] = len(attempted)
                r["empty_invalid_count"] = page.evaluate("(idx) => { const f = document.querySelectorAll('form')[idx]; return f ? f.querySelectorAll(':invalid').length + f.querySelectorAll('[class*=error]:not([style*=\"display: none\"]), [aria-invalid=true], .wpcf7-not-valid, .invalid-feedback, .error-message').length : -1; }", f["idx"])
                page.goto(start_url, wait_until="domcontentloaded"); page.wait_for_timeout(800)
                # 2) filled submit
                attempted.clear()
                fill_res = page.evaluate("""(idx) => { const f = document.querySelectorAll('form')[idx]; if (!f) return 'no form'; let filled = 0;
                    for (const e of f.querySelectorAll('input,select,textarea')) { if (['hidden','submit','button','image','reset'].includes(e.type) || e.disabled) continue; const r = e.getBoundingClientRect(); if (r.width === 0) continue;
                      const set = v => { const proto = e.tagName === 'TEXTAREA' ? HTMLTextAreaElement.prototype : HTMLInputElement.prototype; const d = Object.getOwnPropertyDescriptor(proto, 'value'); if (d && d.set) d.set.call(e, v); else e.value = v; e.dispatchEvent(new Event('input', {bubbles:true})); e.dispatchEvent(new Event('change', {bubbles:true})); filled++; };
                      const n = (e.name + ' ' + e.id + ' ' + e.placeholder + ' ' + (e.getAttribute('aria-label')||'')).toLowerCase();
                      if (e.tagName === 'SELECT') { if (e.options.length > 1) { e.selectedIndex = 1; e.dispatchEvent(new Event('change', {bubbles:true})); filled++; } }
                      else if (e.type === 'checkbox' || e.type === 'radio') { e.checked = true; e.dispatchEvent(new Event('change', {bubbles:true})); filled++; }
                      else if (e.type === 'email' || /mail/.test(n)) set('qa-test@example.com');
                      else if (e.type === 'tel' || /phone|tel|mobile/.test(n)) set('5555550199');
                      else if (e.type === 'number' || /zip|postal/.test(n)) set('12345');
                      else if (e.type === 'date') set('2030-01-15');
                      else if (e.type === 'url') set('https://example.com');
                      else if (/first/.test(n)) set('QA');
                      else if (/last|surname/.test(n)) set('Test');
                      else if (/name/.test(n)) set('QA Test');
                      else if (e.tagName === 'TEXTAREA' || /message|comment/.test(n)) set('QA TEST - automated website check, please ignore.');
                      else set('QA Test'); }
                    return filled; }""", f["idx"])
                page.evaluate("""(idx) => { const f = document.querySelectorAll('form')[idx]; const b = f.querySelector('button[type=submit],input[type=submit],button:not([type])'); if (b) { b.scrollIntoView({block:'center'}); b.click(); } }""", f["idx"])
                page.wait_for_timeout(2000)
                r["filled"] = fill_res
                r["filled_submit_request"] = len(attempted)
                r["filled_invalid_count"] = page.evaluate("(idx) => { const f = document.querySelectorAll('form')[idx]; return f ? f.querySelectorAll(':invalid').length : -1; }", f["idx"])
                r["request_target"] = attempted[0]["url"] if attempted else ""
                r["captcha"] = page.evaluate("() => !!document.querySelector('.g-recaptcha, [data-sitekey], iframe[src*=recaptcha], iframe[src*=hcaptcha], .cf-turnstile, iframe[src*=turnstile]')")
                r["error_text"] = page.evaluate("(idx) => { const f = document.querySelectorAll('form')[idx]; const el = f && Array.from(f.querySelectorAll('[class*=error],[class*=invalid],[role=alert]')).find(e => e.getBoundingClientRect().height > 0 && e.innerText.trim()); return el ? el.innerText.trim().slice(0,120) : ''; }", f["idx"])
                results.append(r)
                page.goto(start_url, wait_until="domcontentloaded"); page.wait_for_timeout(800)
        finally:
            try: page.unroute("**/*", route_handler)
            except Exception: pass
        return results

    # ---------- link checking (httpx) ----------
    def check_links(self):
        urls = {}
        for pd in self.pages.values():
            for l in pd.links:
                h = l["href"]
                if not h.startswith("http") or SKIP_EXT.search(h) and self.is_internal(h) and False: continue
                if not h.startswith("http"): continue
                u = norm_url(h) if self.is_internal(h) else h.split("#")[0]
                if u in self.pages: continue
                urls.setdefault(u, 0); urls[u] += 1
            for im in pd.images:
                if im["src"].startswith("http") and (im["natural_w"] == 0 and im["complete"]):
                    urls.setdefault(im["src"], 0)
            for m in pd.media:
                if m["src"].startswith("http"): urls.setdefault(m["src"], 0)
        internal = [u for u in urls if self.is_internal(u)]
        external = sorted([u for u in urls if not self.is_internal(u)], key=lambda u: -urls[u])[:400]
        self.log(f"Checking {len(internal)} internal + {len(external)} external links...")
        client = httpx.Client(timeout=15, follow_redirects=False, headers={"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/125.0 Safari/537.36"}, verify=False)
        for u in internal + external:
            self.link_status[u] = self._check(client, u)
        client.close()
        self.browser_verify_external()

    def browser_verify_external(self):
        """Many sites (Facebook, Google Maps, LinkedIn, Cloudflare-protected) reject plain HTTP checks. Re-open failures in the real browser."""
        sus = [u for u, st in self.link_status.items() if (not self.is_internal(u) and (st.get("status") in (0, 400, 401, 403, 405, 406, 429, 503, 999) or st.get("loop"))) or (self.is_internal(u) and st.get("status") in (0, 403, 429, 503))]
        if not sus: return
        self.log(f"Re-verifying {len(sus)} external links in the browser...")
        with sync_playwright() as p:
            browser = p.chromium.launch(headless=self.headless)
            ctx = browser.new_context(ignore_https_errors=True, user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/125.0 Safari/537.36")
            for u in sus[:150]:
                page = ctx.new_page()
                try:
                    resp = page.goto(u, wait_until="domcontentloaded", timeout=30000)
                    page.wait_for_timeout(800)
                    code = resp.status if resp else 0
                    final = page.url
                    body = (page.evaluate("() => (document.body && document.body.innerText || '').slice(0, 600)") or "").lower()
                    not_found = code in (404, 410) or ("page not found" in body or "this page isn't available" in body or "content isn't available" in body or "sorry, this page" in body)
                    if code and code < 400 and not not_found:
                        self.link_status[u] = {"status": code, "final": final, "error": "", "redirects": 0, "loop": False, "chain": [], "verified": "browser"}
                    elif code and code < 400 and not_found:
                        self.link_status[u] = {"status": 404, "final": final, "error": "page reports not found", "redirects": 0, "loop": False, "chain": [], "verified": "browser"}
                    elif code == 0:
                        self.link_status[u]["error"] = "unreachable in browser too"; self.link_status[u]["verified"] = "browser"
                    else:
                        self.link_status[u]["status"] = code; self.link_status[u]["verified"] = "browser"
                except Exception as e:
                    self.link_status[u]["error"] = f"browser: {type(e).__name__}"; self.link_status[u]["verified"] = "browser"
                finally:
                    page.close()
            browser.close()

    def _check(self, client, url):
        res = {"status": 0, "final": url, "error": "", "redirects": 0, "loop": False, "chain": []}
        seen = []; cur = url
        for _ in range(10):
            try:
                r = client.head(cur)
                if r.status_code in (405, 403, 400, 404, 501) or r.status_code >= 500:
                    r = client.get(cur)
            except Exception as e:
                res["error"] = type(e).__name__; res["status"] = 0; return res
            res["status"] = r.status_code
            if 300 <= r.status_code < 400 and r.headers.get("location"):
                nxt = urljoin(cur, r.headers["location"])
                res["chain"].append(cur)
                if nxt in seen or nxt == cur:
                    res["loop"] = True; res["final"] = nxt; return res
                seen.append(cur); cur = nxt; res["redirects"] += 1; continue
            res["final"] = cur
            return res
        res["loop"] = res["redirects"] >= 10
        return res

    # ---------- element screenshots for broken links ----------
    def is_broken_href(self, href: str) -> bool:
        st = self.link_status.get(href) or self.link_status.get(href.split("#")[0])
        if st: return bool(st.get("loop")) or st.get("status", 1) >= 400 or st.get("status") == 0
        tp = self.pages.get(norm_url(href)) if href.startswith("http") else None
        if tp: return tp.status >= 400 or bool(tp.error and not tp.error.startswith("non-html") and not re.search(r"Timeout|TIMED_OUT", tp.error))
        return False

    def capture_link_evidence(self):
        """Reopen pages that contain broken links; outline each dead link in red and save a close-up screenshot."""
        self.link_evidence: dict[str, dict[str, str]] = {}
        targets = []
        for u, pd in self.pages.items():
            hrefs = sorted({l["href"] for l in pd.links if l["href"].startswith("http") and self.is_broken_href(l["href"])})
            if hrefs: targets.append((u, hrefs[:12]))
        if not targets: return
        self.log(f"Capturing screenshots of broken links on {len(targets)} pages...")
        with sync_playwright() as p:
            browser = p.chromium.launch(headless=self.headless)
            ctx = browser.new_context(viewport={"width": 1440, "height": 900}, ignore_https_errors=True)
            ctx.set_default_timeout(20000)
            for u, hrefs in targets[:40]:
                page = ctx.new_page()
                try:
                    page.goto(u, wait_until="domcontentloaded", timeout=45000)
                    try: page.wait_for_load_state("networkidle", timeout=10000)
                    except Exception: pass
                    page.wait_for_timeout(800)
                    try:
                        page.evaluate(js.DISMISS_CONSENT); page.evaluate(js.CLEAR_OVERLAYS); page.wait_for_timeout(400)
                    except Exception: pass
                    for href in hrefs:
                        try:
                            box = page.evaluate("""(href) => { const els = Array.from(document.querySelectorAll('a[href]')).filter(a => a.href === href || a.href.split('#')[0] === href.split('#')[0]);
                                const el = els.find(a => { const r = a.getBoundingClientRect(); return r.width > 0 && r.height > 0; }) || els[0]; if (!el) return null;
                                el.scrollIntoView({block: 'center', inline: 'center'}); el.style.outline = '4px solid #dc2626'; el.style.outlineOffset = '3px'; el.style.boxShadow = '0 0 0 6px rgba(220,38,38,.25)';
                                const r = el.getBoundingClientRect(); return {x: r.left, y: r.top, w: r.width, h: r.height}; }""", href)
                            if not box: continue
                            page.wait_for_timeout(300)
                            vw, vh = 1440, 900
                            pad = 160
                            x = max(0, box["x"] - pad); y = max(0, box["y"] - pad)
                            w = min(vw - x, box["w"] + 2 * pad); h = min(vh - y, box["h"] + 2 * pad)
                            name = f"link_{hashlib.md5((u + '|' + href).encode()).hexdigest()[:12]}.png"
                            sp = self.out / "shots" / name
                            page.screenshot(path=str(sp), clip={"x": x, "y": y, "width": max(w, 200), "height": max(h, 120)})
                            self.link_evidence.setdefault(u, {})[href] = "shots/" + name
                            page.evaluate("""(href) => { for (const a of document.querySelectorAll('a[href]')) if (a.href === href) { a.style.outline = ''; a.style.boxShadow = ''; } }""", href)
                        except Exception:
                            continue
                except Exception:
                    pass
                finally:
                    page.close()
            browser.close()

    # ---------- persistence ----------
    def save(self, path: Path):
        data = {"start_url": self.start_url, "host": self.host, "robots": self.robots, "sitemap_urls": sorted(self.sitemap_urls),
                "sitemap_error": self.sitemap_error, "link_status": self.link_status, "link_evidence": getattr(self, "link_evidence", {}),
                "pages": {u: {k: v for k, v in pd.__dict__.items() if k not in ("html",)} for u, pd in self.pages.items()}}
        Path(path).write_text(json.dumps(data, ensure_ascii=False), "utf-8")
        return data
