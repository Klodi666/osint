# OSINT Toolkit

Two Python command-line tools for open-source intelligence (OSINT) gathering from **public** data sources.

| Script | Purpose | API keys |
|---|---|---|
| [`osint_master.py`](osint_master.py) | Multi-technique recon toolkit: usernames, DNS, WHOIS, certificates, email, phone, EXIF, ports and more (interactive menu + CLI) | None required |
| [`osint.py`](osint.py) | Public-web name search across several search engines, with relevance scoring and optional Gemini AI analysis | Optional (SerpAPI, Brave, Google CSE, Gemini) |

> ⚠️ **Legal & ethical use only.** These tools are for education, authorized security testing, and research on public information. Only scan or investigate targets you own or have explicit permission to test. You are responsible for complying with all applicable laws and the terms of service of every site queried.

---

## Table of contents

- [Requirements](#requirements)
- [Installation](#installation)
- [`osint_master.py`](#osint_masterpy)
- [`osint.py`](#osintpy)
- [Limitations](#limitations)
- [Disclaimer](#disclaimer)

---

## Requirements

- Python 3.9+
- Dependencies are listed per script below. Optional libraries are detected at runtime and the related features are skipped if the library is missing.

## Installation

```bash
git clone https://github.com/<your-username>/<your-repo>.git
cd <your-repo>

python -m venv .venv
source .venv/bin/activate        # Windows: .venv\Scripts\activate
```

Install what you need:

```bash
# osint_master.py
pip install requests beautifulsoup4 rich
pip install dnspython python-whois phonenumbers Pillow   # optional features

# osint.py
pip install rich requests python-dotenv ddgs google-generativeai
```

---

## `osint_master.py`

An all-in-one recon tool built on free, public sources. Run it with **no arguments** for an interactive menu, or use flags for one-shot CLI runs.

### Features

| # | Technique | Notes |
|---|---|---|
| 1 | Username enumeration | Checks 50+ platforms via HTTP response |
| 2 | Search engine scrape | DuckDuckGo HTML endpoint |
| 3 | DNS lookup | A, AAAA, MX, TXT, NS, SOA, CNAME/CAA (needs `dnspython`) |
| 4 | WHOIS | Needs `python-whois` |
| 5 | Certificate transparency | Subdomain discovery via crt.sh |
| 6 | Gravatar lookup | By email |
| 7 | PGP keyserver lookup | By email |
| 8 | Wayback Machine | Historical snapshots (CDX API) |
| 9 | Cache check | Google / Bing / Wayback |
| 10 | RSS feed fetcher | Parses feed entries |
| 11 | `robots.txt` + `sitemap.xml` | |
| 12 | Phone number analysis | Offline, needs `phonenumbers` |
| 13 | EXIF metadata | From local images, needs `Pillow` |
| 14 | Reverse DNS | By IP |
| 15 | Port scan | Common ports only, **authorized targets only** |
| 16 | SSL/TLS certificate info | |
| 17 | Email header analysis | Parses `.eml` files |
| 18 | Profile URL scrape | Title, meta tags, headings, bio, follower counts |
| 19 | Full recon | Automatic multi-step run from the menu |

### Usage

**Interactive mode**

```bash
python osint_master.py
```

**CLI mode**

```bash
# Username across 50+ sites
python osint_master.py -u johndoe

# Domain: WHOIS, DNS, crt.sh, robots/sitemap, SSL
python osint_master.py -d example.com

# Email: Gravatar + PGP keyservers
python osint_master.py -e someone@example.com

# Phone number analysis
python osint_master.py -p "+355691234567"

# Reverse DNS
python osint_master.py -i 8.8.8.8

# DuckDuckGo search
python osint_master.py -s '"John Doe" GitHub'

# Wayback Machine snapshots
python osint_master.py --wayback example.com

# EXIF from an image
python osint_master.py --exif photo.jpg

# Email header analysis
python osint_master.py --eml message.eml

# SSL certificate info
python osint_master.py --ssl example.com

# Combine flags and export to JSON
python osint_master.py -d example.com -e admin@example.com -o results.json
```

### CLI options

| Flag | Description |
|---|---|
| `-u`, `--username` | Enumerate a username across 50+ sites |
| `-d`, `--domain` | Domain for DNS / WHOIS / crt.sh / SSL / robots |
| `-e`, `--email` | Email for Gravatar + PGP lookup |
| `-p`, `--phone` | Phone number to analyze |
| `-i`, `--ip` | IP address for reverse DNS |
| `-s`, `--search` | DuckDuckGo search query |
| `--wayback` | Domain for Wayback Machine snapshots |
| `--exif` | Image path for EXIF extraction |
| `--eml` | `.eml` file for header analysis |
| `--ssl` | Hostname for SSL/TLS certificate info |
| `-o`, `--output` | Export results to a JSON file |

---

## `osint.py`

A public-web search tool for a person's name. It generates a set of targeted queries, runs them through multiple search providers, de-duplicates and categorizes the results, scores their relevance, and optionally asks Google Gemini to summarize the findings.

### How it works

1. **Query generation:** builds queries such as `"First Last" LinkedIn`, `... GitHub`, `... news`, and so on, optionally narrowed by country, city, company, or domain.
2. **Multi-provider search:** every provider that is configured or available is queried:
   1. SerpAPI (Google results), needs `SERPAPI_KEY`
   2. Brave Search, needs `BRAVE_API_KEY`
   3. Google Programmable Search, needs `GOOGLE_CSE_KEY` and `GOOGLE_CSE_CX`
   4. `ddgs` (DuckDuckGo), no key
   5. DuckDuckGo HTML, no key
   6. Wikipedia API, no key
3. **Categorization:** News, Professional, Academic, Social/Public Profile, Government, GitHub/Technical, Publications, Encyclopedia, and more.
4. **Relevance scoring (0-100):** based on name matches in title, snippet, and URL, plus location, company, and domain matches.
5. **Gemini analysis (optional):** grounded Google Search first, falling back to analysis of the locally collected results. The prompts instruct the model not to invent URLs and to note that a name match does not prove identity.
6. **Output:** rich terminal report and/or JSON export.

The tool works with no keys at all (DuckDuckGo and Wikipedia only), but results are better with SerpAPI or Brave configured.

### Configuration

Never hard-code keys. Use environment variables or a `.env` file (loaded automatically via `python-dotenv`):

```bash
# .env
SERPAPI_KEY=your_serpapi_key
BRAVE_API_KEY=your_brave_key
GOOGLE_CSE_KEY=your_google_cse_key
GOOGLE_CSE_CX=your_cse_id
GEMINI_API_KEY=AIza...            # from https://aistudio.google.com/apikey

# Optional tuning
GEMINI_MODEL=gemini-2.0-flash
OSINT_TIMEOUT=20                  # HTTP timeout in seconds
SERPAPI_MAX_CALLS=15              # SerpAPI call budget per run
```

> Add `.env` to your `.gitignore` so keys are never committed.

### Usage

```bash
# Basic search
python osint.py "John" "Smith"

# With filters and verbose logging
python osint.py "John" "Smith" --country Albania --city Tirane --verbose

# Narrow by company or domain
python osint.py "John" "Smith" --company "Acme Corp" --domain linkedin.com

# JSON output to stdout, or saved to a file
python osint.py "John" "Smith" --json
python osint.py "John" "Smith" --json --output report.json

# Skip Gemini analysis
python osint.py "John" "Smith" --no-gemini

# Check which providers and keys are available
python osint.py --diagnose
```

### CLI options

| Flag | Description |
|---|---|
| `first_name` `last_name` | Positional arguments, both required |
| `--country`, `--city` | Location filters |
| `--company` | Company filter |
| `--domain` | Restrict/boost results for a domain (`site:` query) |
| `--max-results` | Max results per provider query (default `30`) |
| `--max-show` | Number of results shown in the terminal table (default `30`) |
| `--json` | Print the report as JSON |
| `--output` | Write the JSON report to a file |
| `--no-gemini` | Disable Gemini analysis |
| `--verbose` | Debug logging, including per-provider failures |
| `--diagnose` | Print environment diagnostics and exit |

### Example JSON structure

```json
{
  "target": {"first_name": "John", "last_name": "Smith"},
  "filters": {"country": "Albania", "city": "Tirane", "company": null, "domain": null},
  "search_time": "2026-01-01T12:00:00+00:00",
  "queries": ["..."],
  "results": [
    {
      "title": "...",
      "url": "...",
      "domain": "...",
      "snippet": "...",
      "provider": "serpapi",
      "category": "Professional",
      "relevance": 75,
      "matched_terms": ["full name in title", "city match"]
    }
  ],
  "statistics": {"total_results": 0, "unique_domains": 0, "by_category": {}, "by_provider": {}},
  "gemini": {"mode": "grounded_search", "summary": "...", "citations": []},
  "errors": [],
  "disclaimer": "..."
}
```

---

## Limitations

- **A name match is not an identity match.** Many people share a name. Always verify results manually.
- **LLM output can be wrong.** Treat Gemini summaries as leads, not facts, and check every cited URL.
- **Username enumeration produces false positives and negatives.** It relies on HTTP status codes and page text, which vary by site and change over time.
- **Scraping is fragile.** DuckDuckGo HTML parsing and site-specific checks can break when page layouts change, and some providers rate-limit.
- **`osint_master.py` disables TLS certificate verification** on some requests (`verify=False`) to tolerate misconfigured sites. Do not use it to send sensitive data.
- **Port scanning** is limited to a short list of common ports and must only be run against systems you own or are authorized to test.

## Disclaimer

This software is provided for educational purposes and authorized security research only. The authors accept no liability for misuse. Do not use these tools for harassment, stalking, doxxing, or unauthorized access. Comply with local laws and the terms of service of all queried services.

## License

Add your license here (for example, MIT).
