#!/usr/bin/env python3
"""
Public Web OSINT Tool — SerpAPI + Gemini (single file, all-in-one)
==================================================================

Providers (queried in order; each returns independently):
  1. SerpAPI          - Google results       (env: SERPAPI_KEY)
  2. Brave Search     - independent index    (env: BRAVE_API_KEY)
  3. Google CSE       - Google Programmable  (env: GOOGLE_CSE_KEY + GOOGLE_CSE_CX)
  4. ddgs             - DuckDuckGo           (no key)
  5. DuckDuckGo HTML  - public HTML endpoint (no key)
  6. Wikipedia        - public API           (no key)

LLM:
  * Gemini grounded search (Google Search tool, if model supports it)
  * Fallback: Gemini analyzes locally-collected results
  * Both modes forbid inventing URLs

Install:
    pip install rich requests python-dotenv ddgs google-generativeai

Configure (NEVER hard-code keys):
    export SERPAPI_KEY="..."
    export GEMINI_API_KEY="AIza..."

Run:
    python osint.py "Klodi" "Ndoci" --country Albania --city Tirane --verbose
    python osint.py "John" "Smith" --json --output report.json
    python osint.py --diagnose
"""

from __future__ import annotations

import argparse
import json
import logging
import os
import re
import sys
import time
from dataclasses import dataclass, field, asdict
from datetime import datetime, timezone
from typing import Any, Iterable, Optional
from urllib.parse import urlparse, urlunparse, quote_plus, unquote

# ---------------------------------------------------------------------------
# Optional imports
# ---------------------------------------------------------------------------

try:
    from dotenv import load_dotenv
    load_dotenv()
except Exception:
    def load_dotenv(*_a, **_k):
        return False

try:
    import requests
except Exception:
    requests = None  # type: ignore

try:
    from rich.console import Console
    from rich.panel import Panel
    from rich.table import Table
    from rich.markdown import Markdown
    from rich.progress import Progress, SpinnerColumn, BarColumn, TextColumn
    RICH_AVAILABLE = True
except Exception:
    RICH_AVAILABLE = False

try:
    from ddgs import DDGS  # type: ignore
    DDGS_AVAILABLE = True
    DDGS_BACKEND = "ddgs"
except Exception:
    try:
        from duckduckgo_search import DDGS  # type: ignore
        DDGS_AVAILABLE = True
        DDGS_BACKEND = "duckduckgo_search"
    except Exception:
        DDGS_AVAILABLE = False
        DDGS_BACKEND = "none"
        DDGS = None  # type: ignore

try:
    import google.generativeai as genai  # type: ignore
    GENAI_AVAILABLE = True
except Exception:
    GENAI_AVAILABLE = False
    genai = None  # type: ignore


# ---------------------------------------------------------------------------
# Logging
# ---------------------------------------------------------------------------

logger = logging.getLogger("osint")


def setup_logging(verbose: bool) -> None:
    logging.basicConfig(
        level=logging.DEBUG if verbose else logging.WARNING,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
        datefmt="%H:%M:%S",
    )


# ---------------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------------

class Config:
    GEMINI_API_KEY: Optional[str] = os.getenv("GEMINI_API_KEY") or os.getenv("GOOGLE_API_KEY")
    GEMINI_MODEL: str = os.getenv("GEMINI_MODEL", "gemini-2.0-flash")

    SERPAPI_KEY: Optional[str] = os.getenv("SERPAPI_KEY")
    BRAVE_API_KEY: Optional[str] = os.getenv("BRAVE_API_KEY")
    GOOGLE_CSE_KEY: Optional[str] = os.getenv("GOOGLE_CSE_KEY")
    GOOGLE_CSE_CX: Optional[str] = os.getenv("GOOGLE_CSE_CX")

    REQUEST_TIMEOUT: int = int(os.getenv("OSINT_TIMEOUT", "20"))
    SERPAPI_MAX_CALLS_PER_RUN: int = int(os.getenv("SERPAPI_MAX_CALLS", "15"))
    USER_AGENT: str = (
        "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/122.0 Safari/537.36"
    )


# ---------------------------------------------------------------------------
# Models
# ---------------------------------------------------------------------------

@dataclass
class SearchResult:
    title: str
    url: str
    domain: str
    snippet: str
    source_query: str
    provider: str = "unknown"
    category: str = "General Web"
    relevance: int = 0
    matched_terms: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class GeminiInsight:
    mode: str = "disabled"
    reason: str = ""
    summary: str = ""
    key_findings: list[str] = field(default_factory=list)
    suggested_queries: list[str] = field(default_factory=list)
    citations: list[dict[str, str]] = field(default_factory=list)
    raw_text: str = ""
    error: Optional[str] = None

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class Report:
    target: dict[str, str]
    filters: dict[str, Any]
    search_time: str
    queries: list[str]
    results: list[SearchResult]
    statistics: dict[str, Any]
    gemini: GeminiInsight = field(default_factory=GeminiInsight)
    errors: list[str] = field(default_factory=list)
    diagnostics: dict[str, Any] = field(default_factory=dict)
    disclaimer: str = (
        "This report contains only publicly available information. "
        "A matching name does NOT establish that two results refer to the "
        "same real person. LLM output can be wrong — always verify URLs "
        "and sources manually."
    )

    def to_dict(self) -> dict[str, Any]:
        d = asdict(self)
        d["results"] = [r.to_dict() for r in self.results]
        d["gemini"] = self.gemini.to_dict()
        return d


# ---------------------------------------------------------------------------
# Query generation
# ---------------------------------------------------------------------------

BASE_QUERY_TEMPLATES = [
    '"{first} {last}"',
    '"{first} {last}" LinkedIn',
    '"{first} {last}" GitHub',
    '"{first} {last}" news',
    '"{first} {last}" interview',
    '"{first} {last}" university',
    '"{first} {last}" researcher',
    '"{first} {last}" publications',
    '"{first} {last}" conference',
    '"{first} {last}" profile',
]


def generate_queries(first: str, last: str,
                     country: Optional[str] = None,
                     city: Optional[str] = None,
                     company: Optional[str] = None,
                     domain: Optional[str] = None) -> list[str]:
    queries: list[str] = []
    for tmpl in BASE_QUERY_TEMPLATES:
        q = tmpl.format(first=first, last=last)
        if company:
            q += f' "{company}"'
        if city:
            q += f' "{city}"'
        if country:
            q += f' "{country}"'
        if domain:
            q += f' site:{domain}'
        queries.append(q)

    if company:
        queries.append(f'"{first} {last}" "{company}"')
    if city or country:
        loc = ", ".join(p for p in [city, country] if p)
        queries.append(f'"{first} {last}" "{loc}"')
    if domain:
        queries.append(f'"{first} {last}" site:{domain}')

    seen: set[str] = set()
    out: list[str] = []
    for q in queries:
        if q not in seen:
            seen.add(q)
            out.append(q)
    return out


# ---------------------------------------------------------------------------
# URL helpers
# ---------------------------------------------------------------------------

def normalize_url(url: str) -> Optional[str]:
    if not url:
        return None
    try:
        p = urlparse(url)
    except Exception:
        return None
    if p.scheme not in ("http", "https"):
        return None
    q = p.query
    if q:
        keep = []
        for chunk in q.split("&"):
            k = chunk.split("=", 1)[0].lower()
            if k in {"utm_source", "utm_medium", "utm_campaign", "utm_term",
                     "utm_content", "fbclid", "gclid", "mc_cid", "mc_eid"}:
                continue
            keep.append(chunk)
        q = "&".join(keep)
    netloc = p.netloc.lower()
    path = p.path.rstrip("/") or "/"
    return urlunparse((p.scheme, netloc, path, "", q, ""))


def domain_of(url: str) -> str:
    try:
        return urlparse(url).netloc.lower()
    except Exception:
        return ""


def _http_get(url: str, params: Optional[dict[str, Any]] = None,
              headers: Optional[dict[str, str]] = None,
              timeout: Optional[int] = None) -> Optional[Any]:
    if requests is None:
        return None
    h = {"User-Agent": Config.USER_AGENT, "Accept": "*/*"}
    if headers:
        h.update(headers)
    try:
        r = requests.get(url, params=params, headers=h,
                         timeout=timeout or Config.REQUEST_TIMEOUT)
        if r.status_code == 429:
            logger.warning("Rate limited: %s", url)
            return None
        r.raise_for_status()
        return r
    except Exception as e:
        logger.debug("HTTP error %s: %s", url, e)
        return None


# ---------------------------------------------------------------------------
# Providers
# ---------------------------------------------------------------------------

_SERPAPI_CALLS = {"count": 0}


def search_serpapi(query: str, max_results: int) -> list[dict[str, Any]]:
    key = Config.SERPAPI_KEY
    if not key:
        return []
    if _SERPAPI_CALLS["count"] >= Config.SERPAPI_MAX_CALLS_PER_RUN:
        logger.info("SerpAPI budget reached (%d calls).", Config.SERPAPI_MAX_CALLS_PER_RUN)
        return []

    r = _http_get(
        "https://serpapi.com/search.json",
        params={"q": query, "num": min(max_results, 20),
                "api_key": key, "engine": "google", "hl": "en"},
    )
    _SERPAPI_CALLS["count"] += 1
    if not r:
        return []
    try:
        data = r.json()
    except Exception:
        return []

    if isinstance(data, dict) and data.get("error"):
        logger.warning("SerpAPI error: %s", data["error"])
        return []

    out: list[dict[str, Any]] = []
    for it in data.get("organic_results", [])[:max_results]:
        out.append({
            "title": it.get("title", ""),
            "url": it.get("link", ""),
            "snippet": it.get("snippet", ""),
            "provider": "serpapi",
        })
    kg = data.get("knowledge_graph") or {}
    if kg.get("title"):
        out.append({
            "title": kg.get("title", ""),
            "url": (kg.get("source") or {}).get("link", "") or "",
            "snippet": kg.get("description", ""),
            "provider": "serpapi_kg",
        })
    return [x for x in out if x["url"]]


def search_brave(query: str, max_results: int) -> list[dict[str, Any]]:
    key = Config.BRAVE_API_KEY
    if not key or requests is None:
        return []
    r = _http_get(
        "https://api.search.brave.com/res/v1/web/search",
        params={"q": query, "count": min(max_results, 20)},
        headers={"Accept": "application/json", "X-Subscription-Token": key},
    )
    if not r:
        return []
    try:
        data = r.json()
    except Exception:
        return []
    return [{
        "title": it.get("title", ""),
        "url": it.get("url", ""),
        "snippet": it.get("description", ""),
        "provider": "brave",
    } for it in (data.get("web", {}) or {}).get("results", [])[:max_results]]


def search_google_cse(query: str, max_results: int) -> list[dict[str, Any]]:
    key, cx = Config.GOOGLE_CSE_KEY, Config.GOOGLE_CSE_CX
    if not key or not cx:
        return []
    r = _http_get("https://www.googleapis.com/customsearch/v1",
                  {"key": key, "cx": cx, "q": query,
                   "num": min(max_results, 10)})
    if not r:
        return []
    try:
        data = r.json()
    except Exception:
        return []
    return [{
        "title": it.get("title", ""),
        "url": it.get("link", ""),
        "snippet": it.get("snippet", ""),
        "provider": "google_cse",
    } for it in data.get("items", [])[:max_results]]


def search_ddgs(query: str, max_results: int) -> list[dict[str, Any]]:
    if not DDGS_AVAILABLE or DDGS is None:
        return []
    try:
        with DDGS() as ddgs:
            hits = list(ddgs.text(query, max_results=max_results))
        return [{
            "title": h.get("title", ""),
            "url": h.get("href") or h.get("url", ""),
            "snippet": h.get("body") or h.get("snippet", ""),
            "provider": DDGS_BACKEND,
        } for h in hits]
    except Exception as e:
        logger.warning("ddgs failed: %s", e)
        return []


def search_ddg_html(query: str, max_results: int) -> list[dict[str, Any]]:
    if requests is None:
        return []
    r = _http_get("https://html.duckduckgo.com/html/",
                  params={"q": query},
                  headers={"Accept": "text/html"})
    if not r:
        return []
    html = r.text
    out: list[dict[str, Any]] = []
    blocks = re.findall(
        r'<a[^>]+class="result__a"[^>]+href="([^"]+)"[^>]*>(.*?)</a>'
        r'.*?<a[^>]+class="result__snippet"[^>]*>(.*?)</a>',
        html, re.S | re.I
    )
    for href, title, snip in blocks[:max_results]:
        m = re.search(r"uddg=([^&]+)", href)
        real = unquote(m.group(1)) if m else (href if href.startswith("http") else None)
        if not real:
            continue
        out.append({
            "title": re.sub(r"<[^>]+>", "", title).strip(),
            "url": real,
            "snippet": re.sub(r"<[^>]+>", "", snip).strip(),
            "provider": "ddg_html",
        })
    return out


def search_wikipedia(query: str, max_results: int) -> list[dict[str, Any]]:
    if requests is None:
        return []
    term = re.sub(r'["\'()\[\]]', " ", query)
    term = re.sub(r'\b(site|linkedin|github|news|interview|university|'
                  r'researcher|publications|conference|profile)\b',
                  " ", term, flags=re.I)
    term = re.sub(r"\s+", " ", term).strip()
    if not term:
        return []
    r = _http_get(
        "https://en.wikipedia.org/w/api.php",
        params={"action": "query", "list": "search", "srsearch": term,
                "format": "json", "srlimit": min(max_results, 10)},
        headers={"Accept": "application/json"},
    )
    if not r:
        return []
    try:
        data = r.json()
    except Exception:
        return []
    out: list[dict[str, Any]] = []
    for item in data.get("query", {}).get("search", [])[:max_results]:
        title = item.get("title", "")
        snip = re.sub(r"<[^>]+>", "", item.get("snippet", ""))
        pageid = item.get("pageid")
        url = (f"https://en.wikipedia.org/?curid={pageid}" if pageid
               else f"https://en.wikipedia.org/wiki/{quote_plus(title)}")
        out.append({
            "title": title, "url": url, "snippet": snip,
            "provider": "wikipedia",
        })
    return out


PROVIDERS = [
    ("serpapi", search_serpapi),
    ("brave", search_brave),
    ("google_cse", search_google_cse),
    ("ddgs", search_ddgs),
    ("ddg_html", search_ddg_html),
    ("wikipedia", search_wikipedia),
]


def search_web(query: str, max_results: int) -> list[dict[str, Any]]:
    combined: list[dict[str, Any]] = []
    for name, fn in PROVIDERS:
        try:
            hits = fn(query, max_results)
        except Exception as e:
            logger.warning("Provider %s failed: %s", name, e)
            hits = []
        if hits:
            logger.info("Provider %s -> %d hits for %r", name, len(hits), query[:70])
            combined.extend(hits)
        time.sleep(0.25)
    return combined


# ---------------------------------------------------------------------------
# Categorization
# ---------------------------------------------------------------------------

CATEGORY_DOMAINS: dict[str, list[str]] = {
    "News": ["bbc.", "cnn.", "nytimes.", "theguardian.", "reuters.",
             "washingtonpost.", "wsj.", "apnews.", "aljazeera.", "forbes.",
             "bloomberg.", "npr.", "abcnews.", "nbcnews.", "cbsnews.",
             "foxnews.", "usatoday.", "latimes.", "economist.",
             "top-channel", "shqiptarja", "panorama", "balkanweb", "exit.al",
             "gazeta", "shekulli", "noa.al", "oranews"],
    "Professional": ["linkedin.", "xing.", "glassdoor.", "indeed.",
                     "crunchbase.", "angellist.", "wellfound."],
    "Academic": [".edu", ".ac.", "scholar.google.", "researchgate.",
                 "academia.edu", "springer.", "sciencedirect.", "jstor.",
                 "arxiv.", "pubmed", "ncbi.nlm.nih.", "semanticscholar.",
                 "orcid.org", "dblp."],
    "Social/Public Profile": ["twitter.", "x.com", "facebook.", "instagram.",
                              "tiktok.", "youtube.", "reddit.", "mastodon.",
                              "threads.net", "bsky.app"],
    "Company": ["about.", "careers.", "crunchbase.",
                "bloomberg.com/company", "companieshouse.", "opencorporates."],
    "Government/Public Institution": [".gov", ".mil", "europa.eu", "un.org",
                                      "who.int", "worldbank.org", "imf.org",
                                      "oecd.org", ".gov.al"],
    "GitHub/Technical": ["github.", "gitlab.", "bitbucket.", "stackoverflow.",
                         "stackexchange.", "medium.com/@", "dev.to",
                         "hashnode.", "npmjs.com", "pypi.org", "crates.io",
                         "hub.docker.com"],
    "Publications": ["doi.org", "springer.", "wiley.", "tandfonline.",
                     "sagepub.", "nature.com", "science.org", "cell.com",
                     "plos.org", "frontiersin.org"],
    "Encyclopedia": ["wikipedia.org", "wikidata.org", "britannica."],
}


def categorize(url: str) -> str:
    low = url.lower()
    for category, patterns in CATEGORY_DOMAINS.items():
        for pat in patterns:
            if pat in low:
                return category
    return "General Web"


# ---------------------------------------------------------------------------
# Scoring
# ---------------------------------------------------------------------------

def score_result(r: SearchResult, first: str, last: str,
                 city: Optional[str] = None, country: Optional[str] = None,
                 company: Optional[str] = None, domain: Optional[str] = None
                 ) -> SearchResult:
    score = 0
    matched: list[str] = []
    full = f"{first} {last}".lower()
    title = r.title.lower()
    snippet = r.snippet.lower()
    url = r.url.lower()

    if full in title:
        score += 40
        matched.append("full name in title")
    elif first.lower() in title and last.lower() in title:
        score += 25
        matched.append("both name parts in title")

    if full in snippet:
        score += 25
        matched.append("full name in snippet")
    elif first.lower() in snippet and last.lower() in snippet:
        score += 15
        matched.append("both name parts in snippet")

    tokens = re.split(r"[-_./]", url)
    if first.lower() in tokens and last.lower() in tokens:
        score += 10
        matched.append("name in URL")

    if city and city.lower() in (title + " " + snippet):
        score += 8
        matched.append("city match")
    if country and country.lower() in (title + " " + snippet):
        score += 6
        matched.append("country match")
    if company and company.lower() in (title + " " + snippet):
        score += 8
        matched.append("company match")
    if domain and domain.lower() in url:
        score += 6
        matched.append("domain match")

    r.relevance = max(0, min(100, score))
    r.matched_terms = matched
    return r


# ---------------------------------------------------------------------------
# Collection
# ---------------------------------------------------------------------------

def collect_results(queries: list[str], max_results: int,
                    verbose: bool = False,
                    progress_cb: Optional[Any] = None
                    ) -> tuple[list[SearchResult], list[str], dict[str, int]]:
    seen: set[str] = set()
    collected: list[SearchResult] = []
    errors: list[str] = []
    provider_counts: dict[str, int] = {}
    per_query = max(5, max_results // max(1, len(queries)) + 3)

    for i, q in enumerate(queries):
        if progress_cb:
            progress_cb(i, len(queries), q)
        try:
            raw = search_web(q, per_query)
        except Exception as e:
            msg = f"Query failed: {q} ({e})"
            errors.append(msg)
            logger.warning(msg)
            continue

        for hit in raw:
            url = normalize_url(hit.get("url", ""))
            if not url or url in seen:
                continue
            seen.add(url)
            prov = hit.get("provider", "unknown")
            provider_counts[prov] = provider_counts.get(prov, 0) + 1
            collected.append(SearchResult(
                title=hit.get("title", "").strip(),
                url=url,
                domain=domain_of(url),
                snippet=hit.get("snippet", "").strip(),
                source_query=q,
                provider=prov,
            ))

        if verbose:
            logger.info("Query '%s' -> %d raw, %d total unique",
                        q, len(raw), len(collected))

    if progress_cb:
        progress_cb(len(queries), len(queries), "")
    return collected, errors, provider_counts


# ---------------------------------------------------------------------------
# Gemini
# ---------------------------------------------------------------------------

def gemini_status() -> tuple[bool, str]:
    if not GENAI_AVAILABLE:
        return False, "google-generativeai not installed (pip install google-generativeai)"
    if not Config.GEMINI_API_KEY:
        return False, "GEMINI_API_KEY not set in environment"
    key = Config.GEMINI_API_KEY.strip()
    if not key.startswith("AIza"):
        return False, (
            f"GEMINI_API_KEY does not look like a Gemini key "
            f"(should start with 'AIza', got '{key[:6]}...'). "
            f"Get one at https://aistudio.google.com/apikey"
        )
    return True, ""


def _build_gemini_prompt(first, last, city, country, company, domain,
                         local_results) -> str:
    lines = [
        "You are a careful OSINT research assistant. Follow these rules:",
        "1. NEVER invent URLs, emails, phone numbers, addresses, or facts.",
        "2. If unsure, say so.",
        "3. A matching name does NOT mean it is the same person — always note this.",
        "4. Do NOT produce content facilitating doxxing, harassment, credential theft, or private-data access.",
        "5. Only reference the search results provided below.",
        "",
        f"Target name: {first} {last}",
        f"Country filter: {country or 'none'}",
        f"City filter: {city or 'none'}",
        f"Company filter: {company or 'none'}",
        f"Domain filter: {domain or 'none'}",
        "",
        "Public search results collected by the local search layer:",
    ]
    for i, r in enumerate(local_results[:40], 1):
        lines.append(
            f"[{i}] title={r.title!r} | url={r.url} | category={r.category} "
            f"| relevance={r.relevance} | query={r.source_query!r} "
            f"| snippet={r.snippet[:280]!r}"
        )
    lines += [
        "",
        "Produce a Markdown report with these sections:",
        "## Summary",
        "## Key Findings (bullet points, each citing the source URL)",
        "## Likely Categories of Results",
        "## Cautions & Ambiguities",
        "## Suggested Follow-up Queries (public sources only)",
        "",
        "Do not include any URL that was not in the list above.",
    ]
    return "\n".join(lines)


def gemini_grounded_search(first, last, city, country, company, domain) -> GeminiInsight:
    enabled, reason = gemini_status()
    if not enabled:
        return GeminiInsight(mode="disabled", reason=reason, error=reason)
    try:
        genai.configure(api_key=Config.GEMINI_API_KEY)

        hints = [f'"{first} {last}"']
        if company:
            hints.append(f'"{company}"')
        if city:
            hints.append(f'"{city}"')
        if country:
            hints.append(f'"{country}"')
        if domain:
            hints.append(f'site:{domain}')

        prompt = (
            "Perform a public-web OSINT search about the person whose name "
            f"is {first} {last}. Additional filters: {' '.join(hints[1:]) or 'none'}.\n\n"
            "Rules:\n"
            "- Use only publicly available sources.\n"
            "- Do NOT return passwords, credentials, private phone numbers,\n"
            "  private addresses, financial data, or private account contents.\n"
            "- A matching name does NOT establish identity. State this clearly.\n"
            "- Return a Markdown report with sections:\n"
            "  ## Summary\n  ## Key Findings (with source URLs)\n"
            "  ## Categories of Presence\n  ## Cautions & Ambiguities\n"
            "  ## Suggested Follow-up Queries\n"
            "- Every factual claim must include the source URL."
        )

        model = genai.GenerativeModel(Config.GEMINI_MODEL)

        try:
            tool = genai.protos.Tool(
                google_search=genai.protos.Tool.GoogleSearch()
            )
            response = model.generate_content(prompt, tools=[tool])
        except Exception as e:
            logger.info("Grounding tool unavailable (%s).", e)
            return GeminiInsight(mode="error",
                                 reason="grounding tool not available",
                                 error=str(e))

        text = getattr(response, "text", "") or ""
        return GeminiInsight(
            mode="grounded_search",
            summary=_first_section(text, "Summary"),
            key_findings=_bullets(text),
            suggested_queries=_bullets_under(text, "Suggested Follow-up Queries"),
            citations=_extract_urls(text),
            raw_text=text,
        )
    except Exception as e:
        logger.warning("Gemini grounded search failed: %s", e)
        return GeminiInsight(mode="error", reason="exception", error=str(e))


def gemini_analyze_results(first, last, city, country, company, domain,
                           local_results) -> GeminiInsight:
    enabled, reason = gemini_status()
    if not enabled:
        return GeminiInsight(mode="disabled", reason=reason, error=reason)
    if not local_results:
        return GeminiInsight(mode="analysis",
                             summary="No local results to analyze.")
    try:
        genai.configure(api_key=Config.GEMINI_API_KEY)
        prompt = _build_gemini_prompt(
            first, last, city, country, company, domain, local_results
        )
        model = genai.GenerativeModel(Config.GEMINI_MODEL)
        response = model.generate_content(prompt)
        text = getattr(response, "text", "") or ""
        return GeminiInsight(
            mode="analysis",
            summary=_first_section(text, "Summary"),
            key_findings=_bullets(text),
            suggested_queries=_bullets_under(text, "Suggested Follow-up Queries"),
            citations=_extract_urls(text),
            raw_text=text,
        )
    except Exception as e:
        logger.warning("Gemini analysis failed: %s", e)
        return GeminiInsight(mode="error", reason="exception", error=str(e))


def _extract_urls(text: str) -> list[dict[str, str]]:
    urls = re.findall(r'https?://[^\s\)\]\}<>"\']+', text)
    seen: set[str] = set()
    out: list[dict[str, str]] = []
    for u in urls:
        u = u.rstrip(".,;:")
        if u in seen:
            continue
        seen.add(u)
        out.append({"url": u, "domain": domain_of(u)})
    return out


def _first_section(text: str, name: str) -> str:
    m = re.search(rf"##\s*{re.escape(name)}\s*\n(.*?)(?=\n##\s|\Z)",
                  text, re.S | re.I)
    return m.group(1).strip() if m else ""


def _bullets(text: str) -> list[str]:
    return [ln.strip(" -*\t") for ln in text.splitlines()
            if re.match(r"^\s*[-*]\s+", ln)]


def _bullets_under(text: str, section: str) -> list[str]:
    body = _first_section(text, section)
    return [ln.strip(" -*\t") for ln in body.splitlines()
            if re.match(r"^\s*[-*]\s+", ln)]


# ---------------------------------------------------------------------------
# Report
# ---------------------------------------------------------------------------

def build_report(first, last, filters, queries, results, gemini, errors,
                 provider_counts) -> Report:
    cat_counts: dict[str, int] = {}
    for r in results:
        cat_counts[r.category] = cat_counts.get(r.category, 0) + 1
    stats = {
        "total_results": len(results),
        "unique_domains": len({r.domain for r in results if r.domain}),
        "by_category": cat_counts,
        "by_provider": provider_counts,
        "serpapi_calls_used": _SERPAPI_CALLS["count"],
    }
    return Report(
        target={"first_name": first, "last_name": last},
        filters=filters,
        search_time=datetime.now(timezone.utc).isoformat(),
        queries=queries,
        results=results,
        statistics=stats,
        gemini=gemini,
        errors=errors,
        diagnostics={
            "ddgs_available": DDGS_AVAILABLE,
            "ddgs_backend": DDGS_BACKEND,
            "genai_available": GENAI_AVAILABLE,
            "gemini_key_set": bool(Config.GEMINI_API_KEY),
            "gemini_key_looks_valid": (
                bool(Config.GEMINI_API_KEY) and Config.GEMINI_API_KEY.startswith("AIza")
            ),
            "serpapi_key_set": bool(Config.SERPAPI_KEY),
            "brave_key_set": bool(Config.BRAVE_API_KEY),
            "google_cse_set": bool(Config.GOOGLE_CSE_KEY and Config.GOOGLE_CSE_CX),
        },
    )


# ---------------------------------------------------------------------------
# Terminal rendering
# ---------------------------------------------------------------------------

def render_terminal(report, first, last, city, country, max_show=30) -> None:
    if RICH_AVAILABLE:
        _render_rich(report, first, last, city, country, max_show)
    else:
        _render_plain(report, first, last, city, country, max_show)


def _render_rich(report, first, last, city, country, max_show):
    console = Console()
    console.print(Panel.fit(
        "[bold cyan]PUBLIC WEB OSINT — SerpAPI + Gemini[/bold cyan]\n"
        "[dim]Public sources only — ethical/legal use required[/dim]",
        border_style="cyan",
    ))
    loc = ", ".join(p for p in [city, country] if p) or "None"
    console.print(f"[bold]Target:[/bold] {first} {last}")
    console.print(f"[bold]Location filter:[/bold] {loc}")
    console.print(f"[bold]Queries run:[/bold] {len(report.queries)}")
    console.print(f"[bold]Results collected:[/bold] {len(report.results)}")
    console.print(f"[bold]Gemini mode:[/bold] {report.gemini.mode}")
    if report.gemini.mode == "disabled" and report.gemini.reason:
        console.print(f"[yellow]Gemini disabled:[/yellow] {report.gemini.reason}")
    console.print()

    d = report.diagnostics
    console.print("[dim]Diagnostics: "
                  f"ddgs={'ok' if d['ddgs_available'] else 'missing'} "
                  f"({d['ddgs_backend']}), "
                  f"genai={'ok' if d['genai_available'] else 'missing'}, "
                  f"gemini_key={'set' if d['gemini_key_set'] else 'unset'}"
                  f"{' (valid format)' if d['gemini_key_looks_valid'] else ''}, "
                  f"serpapi={'set' if d['serpapi_key_set'] else 'unset'}, "
                  f"brave={'set' if d['brave_key_set'] else 'unset'}, "
                  f"google_cse={'set' if d['google_cse_set'] else 'unset'}[/dim]")
    console.print()

    if report.gemini.mode != "disabled" and (report.gemini.raw_text or report.gemini.error):
        console.rule("[bold green]GEMINI ANALYSIS[/bold green]")
        if report.gemini.error and not report.gemini.raw_text:
            console.print(f"[red]Gemini error:[/red] {report.gemini.error}")
        else:
            console.print(Markdown(report.gemini.raw_text))
        if report.gemini.citations:
            console.print()
            console.print("[bold]Gemini citations:[/bold]")
            for c in report.gemini.citations[:20]:
                console.print(f"  - {c['url']}")
        console.print()

    if not report.results:
        console.print("[yellow]No public results found.[/yellow]")
        console.print("[dim]Run with --verbose to see per-provider failures. "
                      "Set SERPAPI_KEY for reliable Google results.[/dim]")
    else:
        table = Table(title="COLLECTED RESULTS", header_style="bold magenta")
        table.add_column("#", style="dim", width=4)
        table.add_column("Title", overflow="fold")
        table.add_column("Category", style="cyan")
        table.add_column("Domain", style="green")
        table.add_column("Rel.", justify="right")
        table.add_column("Provider", style="dim")
        table.add_column("Query", overflow="fold", style="dim")
        for i, r in enumerate(report.results[:max_show], 1):
            table.add_row(str(i), r.title or "(no title)", r.category,
                          r.domain, f"{r.relevance}%", r.provider, r.source_query)
        console.print(table)
        if len(report.results) > max_show:
            console.print(f"[dim]... {len(report.results) - max_show} more "
                          f"(use --json --output to export all)[/dim]")

    console.print()
    console.rule("[bold]SUMMARY[/bold]")
    stats = report.statistics
    console.print(f"Results:          {stats['total_results']}")
    console.print(f"Unique domains:   {stats['unique_domains']}")
    console.print(f"SerpAPI calls:    {stats.get('serpapi_calls_used', 0)}")
    if stats["by_provider"]:
        console.print("[bold]By provider:[/bold]")
        for p, c in stats["by_provider"].items():
            console.print(f"  {p}: {c}")
    if stats["by_category"]:
        console.print("[bold]By category:[/bold]")
        for cat, cnt in sorted(stats["by_category"].items(), key=lambda x: -x[1]):
            console.print(f"  {cat}: {cnt}")
    console.print()
    console.print(Panel.fit(
        "[yellow]Note:[/yellow] A matching name does NOT establish identity. "
        "LLM output can be wrong — verify every source manually.",
        border_style="yellow",
    ))
    if report.errors:
        console.print()
        console.rule("[bold red]ERRORS[/bold red]")
        for e in report.errors[:10]:
            console.print(f"[red]- {e}[/red]")


def _render_plain(report, first, last, city, country, max_show):
    print("=" * 60)
    print("PUBLIC WEB OSINT — SerpAPI + Gemini")
    print("=" * 60)
    loc = ", ".join(p for p in [city, country] if p) or "None"
    print(f"Target: {first} {last}")
    print(f"Location filter: {loc}")
    print(f"Queries run: {len(report.queries)}")
    print(f"Results collected: {len(report.results)}")
    print(f"Gemini mode: {report.gemini.mode}")
    if report.gemini.mode == "disabled" and report.gemini.reason:
        print(f"Gemini disabled: {report.gemini.reason}")
    print()
    if report.gemini.raw_text:
        print("GEMINI ANALYSIS")
        print("-" * 60)
        print(report.gemini.raw_text)
        print()
    if report.results:
        print("COLLECTED RESULTS")
        print("-" * 60)
        for i, r in enumerate(report.results[:max_show], 1):
            print(f"[{i}] {r.title}")
            print(f"    Category: {r.category} | Domain: {r.domain} | "
                  f"Relevance: {r.relevance}% | Provider: {r.provider}")
            print(f"    URL: {r.url}")
            print(f"    Query: {r.source_query}")
            print()
    print("-" * 60)
    print("SUMMARY")
    stats = report.statistics
    print(f"Results:          {stats['total_results']}")
    print(f"Unique domains:   {stats['unique_domains']}")
    print(f"SerpAPI calls:    {stats.get('serpapi_calls_used', 0)}")
    for cat, cnt in sorted(stats["by_category"].items(), key=lambda x: -x[1]):
        print(f"{cat + ':':<22}{cnt}")
    print()
    print("Note: A matching name does NOT establish identity.")
    if report.errors:
        print()
        print("ERRORS")
        for e in report.errors[:10]:
            print(f"- {e}")


# ---------------------------------------------------------------------------
# JSON export
# ---------------------------------------------------------------------------

def export_json(report: Report, path: str) -> None:
    try:
        with open(path, "w", encoding="utf-8") as f:
            json.dump(report.to_dict(), f, indent=2, ensure_ascii=False)
        print(f"\n[+] JSON report written to {path}")
    except OSError as e:
        print(f"[!] Failed to write JSON report: {e}", file=sys.stderr)


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="osint.py",
        description=(
            "Public-web OSINT tool with SerpAPI + Gemini LLM analysis. "
            "A matching name does NOT establish identity."
        ),
    )
    p.add_argument("first_name")
    p.add_argument("last_name")
    p.add_argument("--country")
    p.add_argument("--city")
    p.add_argument("--company")
    p.add_argument("--domain")
    p.add_argument("--max-results", type=int, default=30)
    p.add_argument("--json", action="store_true")
    p.add_argument("--output")
    p.add_argument("--verbose", action="store_true")
    p.add_argument("--max-show", type=int, default=30)
    p.add_argument("--no-gemini", action="store_true")
    p.add_argument("--diagnose", action="store_true",
                   help="Print environment diagnostics and exit")
    return p


def print_diagnostics() -> None:
    print("=== OSINT TOOL DIAGNOSTICS ===")
    print(f"ddgs_available       : {DDGS_AVAILABLE} (backend: {DDGS_BACKEND})")
    print(f"genai_available      : {GENAI_AVAILABLE}")
    print(f"requests_available   : {requests is not None}")
    print(f"rich_available       : {RICH_AVAILABLE}")
    print(f"GEMINI_API_KEY set   : {bool(Config.GEMINI_API_KEY)}")
    if Config.GEMINI_API_KEY:
        print(f"  starts with AIza   : {Config.GEMINI_API_KEY.startswith('AIza')}")
        print(f"  key length         : {len(Config.GEMINI_API_KEY)}")
    print(f"SERPAPI_KEY set      : {bool(Config.SERPAPI_KEY)}")
    if Config.SERPAPI_KEY:
        print(f"  key length         : {len(Config.SERPAPI_KEY)}")
    print(f"BRAVE_API_KEY set    : {bool(Config.BRAVE_API_KEY)}")
    print(f"GOOGLE_CSE_KEY set   : {bool(Config.GOOGLE_CSE_KEY)}")
    print(f"GOOGLE_CSE_CX set    : {bool(Config.GOOGLE_CSE_CX)}")
    print(f"GEMINI_MODEL         : {Config.GEMINI_MODEL}")
    print()
    print("Quick tests:")
    print('  python -c "from ddgs import DDGS; print(len(list(DDGS().text(\'test\', max_results=3))))"')
    print('  python -c "import requests,os; '
          'print(requests.get(\'https://serpapi.com/search.json\','
          'params={\'q\':\'test\',\'api_key\':os.environ.get(\'SERPAPI_KEY\'),\'engine\':\'google\'}).status_code)"')


def main(argv: Optional[list[str]] = None) -> int:
    args = build_parser().parse_args(argv)
    setup_logging(args.verbose)

    if args.diagnose:
        print_diagnostics()
        return 0

    if not args.first_name.strip() or not args.last_name.strip():
        print("[!] First and last name are required.", file=sys.stderr)
        return 2
    if args.max_results < 1:
        print("[!] --max-results must be >= 1", file=sys.stderr)
        return 2

    filters = {
        "country": args.country,
        "city": args.city,
        "company": args.company,
        "domain": args.domain,
    }
    queries = generate_queries(
        args.first_name, args.last_name,
        country=args.country, city=args.city,
        company=args.company, domain=args.domain,
    )

    if not args.json and RICH_AVAILABLE:
        console = Console()
        console.rule("[bold cyan]Searching public sources...[/bold cyan]")
        with Progress(
            SpinnerColumn(),
            TextColumn("[progress.description]{task.description}"),
            BarColumn(),
            TextColumn("{task.completed}/{task.total}"),
            transient=True,
            console=console,
        ) as progress:
            task = progress.add_task("querying", total=len(queries))

            def cb(i, total, q):
                progress.update(task, completed=i,
                                description=(q[:60] if q else "done"))

            results, errors, prov_counts = collect_results(
                queries, args.max_results, verbose=args.verbose, progress_cb=cb
            )
    else:
        results, errors, prov_counts = collect_results(
            queries, args.max_results, verbose=args.verbose
        )

    for r in results:
        r.category = categorize(r.url)
        score_result(r, args.first_name, args.last_name,
                     city=args.city, country=args.country,
                     company=args.company, domain=args.domain)
    results.sort(key=lambda x: (-x.relevance, x.category, x.title.lower()))

    if args.no_gemini:
        gemini = GeminiInsight(mode="disabled", reason="disabled by --no-gemini")
    else:
        enabled, reason = gemini_status()
        if not enabled:
            gemini = GeminiInsight(mode="disabled", reason=reason, error=reason)
            if not args.json:
                print(f"[!] Gemini disabled: {reason}")
        else:
            if not args.json and RICH_AVAILABLE:
                Console().print("[cyan]Asking Gemini...[/cyan]")
            else:
                print("[*] Asking Gemini...")
            gemini = gemini_grounded_search(
                args.first_name, args.last_name,
                args.city, args.country, args.company, args.domain,
            )
            if gemini.mode != "grounded_search" or not gemini.raw_text:
                gemini = gemini_analyze_results(
                    args.first_name, args.last_name,
                    args.city, args.country, args.company, args.domain,
                    results,
                )

    report = build_report(
        args.first_name, args.last_name, filters, queries,
        results, gemini, errors, prov_counts,
    )

    if args.json:
        print(json.dumps(report.to_dict(), indent=2, ensure_ascii=False))
    else:
        render_terminal(report, args.first_name, args.last_name,
                        args.city, args.country, max_show=args.max_show)

    if args.output:
        export_json(report, args.output)

    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except KeyboardInterrupt:
        print("\n[!] Interrupted by user.", file=sys.stderr)
        sys.exit(130)