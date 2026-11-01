"""Browser-side extraction scripts (run inside the page)."""

EXTRACT_PAGE = r"""
() => {
  const q = (s, r=document) => Array.from(r.querySelectorAll(s));
  const txt = el => (el.innerText || el.textContent || '').replace(/\s+/g,' ').trim();
  const region = el => {
    if (el.closest('header, [role=banner], nav, [role=navigation], .navbar, .nav, #nav, #header, .header, [class*=header], [class*=navbar], [class*=main-menu], [class*=menu-wrap], [class*=topbar], [class*=top-bar]')) return 'nav';
    if (el.closest('footer, [role=contentinfo], .footer, #footer, [class*=footer]')) return 'footer';
    if (el.closest('.breadcrumb, .breadcrumbs, [aria-label*=readcrumb i]')) return 'breadcrumb';
    return 'main';
  };
  const vis = el => { const r = el.getBoundingClientRect(); const cs = getComputedStyle(el);
    return r.width > 0 && r.height > 0 && cs.visibility !== 'hidden' && cs.display !== 'none'; };
  const selPath = el => {
    if (el.id) return '#' + el.id;
    let s = el.tagName.toLowerCase();
    if (el.className && typeof el.className === 'string') s += '.' + el.className.trim().split(/\s+/).slice(0,2).join('.');
    return s;
  };
  const meta = {};
  q('meta').forEach(m => { const k = m.getAttribute('name') || m.getAttribute('property'); if (k) meta[k.toLowerCase()] = m.getAttribute('content') || ''; });
  const canon = document.querySelector('link[rel=canonical]');
  meta['canonical'] = canon ? canon.href : '';
  meta['lang'] = document.documentElement.lang || '';
  meta['viewport'] = (document.querySelector('meta[name=viewport]')||{}).content || '';

  const headings = q('h1,h2,h3,h4,h5,h6').map(h => ({tag: h.tagName.toLowerCase(), text: txt(h).slice(0,200)}));

  const links = q('a[href]').map(a => ({
    href: a.href, raw: a.getAttribute('href'), text: txt(a).slice(0,150) || (a.getAttribute('aria-label')||'') || (a.querySelector('img') ? ('[img:' + (a.querySelector('img').alt||'') + ']') : ''),
    region: region(a), rel: a.rel || '', target: a.target || '', visible: vis(a), sel: selPath(a),
    is_logo: !!(a.closest('.logo, #logo, .brand, .navbar-brand, [class*=logo]') || (a.querySelector('img') && /logo/i.test(a.querySelector('img').src + ' ' + (a.querySelector('img').alt||'') + ' ' + (a.querySelector('img').className||''))))
  }));

  const images = q('img').map(i => { const r = i.getBoundingClientRect(); return {
    src: i.currentSrc || i.src, alt: i.getAttribute('alt'), natural_w: i.naturalWidth, natural_h: i.naturalHeight,
    rendered_w: Math.round(r.width), rendered_h: Math.round(r.height), complete: i.complete, loading: i.loading || '',
    visible: vis(i), sel: selPath(i), in_slider: !!i.closest('.slick-slide,.swiper-slide,.carousel-item,.splide__slide,.owl-item,[class*=slide]')
  };});

  const media = [];
  q('iframe').forEach(f => media.push({type:'iframe', src: f.src, visible: vis(f), title: f.title||''}));
  q('video').forEach(v => media.push({type:'video', src: v.currentSrc || v.src || (v.querySelector('source')||{}).src || '', visible: vis(v), ready: v.readyState, error: v.error ? v.error.code : 0, poster: v.poster||''}));
  q('audio').forEach(v => media.push({type:'audio', src: v.currentSrc || v.src || '', visible: vis(v), ready: v.readyState, error: v.error ? v.error.code : 0}));

  const forms = q('form').map((f, idx) => ({
    idx, action: f.action || '', method: (f.method||'get').toLowerCase(), id: f.id||'', name: f.getAttribute('name')||'', sel: selPath(f),
    visible: vis(f),
    fields: q('input,select,textarea', f).filter(e => !['hidden','submit','button','image','reset'].includes(e.type)).map(e => ({
      tag: e.tagName.toLowerCase(), type: e.type||'', name: e.name||'', id: e.id||'', required: e.required, placeholder: e.placeholder||'',
      options: e.tagName === 'SELECT' ? e.options.length : 0, visible: vis(e), disabled: e.disabled })),
    submits: q('button[type=submit],input[type=submit],button:not([type])', f).map(b => ({text: txt(b)||b.value||'', disabled: b.disabled, visible: vis(b)})),
    hidden_fields: q('input[type=hidden]', f).map(e => e.name)
  }));

  const buttons = q('button, [role=button], input[type=button], .btn, .button, a.btn, a.button').filter(vis).map((b,i) => ({
    i, text: (txt(b) || b.value || b.getAttribute('aria-label') || '').slice(0,100), tag: b.tagName.toLowerCase(), type: b.type||'',
    href: b.href || '', has_onclick: !!b.onclick || b.hasAttribute('onclick') || b.hasAttribute('data-toggle') || b.hasAttribute('data-bs-toggle') || b.hasAttribute('data-target') || b.hasAttribute('data-open') || b.hasAttribute('aria-controls') || b.hasAttribute('data-modal') || b.hasAttribute('data-action'),
    in_form: !!b.closest('form'), is_submit: !!b.closest('form') && (b.type||'submit') === 'submit', in_slider: !!b.closest('[class*=slick],[class*=swiper],[class*=carousel],[class*=slider]'), sel: selPath(b), disabled: b.disabled
  }));

  const scripts = q('script[src]').map(s => s.src);
  const jsonld = q('script[type="application/ld+json"]').map(s => s.textContent);
  const html = document.documentElement.outerHTML;
  const gtmIds = Array.from(new Set((html.match(/GTM-[A-Z0-9]{4,}/g)||[])));
  const ga4Ids = Array.from(new Set((html.match(/G-[A-Z0-9]{6,}/g)||[])));
  const uaIds = Array.from(new Set((html.match(/UA-\d{4,}-\d+/g)||[])));
  const gtagLoads = q('script[src*="googletagmanager.com/gtag/js"]').length;
  const gtmLoads = q('script[src*="googletagmanager.com/gtm.js"]').length;
  const tracking = { gtm_ids: gtmIds, ga4_ids: ga4Ids, ua_ids: uaIds, gtag_loads: gtagLoads, gtm_loads: gtmLoads,
    has_dataLayer: typeof window.dataLayer !== 'undefined', has_gtag: typeof window.gtag === 'function',
    fb_pixel: /connect\.facebook\.net\/[a-z_]+\/fbevents\.js|fbq\(/.test(html), fb_pixel_loads: q('script[src*="fbevents.js"]').length,
    hotjar: /static\.hotjar\.com/.test(html), clarity: /clarity\.ms/.test(html), linkedin: /snap\.licdn\.com/.test(html),
    chat_widgets: ['intercom','drift','tawk','crisp','hubspot','zendesk','tidio','livechat','freshchat','olark'].filter(w => new RegExp(w,'i').test(scripts.join(' ')))
  };

  const sliders = q('.slick-slider,.swiper,.carousel,.splide,.owl-carousel,[class*=slider]:not(input)').filter(vis).map(s => ({
    sel: selPath(s), slides: q('.slick-slide,.swiper-slide,.carousel-item,.splide__slide,.owl-item,[class*=slide]', s).length,
    images: q('img', s).length, broken_images: q('img', s).filter(i => i.complete && i.naturalWidth === 0).length,
    has_controls: !!s.querySelector('[class*=arrow],[class*=prev],[class*=next],[class*=dots],[class*=pagination],button')
  }));

  const logo = links.find(l => l.is_logo && l.region === 'nav') || links.find(l => l.is_logo);
  const anchorsOnPage = links.filter(l => (l.raw||'').startsWith('#') && l.raw.length > 1).map(l => l.raw.slice(1));
  const missingAnchors = anchorsOnPage.filter(id => !/^(0|!|top)$/.test(id)).filter(id => { try { return !document.getElementById(id) && !document.querySelector('[name="' + CSS.escape(id) + '"]'); } catch(e) { return false; } });
  const breadcrumbs = q('.breadcrumb a, .breadcrumbs a, [aria-label*=readcrumb i] a, nav[class*=breadcrumb] a').map(a => ({href: a.href, text: txt(a)}));
  const pagination = q('.pagination a, .page-numbers, a[rel=next], a[rel=prev], .pager a, nav[aria-label*=agination i] a').map(a => ({href: a.href, text: txt(a)}));
  const nav = { logo_href: logo ? logo.href : '', has_logo: !!logo, missing_anchors: missingAnchors, breadcrumbs, pagination,
    nav_link_count: links.filter(l => l.region === 'nav').length, footer_link_count: links.filter(l => l.region === 'footer').length,
    hamburger_sel: (() => { const h = q('button[aria-label*=menu i], .hamburger, .menu-toggle, .navbar-toggler, [class*=burger], [class*=menu-btn], [class*=mobile-menu] button, button[aria-controls*=nav i], [data-toggle=collapse], [class*=menu-toggle], [class*=nav-toggle], [class*=toggle-menu], [class*=menu-icon], [class*=nav-icon], [class*=mobile-nav] button, .vs-menu-toggle, [class*=sidemenu], [class*=offcanvas-toggle]')[0]; return h ? selPath(h) : ''; })()
  };

  const bodyClone = document.body.cloneNode(true);
  q('script,style,noscript', bodyClone).forEach(e => e.remove());
  const fullText = txt(bodyClone);
  q('header,nav,footer,[role=navigation],[role=banner],[role=contentinfo]', bodyClone).forEach(e => e.remove());
  const mainText = txt(bodyClone);

  const phones = Array.from(new Set((fullText.match(/(?:\+?1[\s.-]?)?\(?\b[2-9]\d{2}\)?[\s.-]?\d{3}[\s.-]?\d{4}\b/g)||[]).concat(links.filter(l => l.href.startsWith('tel:')).map(l => decodeURIComponent(l.href.slice(4))))));
  const emails = Array.from(new Set((fullText.match(/[\w.+-]+@[\w-]+\.[\w.-]+/g)||[]).concat(links.filter(l => l.href.startsWith('mailto:')).map(l => l.href.slice(7).split('?')[0]))));

  const nt = performance.getEntriesByType('navigation')[0] || {};
  const res = performance.getEntriesByType('resource').map(r => ({url: r.name, type: r.initiatorType, bytes: r.transferSize || r.encodedBodySize || 0, ms: Math.round(r.duration)}));
  const renderBlocking = q('head script[src]:not([async]):not([defer]):not([type=module])').length + q('head link[rel=stylesheet]:not([media=print])').length;
  const perf = { dom_content_loaded: Math.round(nt.domContentLoadedEventEnd||0), load: Math.round(nt.loadEventEnd||0), ttfb: Math.round(nt.responseStart||0),
    transfer_bytes: res.reduce((a,r)=>a+r.bytes,0) + (nt.transferSize||0), resource_count: res.length, render_blocking: renderBlocking,
    lcp: window.__wef_lcp || 0, cls: window.__wef_cls || 0 };

  return { title: document.title, meta, headings, links, images, media, forms, buttons, scripts, jsonld, tracking, sliders, nav,
    text: fullText, main_text: mainText, phones, emails, perf, resources: res,
    word_count: mainText.split(/\s+/).filter(Boolean).length, html_len: html.length };
}
"""

INSTALL_OBSERVERS = r"""
() => {
  window.__wef_cls = 0; window.__wef_lcp = 0;
  try { new PerformanceObserver(l => { for (const e of l.getEntries()) if (!e.hadRecentInput) window.__wef_cls += e.value; }).observe({type:'layout-shift', buffered:true}); } catch(e){}
  try { new PerformanceObserver(l => { const es = l.getEntries(); if (es.length) window.__wef_lcp = Math.round(es[es.length-1].startTime); }).observe({type:'largest-contentful-paint', buffered:true}); } catch(e){}
}
"""

VIEWPORT_METRICS = r"""
() => {
  const q = (s, r=document) => Array.from(r.querySelectorAll(s));
  const txt = el => (el.innerText || '').replace(/\s+/g,' ').trim();
  const vw = document.documentElement.clientWidth, vh = innerHeight;
  const vis = el => { const r = el.getBoundingClientRect(); const cs = getComputedStyle(el); return r.width > 0 && r.height > 0 && cs.visibility !== 'hidden' && cs.display !== 'none' && cs.opacity !== '0'; };
  const selPath = el => { if (el.id) return '#' + el.id; let s = el.tagName.toLowerCase(); if (el.className && typeof el.className === 'string') s += '.' + el.className.trim().split(/\s+/).slice(0,2).join('.'); return s; };
  const sw = Math.max(document.documentElement.scrollWidth, document.body.scrollWidth);
  const horizontal_scroll = sw > vw + 2;
  const all = q('body *').filter(el => !['SCRIPT','STYLE','NOSCRIPT','BR'].includes(el.tagName));
  const overflow = [];
  for (const el of all) { if (overflow.length > 15) break; const r = el.getBoundingClientRect();
    if (vis(el) && r.width > 20 && r.right > vw + 8 && r.width < sw * 2 && getComputedStyle(el).position !== 'fixed') {
      let clipped = false; for (let a = el.parentElement; a && a !== document.body; a = a.parentElement) { const ox = getComputedStyle(a).overflowX; if (ox === 'hidden' || ox === 'clip' || ox === 'scroll' || ox === 'auto') { clipped = true; break; } } if (clipped) continue;
      overflow.push({sel: selPath(el), right: Math.round(r.right), text: txt(el).slice(0,60)}); } }
  const cutoff = [];
  for (const el of q('h1,h2,h3,h4,p,li,a,span,button,td,label,div')) { if (cutoff.length > 15) break;
    if (!vis(el) || el.children.length > 3) continue; const cs = getComputedStyle(el);
    if (parseFloat(cs.textIndent) <= -999 || parseFloat(cs.fontSize) === 0 || /img-replace|sr-only|visually-hidden|screen-reader/.test(el.className)) continue;
    if ((cs.overflow === 'hidden' || cs.overflowX === 'hidden' || cs.textOverflow === 'ellipsis') && el.scrollWidth > el.clientWidth + 6 && cs.whiteSpace === 'nowrap' && txt(el).length > 3) cutoff.push({sel: selPath(el), text: txt(el).slice(0,60)}); }
  const inFixed = el => { for (let e = el; e && e !== document.body; e = e.parentElement) { const ps = getComputedStyle(e).position; if (ps === 'fixed' || ps === 'sticky') return true; } return false; };
  const textEls = q('h1,h2,h3,h4,p,a,button,li,label').filter(el => vis(el) && txt(el).length > 2 && el.children.length < 3 && !inFixed(el)).slice(0, 300).map(el => ({el, r: el.getBoundingClientRect()}));
  const overlaps = [];
  for (let i = 0; i < textEls.length && overlaps.length < 10; i++) for (let j = i+1; j < textEls.length; j++) {
    const a = textEls[i], b = textEls[j]; if (a.el.contains(b.el) || b.el.contains(a.el)) continue;
    const ix = Math.min(a.r.right, b.r.right) - Math.max(a.r.left, b.r.left), iy = Math.min(a.r.bottom, b.r.bottom) - Math.max(a.r.top, b.r.top);
    if (ix > 12 && iy > 8) { const minA = Math.min(a.r.width*a.r.height, b.r.width*b.r.height); if (ix*iy > minA*0.3) { overlaps.push({a: txt(a.el).slice(0,40), b: txt(b.el).slice(0,40), sel_a: selPath(a.el), sel_b: selPath(b.el)}); break; } } }
  const ctaRe = /book|buy|sign ?up|get started|contact|call|schedule|reserve|order|register|subscribe|download|try|demo|quote|apply|join|shop|learn more|get a|start/i;
  const ctas = q('a,button').filter(el => ctaRe.test(txt(el)) || ctaRe.test(el.getAttribute('aria-label')||''));
  const hidden_ctas = ctas.filter(el => !vis(el) && !el.closest('nav,header,[class*=menu],[class*=mobile],[class*=offcanvas],[class*=drawer],[class*=modal],[class*=popup],[class*=dropdown],[class*=submenu],[class*=collapse],footer,[class*=slide],[class*=tab],[class*=accordion]')).slice(0,10).map(el => ({text: txt(el).slice(0,50), sel: selPath(el)}));
  const tiny_ctas = ctas.filter(el => { if (!vis(el)) return false; const r = el.getBoundingClientRect(); return (r.width < 24 || r.height < 24); }).slice(0,10).map(el => ({text: txt(el).slice(0,50), sel: selPath(el)}));
  const offscreen_ctas = ctas.filter(el => { if (!vis(el)) return false; const r = el.getBoundingClientRect(); return r.right <= 0 || r.left >= vw; }).slice(0,10).map(el => ({text: txt(el).slice(0,50), sel: selPath(el)}));
  const fixed = all.filter(el => { const cs = getComputedStyle(el); return (cs.position === 'fixed' || cs.position === 'sticky') && vis(el); }).map(el => { const r = el.getBoundingClientRect(); return {sel: selPath(el), pct: Math.round(100*Math.min(r.width,vw)*Math.min(r.height,vh)/(vw*vh))}; }).filter(f => f.pct >= 25);
  const small_text = q('p,li,a,span,label,td').filter(el => vis(el) && txt(el).length > 20 && parseFloat(getComputedStyle(el).fontSize) < 11).length;
  const sections = q('section, main > div, article, [class*=section]').filter(vis);
  const empty_sections = sections.filter(s => { const r = s.getBoundingClientRect(); return r.height > 150 && txt(s).length === 0 && !s.querySelector('img,video,iframe,svg,canvas,form'); }).slice(0,8).map(s => ({sel: selPath(s), h: Math.round(s.getBoundingClientRect().height)}));
  const blocks = q('body *').filter(el => vis(el) && (txt(el).length > 0 || el.querySelector('img,video,iframe')) && el.getBoundingClientRect().height < vh*3 && getComputedStyle(el).position !== 'fixed').map(el => el.getBoundingClientRect()).sort((a,b)=>a.top-b.top);
  let maxGap = 0, gapAt = 0, lastBottom = 0;
  for (const r of blocks) { if (lastBottom && r.top - lastBottom > maxGap) { maxGap = Math.round(r.top - lastBottom); gapAt = Math.round(lastBottom); } lastBottom = Math.max(lastBottom, r.bottom); }
  const distorted = q('img').filter(i => vis(i) && i.naturalWidth > 0).filter(i => { const r = i.getBoundingClientRect(); const cs = getComputedStyle(i); if (cs.objectFit && cs.objectFit !== 'fill') return false; const na = i.naturalWidth / i.naturalHeight, ra = r.width / r.height; return r.width > 40 && r.height > 40 && Math.abs(na - ra) / na > 0.2; }).slice(0,8).map(i => ({src: i.currentSrc||i.src, natural: i.naturalWidth+'x'+i.naturalHeight, rendered: Math.round(i.getBoundingClientRect().width)+'x'+Math.round(i.getBoundingClientRect().height)}));
  const small_targets = vw < 500 ? q('a,button').filter(el => vis(el) && txt(el).length > 0).filter(el => { const r = el.getBoundingClientRect(); return r.height < 24 && r.width < 48; }).length : 0;
  const modals = q('[role=dialog], .modal.show, .modal.in, [class*=popup], [class*=lightbox], [class*=overlay]').filter(el => vis(el) && el.getBoundingClientRect().width > vw*0.3 && el.getBoundingClientRect().height > vh*0.3).map(m => ({sel: selPath(m), has_close: !!m.querySelector('[class*=close], [aria-label*=close i], button[data-dismiss], button[data-bs-dismiss], [class*=dismiss]') || !!document.querySelector('[class*=close]'), pct: Math.round(100*Math.min(m.getBoundingClientRect().width,vw)*Math.min(m.getBoundingClientRect().height,vh)/(vw*vh))}));
  const viewport_meta = (document.querySelector('meta[name=viewport]')||{}).content || '';
  return { vw, vh, scroll_w: sw, page_h: Math.max(document.documentElement.scrollHeight, document.body.scrollHeight), horizontal_scroll, overflow, cutoff, overlaps, hidden_ctas, tiny_ctas, offscreen_ctas, fixed, small_text, empty_sections, max_gap: maxGap, gap_at: gapAt, distorted, small_targets, modals, viewport_meta, cta_count: ctas.length };
}
"""

DOM_SIGNATURE = r"""
() => { const n = document.body.querySelectorAll('*').length; const vis = Array.from(document.querySelectorAll('[role=dialog],.modal,[class*=popup],[class*=modal],[class*=drawer],[class*=offcanvas],[class*=overlay],[class*=open],[class*=active],[class*=expanded],[aria-expanded=true]')).filter(e => { const r = e.getBoundingClientRect(); return r.width > 0 && r.height > 0; }).length; return {n, vis, url: location.href, scroll: Math.round(scrollY), hash: location.hash, bodyClass: document.body.className}; }
"""

HAMBURGER_TEST = r"""
(sel) => {
  const h = document.querySelector(sel); if (!h) return {found: false};
  const vw = innerWidth, vh = innerHeight;
  const count = () => Array.from(document.querySelectorAll('nav a, [class*=menu] a, [class*=nav] a, [class*=drawer] a, [class*=offcanvas] a')).filter(a => {
    const r = a.getBoundingClientRect(); if (r.width <= 0 || r.height <= 0) return false;
    const cx = r.left + r.width/2, cy = r.top + r.height/2; if (cx < 0 || cx > vw || cy < 0 || cy > vh) return false;
    const hit = document.elementFromPoint(cx, cy); return !!hit && (a.contains(hit) || hit.contains(a)); }).length;
  const sig = () => document.body.className + '|' + (h.getAttribute('aria-expanded')||'') + '|' + Array.from(document.querySelectorAll('[class*=menu],[class*=nav],[class*=drawer],[class*=offcanvas]')).map(e => getComputedStyle(e).transform + getComputedStyle(e).visibility + getComputedStyle(e).display + e.className).join('').length;
  const before = count(), sb = sig();
  h.click();
  return new Promise(res => setTimeout(() => { const after = count(); res({found: true, before, after, works: after > before || sig() !== sb}); }, 800));
}
"""

POPUPS = r"""
() => {
  const q = (s, r=document) => Array.from(r.querySelectorAll(s));
  const txt = el => (el.innerText || '').replace(/\s+/g,' ').trim();
  const vw = innerWidth, vh = innerHeight;
  const vis = el => { const r = el.getBoundingClientRect(); const cs = getComputedStyle(el); return r.width > 40 && r.height > 40 && cs.visibility !== 'hidden' && cs.display !== 'none' && cs.opacity !== '0'; };
  const selPath = el => { if (el.id) return '#' + el.id; let s = el.tagName.toLowerCase(); if (el.className && typeof el.className === 'string') s += '.' + el.className.trim().split(/\s+/).slice(0,2).join('.'); return s; };
  const cands = q('[role=dialog], [aria-modal=true], .modal, [class*=popup], [class*=pop-up], [class*=modal], [class*=lightbox], [class*=overlay], [class*=announcement], [class*=promo-bar], [id*=popup], [id*=modal], .cdk-overlay-pane, [class*=dialog]').filter(el => {
    if (!vis(el)) return false; const cs = getComputedStyle(el); const r = el.getBoundingClientRect();
    const floating = cs.position === 'fixed' || cs.position === 'absolute' || parseInt(cs.zIndex||'0') >= 100 || (el.parentElement && getComputedStyle(el.parentElement).position === 'fixed');
    return floating && (r.width * r.height) > (vw * vh * 0.04) && !el.closest('header, nav, footer'); });
  // text of the popup = its own text, or (for empty backdrops/containers) the text of the overlay group it belongs to
  const groupText = el => { let t = txt(el); if (t.length > 10) return t; const c = el.closest('.cdk-overlay-container, [class*=overlay-container], [class*=modal-wrapper], body'); return c ? txt(c).slice(0, 2000) : t; };
  const classify = t => { const s = t.toLowerCase();
    if (/cookie|consent|privacy|gdpr|essential cookies|accept all/.test(s)) return 'consent';
    if (/(log ?in|sign ?in|password|create account)/.test(s)) return 'auth';
    if (/(are you (over|at least)|years (old|of age)|age verification|date of birth)/.test(s)) return 'age';
    if (/(newsletter|subscribe|email address|enter your email)/.test(s)) return 'newsletter';
    if (/chat|how can we help|leave a message/.test(s) && t.length < 200) return 'chat';
    return 'promo'; };
  const seen = []; const out = [];
  for (const el of cands) { if (seen.some(p => p.contains(el) || el.contains(p))) continue; seen.push(el);
    const t = groupText(el); const kind = classify(t);
    const scope = txt(el).length > 10 ? el : (el.closest('.cdk-overlay-container, [class*=overlay-container], [class*=modal-wrapper]') || el);
    const links = q('a[href]', scope).map(a => ({href: a.href, text: txt(a).slice(0,80)})).filter(l => !/^javascript:/.test(l.href));
    const imgs = q('img', scope).map(i => ({src: i.currentSrc||i.src, broken: i.complete && i.naturalWidth === 0}));
    const r = el.getBoundingClientRect();
    if (kind === 'promo' && !imgs.length && !links.length && t.length < 20) continue;   // empty backdrop with nothing behind it
    out.push({sel: selPath(el), kind, text: t.slice(0,160), links, imgs, pct: Math.round(100*Math.min(r.width,vw)*Math.min(r.height,vh)/(vw*vh)),
      has_close: !!scope.querySelector('[class*=close], [aria-label*=close i], button[data-dismiss], button[data-bs-dismiss], [class*=dismiss], .mfp-close') || kind !== 'promo',
      clickable: !!el.getAttribute('onclick') || el.tagName === 'A' || !!el.closest('a[href]') }); }
  return out;
}
"""

DISMISS_CONSENT = r"""
() => {
  const btns = Array.from(document.querySelectorAll('button, a, [role=button]')).filter(b => { const r = b.getBoundingClientRect(); return r.width > 0 && r.height > 0; });
  const re = /^(accept( all)?( cookies)?|allow all|i agree|agree|got it|ok(ay)?|essential cookies only|accept necessary|reject all|decline)$/i;
  const b = btns.find(b => re.test((b.innerText||'').trim()));
  if (b) { b.click(); return (b.innerText||'').trim(); }
  return '';
}
"""


CLEAR_OVERLAYS = r"""
() => {
  // close popups via their close buttons, then remove any remaining fixed overlay covering most of the viewport (backdrops, preloaders)
  const vw = innerWidth, vh = innerHeight; let removed = 0;
  for (const b of document.querySelectorAll('[class*=close], [aria-label*=close i], button[data-dismiss], button[data-bs-dismiss], .mfp-close, [class*=dismiss]')) {
    const r = b.getBoundingClientRect(); if (r.width > 0 && r.height > 0 && !b.closest('header,nav,footer')) { try { b.click(); } catch(e) {} } }
  for (const el of Array.from(document.querySelectorAll('body *'))) {
    const cs = getComputedStyle(el); if (cs.position !== 'fixed' && cs.position !== 'absolute') continue;
    const r = el.getBoundingClientRect(); if (r.width * r.height < vw * vh * 0.5) continue;
    if (el.closest('header,nav,footer,main') && cs.position === 'absolute') continue;
    if (/overlay|backdrop|modal|popup|preload|loader|loading|mask|dim/i.test(el.className + ' ' + el.id) || parseFloat(cs.opacity) < 1 || cs.backgroundColor.startsWith('rgba')) { el.style.setProperty('display', 'none', 'important'); removed++; } }
  document.body.style.overflow = 'auto';
  return removed;
}
"""
