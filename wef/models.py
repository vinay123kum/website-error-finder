from __future__ import annotations
import hashlib
from dataclasses import dataclass, field, asdict
from typing import Optional

SEVERITIES = ["critical", "high", "medium", "low"]
CATEGORIES = ["functionality", "cta", "forms", "responsive", "content", "consistency",
              "navigation", "visual", "tracking", "seo", "performance"]
SEV_ICON = {"critical": "🔴", "high": "🟠", "medium": "🟡", "low": "🔵"}


@dataclass
class Issue:
    category: str
    severity: str
    title: str
    page: str
    element: str = ""
    current: str = ""
    expected: str = ""
    impact: str = ""
    evidence: str = ""          # screenshot path or URL
    fix: str = ""
    details: dict = field(default_factory=dict)

    @property
    def fingerprint(self) -> str:
        key = f"{self.category}|{self.title}|{self.page}|{self.element}|{self.current}"
        return hashlib.sha1(key.encode("utf-8", "ignore")).hexdigest()[:16]

    def to_dict(self) -> dict:
        d = asdict(self)
        d["fingerprint"] = self.fingerprint
        return d


@dataclass
class PageData:
    url: str
    final_url: str = ""
    status: int = 0
    ok: bool = False
    error: str = ""
    redirect_chain: list = field(default_factory=list)
    load_ms: int = 0
    html: str = ""
    text: str = ""
    title: str = ""
    meta: dict = field(default_factory=dict)
    headings: list = field(default_factory=list)     # [{tag,text}]
    links: list = field(default_factory=list)        # [{href,text,region,rel,target}]
    images: list = field(default_factory=list)       # [{src,alt,natural_w,natural_h,rendered_w,rendered_h,complete,bytes}]
    media: list = field(default_factory=list)        # iframes/videos
    forms: list = field(default_factory=list)
    buttons: list = field(default_factory=list)
    console_errors: list = field(default_factory=list)
    failed_requests: list = field(default_factory=list)
    bad_responses: list = field(default_factory=list)   # {url,status,type}
    resources: list = field(default_factory=list)       # {url,type,bytes}
    scripts: list = field(default_factory=list)
    jsonld: list = field(default_factory=list)
    phones: list = field(default_factory=list)
    emails: list = field(default_factory=list)
    perf: dict = field(default_factory=dict)
    viewports: dict = field(default_factory=dict)       # name -> metrics
    screenshots: dict = field(default_factory=dict)     # name -> path
    nav: dict = field(default_factory=dict)             # hamburger, logo, anchors, breadcrumbs
    sliders: list = field(default_factory=list)
    popups: dict = field(default_factory=dict)
    unexpected_navigations: list = field(default_factory=list)
    button_tests: list = field(default_factory=list)
    form_tests: list = field(default_factory=list)
    tracking: dict = field(default_factory=dict)
    content_hash: str = ""
    depth: int = 0
    found_on: str = ""
    is_external_landing: bool = False
