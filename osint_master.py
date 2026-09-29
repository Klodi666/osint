#!/usr/bin/env python3
"""
OSINT Master Tool - 100% Free, No API Keys Required
Implements 18 public data gathering techniques.
For educational and authorized security research only.
"""

import sys
import os
import re
import json
import time
import socket
import ssl
import random
import argparse
import hashlib
import xml.etree.ElementTree as ET
from datetime import datetime
from urllib.parse import quote_plus, urlparse
from email import policy
from email.parser import BytesParser

import requests
from bs4 import BeautifulSoup
from rich.console import Console
from rich.table import Table
from rich.panel import Panel
from rich.progress import Progress, SpinnerColumn, TextColumn
from rich.prompt import Prompt, Confirm

# Optional imports with graceful fallback
try:
    import dns.resolver
    import dns.exception
    DNS_AVAILABLE = True
except ImportError:
    DNS_AVAILABLE = False

try:
    import whois
    WHOIS_AVAILABLE = True
except ImportError:
    WHOIS_AVAILABLE = False

try:
    import phonenumbers
    from phonenumbers import geocoder, carrier, timezone
    PHONE_AVAILABLE = True
except ImportError:
    PHONE_AVAILABLE = False

try:
    from PIL import Image
    from PIL.ExifTags import TAGS, GPSTAGS
    EXIF_AVAILABLE = True
except ImportError:
    EXIF_AVAILABLE = False

console = Console()

USER_AGENTS = [
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.1 Safari/605.1.15",
    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/119.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:120.0) Gecko/20100101 Firefox/120.0"
]

def get_headers():
    return {
        "User-Agent": random.choice(USER_AGENTS),
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
        "Accept-Language": "en-US,en;q=0.5",
        "Connection": "keep-alive",
    }

def polite_delay(min_sec=0.5, max_sec=1.5):
    time.sleep(random.uniform(min_sec, max_sec))

# ==========================================================
# 1. HTML SCRAPING (Social media profiles)
# ==========================================================
def scrape_profile(url):
    """Scrapes public page for metadata: title, description, og:image, follower counts."""
    console.print(f"\n[bold cyan][*] Scraping: {url}[/bold cyan]")
    result = {"url": url, "metadata": {}, "headings": []}
    
    try:
        r = requests.get(url, headers=get_headers(), timeout=10, verify=False)
        if r.status_code != 200:
            return {"error": f"HTTP {r.status_code}"}
        
        soup = BeautifulSoup(r.text, "html.parser")
        
        # Title & meta description
        title = soup.find("title")
        result["metadata"]["title"] = title.get_text(strip=True) if title else None
        
        for meta in soup.find_all("meta"):
            name = meta.get("property") or meta.get("name")
            content = meta.get("content")
            if name and content:
                result["metadata"][name] = content
        
        # Headings
        for h in soup.find_all(["h1", "h2"])[:10]:
            result["headings"].append(h.get_text(strip=True))
        
        # Bio detection (common patterns)
        bio_candidates = soup.find_all(attrs={"class": re.compile(r"bio|description|about", re.I)})
        result["possible_bio"] = [b.get_text(strip=True)[:200] for b in bio_candidates[:3]]
        
        # Follower counts (regex on text)
        follower_match = re.search(r"([\d,\.]+[KkMm]?)\s*(followers|subscribers|fans)", r.text, re.I)
        result["followers"] = follower_match.group(0) if follower_match else None
        
    except Exception as e:
        result["error"] = str(e)
    
    return result

# ==========================================================
# 2. SEARCH ENGINE SCRAPING (DuckDuckGo HTML)
# ==========================================================
def ddg_search(query, max_results=10):
    """Scrapes DuckDuckGo's HTML-only endpoint (no API key needed)."""
    console.print(f"\n[bold cyan][*] DDG Search: {query}[/bold cyan]")
    results = []
    
    try:
        r = requests.post(
            "https://html.duckduckgo.com/html/",
            data={"q": query},
            headers=get_headers(),
            timeout=15
        )
        
        if r.status_code != 200:
            return [{"error": f"HTTP {r.status_code}"}]
        
        soup = BeautifulSoup(r.text, "html.parser")
        
        for res in soup.select(".result")[:max_results]:
            title_el = res.select_one(".result__a")
            snippet_el = res.select_one(".result__snippet")
            url_el = res.select_one(".result__url")
            
            if title_el:
                href = title_el.get("href", "")
                # DDG redirects through a proxy - extract real URL
                if "uddg=" in href:
                    from urllib.parse import unquote, parse_qs
                    parsed = parse_qs(urlparse(href).query)
                    href = unquote(parsed.get("uddg", [href])[0])
                
                results.append({
                    "title": title_el.get_text(strip=True),
                    "url": href,
                    "snippet": snippet_el.get_text(strip=True) if snippet_el else "",
                    "display_url": url_el.get_text(strip=True) if url_el else ""
                })
        
        polite_delay(1.5, 3)
        
    except Exception as e:
        results.append({"error": str(e)})
    
    return results

# ==========================================================
# 3. USERNAME ENUMERATION (100+ sites)
# ==========================================================
USERNAME_SITES = {
    "GitHub": "https://github.com/{}",
    "GitLab": "https://gitlab.com/{}",
    "Bitbucket": "https://bitbucket.org/{}",
    "Reddit": "https://reddit.com/user/{}",
    "Twitter/X": "https://twitter.com/{}",
    "Instagram": "https://instagram.com/{}",
    "TikTok": "https://tiktok.com/@{}",
    "Pinterest": "https://pinterest.com/{}",
    "Twitch": "https://twitch.tv/{}",
    "YouTube": "https://youtube.com/@{}",
    "Medium": "https://medium.com/@{}",
    "Dev.to": "https://dev.to/{}",
    "Keybase": "https://keybase.io/{}",
    "About.me": "https://about.me/{}",
    "Behance": "https://behance.net/{}",
    "Dribbble": "https://dribbble.com/{}",
    "SoundCloud": "https://soundcloud.com/{}",
    "Telegram": "https://t.me/{}",
    "Steam": "https://steamcommunity.com/id/{}",
    "Roblox": "https://www.roblox.com/user.aspx?username={}",
    "Mastodon": "https://mastodon.social/@{}",
    "Facebook": "https://facebook.com/{}",
    "VK": "https://vk.com/{}",
    "Flickr": "https://flickr.com/people/{}",
    "Vimeo": "https://vimeo.com/{}",
    "Wattpad": "https://wattpad.com/user/{}",
    "Replit": "https://replit.com/@{}",
    "Codepen": "https://codepen.io/{}",
    "HackerNews": "https://news.ycombinator.com/user?id={}",
    "ProductHunt": "https://producthunt.com/@{}",
    "Kaggle": "https://kaggle.com/{}",
    "StackOverflow": "https://stackoverflow.com/users/filter?search={}",
    "Npm": "https://npmjs.com/~{}",
    "PyPI": "https://pypi.org/user/{}",
    "DockerHub": "https://hub.docker.com/u/{}",
    "TryHackMe": "https://tryhackme.com/p/{}",
    "HackTheBox": "https://app.hackthebox.com/users/{}",
    "LeetCode": "https://leetcode.com/{}",
    "Codeforces": "https://codeforces.com/profile/{}",
    "Duolingo": "https://duolingo.com/profile/{}",
    "Goodreads": "https://goodreads.com/{}",
    "Letterboxd": "https://letterboxd.com/{}",
    "Spotify": "https://open.spotify.com/user/{}",
    "Last.fm": "https://last.fm/user/{}",
    "Discord": "https://discord.com/users/{}",
    "Minds": "https://minds.com/{}",
    "Patreon": "https://patreon.com/{}",
    "Ko-fi": "https://ko-fi.com/{}",
    "BuyMeACoffee": "https://buymeacoffee.com/{}",
    "Linktree": "https://linktr.ee/{}",
    "Cash.app": "https://cash.app/${}",
    "Venmo": "https://venmo.com/u/{}",
}

def username_enum(username):
    """Checks username existence on 50+ platforms via HTTP status."""
    console.print(f"\n[bold cyan][*] Enumerating username: {username}[/bold cyan]")
    
    results = []
    with Progress(SpinnerColumn(), TextColumn("[progress.description]{task.description}"), transient=True) as progress:
        task = progress.add_task("[cyan]Scanning...", total=len(USERNAME_SITES))
        
        for site, template in USERNAME_SITES.items():
            url = template.format(username)
            progress.update(task, description=f"[cyan]{site}...")
            
            try:
                r = requests.get(url, headers=get_headers(), timeout=7, allow_redirects=True, verify=False)
                
                if r.status_code == 200:
                    tl = r.text.lower()
                    not_found = ["page not found", "user not found", "doesn't exist",
                                 "404 not found", "sorry, this page", "couldn't find",
                                 "this account doesn", "no user found", "profile not found",
                                 "account suspended", "page unavailable"]
                    if not any(i in tl for i in not_found):
                        results.append({"site": site, "url": url, "status": "Found"})
                elif r.status_code == 404:
                    pass  # Not found, skip
                    
            except requests.exceptions.Timeout:
                pass
            except Exception:
                pass
            
            progress.advance(task)
            polite_delay(0.2, 0.5)
    
    return results

# ==========================================================
# 4. DNS LOOKUPS
# ==========================================================
def dns_lookup(domain):
    """Fetches A, AAAA, MX, TXT, NS, SOA, CNAME records."""
    console.print(f"\n[bold cyan][*] DNS Lookup: {domain}[/bold cyan]")
    
    if not DNS_AVAILABLE:
        return {"error": "dnspython not installed. Run: pip install dnspython"}
    
    results = {}
    record_types = ["A", "AAAA", "MX", "TXT", "NS", "SOA", "CNAME", "CAA"]
    
    for rtype in record_types:
        try:
            answers = dns.resolver.resolve(domain, rtype, lifetime=8)
            results[rtype] = [str(r) for r in answers]
        except dns.resolver.NoAnswer:
            results[rtype] = []
        except dns.resolver.NXDOMAIN:
            results[rtype] = ["Domain does not exist"]
            break
        except Exception as e:
            results[rtype] = [f"Error: {str(e)[:50]}"]
    
    return results

# ==========================================================
# 5. WHOIS LOOKUPS
# ==========================================================
def whois_lookup(domain):
    """Queries WHOIS servers directly via port 43."""
    console.print(f"\n[bold cyan][*] WHOIS: {domain}[/bold cyan]")
    
    if not WHOIS_AVAILABLE:
        return {"error": "python-whois not installed. Run: pip install python-whois"}
    
    try:
        w = whois.whois(domain)
        return {
            "domain_name": w.domain_name,
            "registrar": w.registrar,
            "creation_date": str(w.creation_date),
            "expiration_date": str(w.expiration_date),
            "updated_date": str(w.updated_date),
            "name_servers": w.name_servers,
            "status": w.status,
            "emails": w.emails,
            "org": w.org,
            "country": w.country,
            "state": w.state,
            "city": w.city,
            "address": w.address,
            "phone": w.phone,
        }
    except Exception as e:
        return {"error": str(e)}

# ==========================================================
# 6. CERTIFICATE TRANSPARENCY (crt.sh)
# ==========================================================
def crtsh_subdomains(domain):
    """Finds subdomains + emails from certificate transparency logs."""
    console.print(f"\n[bold cyan][*] Certificate Transparency: {domain}[/bold cyan]")
    
    result = {"subdomains": [], "emails": [], "issuers": []}
    
    try:
        r = requests.get(f"https://crt.sh/?q=%25.{domain}&output=json", timeout=30)
        if r.status_code != 200:
            return {"error": f"crt.sh returned HTTP {r.status_code}"}
        
        data = r.json()
        subs = set()
        emails = set()
        issuers = set()
        
        for entry in data:
            name = entry.get("name_value", "")
            for n in name.split("\n"):
                n = n.strip().lower()
                if n.endswith(domain) and "*" not in n:
                    subs.add(n)
            
            issuer = entry.get("issuer_name", "")
            if issuer:
                issuers.add(issuer[:100])
            
            # Look for emails in cert details
            for match in re.findall(r"[\w\.-]+@[\w\.-]+\.\w+", str(entry)):
                emails.add(match)
        
        result["subdomains"] = sorted(subs)
        result["emails"] = sorted(emails)
        result["issuers"] = sorted(issuers)
        result["total_certs"] = len(data)
        
    except Exception as e:
        result["error"] = str(e)
    
    return result

# ==========================================================
# 7. GRAVATAR LOOKUP
# ==========================================================
def gravatar_lookup(email):
    """Fetches Gravatar profile from email MD5 hash."""
    console.print(f"\n[bold cyan][*] Gravatar: {email}[/bold cyan]")
    
    email_hash = hashlib.md5(email.strip().lower().encode()).hexdigest()
    result = {
        "email": email,
        "exists": False,
        "avatar_url": f"https://www.gravatar.com/avatar/{email_hash}",
        "profile_url": f"https://gravatar.com/{email_hash}.json"
    }
    
    try:
        r = requests.get(result["profile_url"], headers=get_headers(), timeout=10)
        if r.status_code == 200:
            data = r.json()
            entry = data.get("entry", [{}])[0]
            result["exists"] = True
            result["data"] = {
                "display_name": entry.get("displayName"),
                "real_name": entry.get("name", {}).get("formatted") if entry.get("name") else None,
                "about_me": entry.get("aboutMe"),
                "location": entry.get("currentLocation"),
                "job_title": entry.get("jobTitle"),
                "company": entry.get("company"),
                "accounts": [a.get("shortname") for a in entry.get("accounts", [])],
                "urls": [u.get("value") for u in entry.get("urls", [])],
            }
    except Exception as e:
        result["error"] = str(e)
    
    return result

# ==========================================================
# 8. PGP KEYSERVER LOOKUP
# ==========================================================
def pgp_lookup(email):
    """Checks keyserver.ubuntu.com for PGP keys associated with email."""
    console.print(f"\n[bold cyan][*] PGP Keyserver: {email}[/bold cyan]")
    
    result = {"email": email, "key_found": False, "key_ids": [], "uids": []}
    
    try:
        url = f"https://keyserver.ubuntu.com/pks/lookup?search={quote_plus(email)}&op=index"
        r = requests.get(url, headers=get_headers(), timeout=15)
        
        if r.status_code == 200 and "pub:" in r.text:
            result["key_found"] = True
            soup = BeautifulSoup(r.text, "html.parser")
            # Extract key IDs and UIDs
            for pre in soup.find_all("pre"):
                text = pre.get_text()
                result["key_ids"].extend(re.findall(r"pub\s+\w+/([A-F0-9]+)", text))
                result["uids"].extend(re.findall(r"uid\s+(.+)", text))
    except Exception as e:
        result["error"] = str(e)
    
    return result

# ==========================================================
# 9. WAYBACK MACHINE (CDX API)
# ==========================================================
def wayback_lookup(domain, limit=20):
    """Finds historical snapshots via Wayback Machine CDX API."""
    console.print(f"\n[bold cyan][*] Wayback Machine: {domain}[/bold cyan]")
    
    result = {"snapshots": [], "total": 0}
    
    try:
        url = f"http://web.archive.org/cdx/search/cdx?url={domain}/*&output=json&limit={limit}&collapse=urlkey"
        r = requests.get(url, headers=get_headers(), timeout=30)
        
        if r.status_code == 200 and r.text.strip():
            data = r.json()
            if len(data) > 1:
                headers_row = data[0]
                for row in data[1:]:
                    entry = dict(zip(headers_row, row))
                    result["snapshots"].append({
                        "url": entry.get("original"),
                        "timestamp": entry.get("timestamp"),
                        "status": entry.get("statuscode"),
                        "wayback_url": f"https://web.archive.org/web/{entry.get('timestamp')}/{entry.get('original')}"
                    })
                result["total"] = len(result["snapshots"])
    except Exception as e:
        result["error"] = str(e)
    
    return result

# ==========================================================
# 10. GOOGLE / BING CACHE CHECK
# ==========================================================
def cache_check(url):
    """Checks if a page is available in Google or Bing cache."""
    console.print(f"\n[bold cyan][*] Cache check: {url}[/bold cyan]")
    
    result = {"url": url, "google_cache": None, "wayback": None}
    
    # Wayback fallback (Google cache is deprecated)
    try:
        r = requests.get(f"http://archive.org/wayback/available?url={url}", timeout=10)
        if r.status_code == 200:
            data = r.json()
            snap = data.get("archived_snapshots", {}).get("closest")
            if snap:
                result["wayback"] = snap.get("url")
    except Exception:
        pass
    
    # Bing cache hint
    result["bing_cache_url"] = f"https://cc.bingj.com/cache.aspx?q={quote_plus(url)}"
    
    return result

# ==========================================================
# 11. RSS FEED PARSING
# ==========================================================
def rss_fetch(feed_url, limit=10):
    """Parses RSS/Atom feed for latest entries."""
    console.print(f"\n[bold cyan][*] RSS Feed: {feed_url}[/bold cyan]")
    
    result = {"feed": feed_url, "items": []}
    
    try:
        r = requests.get(feed_url, headers=get_headers(), timeout=15)
        if r.status_code != 200:
            return {"error": f"HTTP {r.status_code}"}
        
        root = ET.fromstring(r.content)
        
        # Handle both RSS 2.0 and Atom
        items = root.findall(".//item") or root.findall(".//{http://www.w3.org/2005/Atom}entry")
        
        for item in items[:limit]:
            title = item.findtext("title") or item.findtext("{http://www.w3.org/2005/Atom}title")
            link = item.findtext("link") or ""
            if not link:
                link_el = item.find("{http://www.w3.org/2005/Atom}link")
                if link_el is not None:
                    link = link_el.get("href", "")
            desc = item.findtext("description") or item.findtext("{http://www.w3.org/2005/Atom}summary")
            pub = item.findtext("pubDate") or item.findtext("{http://www.w3.org/2005/Atom}updated")
            
            result["items"].append({
                "title": title,
                "link": link,
                "description": (desc or "")[:200],
                "published": pub
            })
    except Exception as e:
        result["error"] = str(e)
    
    return result

# ==========================================================
# 12. SITEMAP.XML / ROBOTS.TXT
# ==========================================================
def robots_sitemap(domain):
    """Fetches robots.txt and sitemap.xml to reveal hidden paths."""
    console.print(f"\n[bold cyan][*] robots.txt + sitemap.xml: {domain}[/bold cyan]")
    
    result = {"robots_txt": None, "sitemap_urls": [], "disallowed_paths": [], "allowed_paths": []}
    
    # robots.txt
    try:
        r = requests.get(f"https://{domain}/robots.txt", headers=get_headers(), timeout=10)
        if r.status_code == 200:
            result["robots_txt"] = r.text[:3000]
            for line in r.text.splitlines():
                line = line.strip()
                if line.lower().startswith("disallow:"):
                    path = line.split(":", 1)[1].strip()
                    if path:
                        result["disallowed_paths"].append(path)
                elif line.lower().startswith("allow:"):
                    path = line.split(":", 1)[1].strip()
                    if path:
                        result["allowed_paths"].append(path)
                elif line.lower().startswith("sitemap:"):
                    result["sitemap_urls"].append(line.split(":", 1)[1].strip())
    except Exception as e:
        result["robots_error"] = str(e)
    
    # Try sitemap.xml
    try:
        r = requests.get(f"https://{domain}/sitemap.xml", headers=get_headers(), timeout=10)
        if r.status_code == 200:
            result["sitemap_xml"] = r.text[:3000]
            result["sitemap_urls"].extend(re.findall(r"<loc>(.*?)</loc>", r.text)[:30])
    except Exception:
        pass
    
    return result

# ==========================================================
# 13. PHONE ANALYSIS (offline)
# ==========================================================
def phone_analysis(phone, region="US"):
    """Analyzes phone number: carrier, country, timezone, line type."""
    console.print(f"\n[bold cyan][*] Phone Analysis: {phone}[/bold cyan]")
    
    if not PHONE_AVAILABLE:
        return {"error": "phonenumbers not installed. Run: pip install phonenumbers"}
    
    result = {"input": phone, "valid": False}
    
    try:
        parsed = phonenumbers.parse(phone, region)
        if phonenumbers.is_valid_number(parsed):
            result["valid"] = True
            result["country_code"] = parsed.country_code
            result["national_number"] = parsed.national_number
            result["international"] = phonenumbers.format_number(parsed, phonenumbers.PhoneNumberFormat.INTERNATIONAL)
            result["e164"] = phonenumbers.format_number(parsed, phonenumbers.PhoneNumberFormat.E164)
            result["location"] = geocoder.description_for_number(parsed, "en") or "Unknown"
            result["carrier"] = carrier.name_for_number(parsed, "en") or "Unknown"
            result["timezone"] = list(timezone.time_zones_for_number(parsed))
            num_type = phonenumbers.number_type(parsed)
            types = {0: "Fixed Line", 1: "Mobile", 2: "Fixed/Mobile", 3: "Toll Free",
                     4: "Premium Rate", 5: "Shared Cost", 6: "VoIP", 7: "Personal",
                     8: "Pager", 9: "UAN", 10: "Voicemail"}
            result["line_type"] = types.get(num_type, "Unknown")
        else:
            result["error"] = "Invalid number"
    except Exception as e:
        result["error"] = str(e)
    
    return result

# ==========================================================
# 14. EXIF METADATA (from images)
# ==========================================================
def exif_extract(image_path):
    """Extracts EXIF metadata (GPS, camera, timestamps) from a local image."""
    console.print(f"\n[bold cyan][*] EXIF Extraction: {image_path}[/bold cyan]")
    
    if not EXIF_AVAILABLE:
        return {"error": "Pillow not installed. Run: pip install Pillow"}
    
    result = {"file": image_path, "exif": {}, "gps": {}}
    
    try:
        img = Image.open(image_path)
        exif_data = img._getexif() or {}
        
        for tag_id, value in exif_data.items():
            tag = TAGS.get(tag_id, tag_id)
            if tag == "GPSInfo":
                gps = {}
                for gps_tag_id, gps_value in value.items():
                    gps_tag = GPSTAGS.get(gps_tag_id, gps_tag_id)
                    gps[gps_tag] = str(gps_value)
                result["gps"] = gps
            else:
                result["exif"][str(tag)] = str(value)[:200]
        
        # Try to convert GPS to decimal
        if result["gps"]:
            try:
                def to_deg(v):
                    d, m, s = v
                    return float(d) + float(m)/60 + float(s)/3600
                lat = to_deg(result["gps"]["GPSLatitude"])
                lon = to_deg(result["gps"]["GPSLongitude"])
                if result["gps"].get("GPSLatitudeRef") == "S": lat = -lat
                if result["gps"].get("GPSLongitudeRef") == "W": lon = -lon
                result["coordinates"] = {"lat": lat, "lon": lon, "maps_url": f"https://maps.google.com/?q={lat},{lon}"}
            except Exception:
                pass
    except Exception as e:
        result["error"] = str(e)
    
    return result

# ==========================================================
# 15. REVERSE DNS
# ==========================================================
def reverse_dns(ip):
    """Resolves IP to hostname."""
    console.print(f"\n[bold cyan][*] Reverse DNS: {ip}[/bold cyan]")
    
    result = {"ip": ip, "hostname": None}
    try:
        hostname, aliases, _ = socket.gethostbyaddr(ip)
        result["hostname"] = hostname
        result["aliases"] = aliases
    except socket.herror:
        result["hostname"] = "No PTR record"
    except Exception as e:
        result["error"] = str(e)
    
    return result

# ==========================================================
# 16. PORT SCANNING (with permission only)
# ==========================================================
COMMON_PORTS = {
    21: "FTP", 22: "SSH", 23: "Telnet", 25: "SMTP", 53: "DNS", 80: "HTTP",
    110: "POP3", 111: "RPC", 135: "MSRPC", 139: "NetBIOS", 143: "IMAP",
    443: "HTTPS", 445: "SMB", 993: "IMAPS", 995: "POP3S", 1433: "MSSQL",
    1521: "Oracle", 3306: "MySQL", 3389: "RDP", 5432: "PostgreSQL",
    5900: "VNC", 6379: "Redis", 8080: "HTTP-Alt", 8443: "HTTPS-Alt",
    9200: "Elasticsearch", 27017: "MongoDB"
}

def port_scan(host, timeout=1.0):
    """Scans common ports. ONLY use on systems you own or have permission to test."""
    console.print(f"\n[bold yellow][!] PORT SCAN: {host} (authorized targets only!)[/bold yellow]")
    
    open_ports = []
    
    with Progress(SpinnerColumn(), TextColumn("[progress.description]{task.description}"), transient=True) as progress:
        task = progress.add_task("[cyan]Scanning ports...", total=len(COMMON_PORTS))
        
        for port, service in COMMON_PORTS.items():
            progress.update(task, description=f"[cyan]Port {port}...")
            try:
                sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
                sock.settimeout(timeout)
                result = sock.connect_ex((host, port))
                if result == 0:
                    open_ports.append({"port": port, "service": service, "state": "open"})
                sock.close()
            except Exception:
                pass
            progress.advance(task)
    
    return {"host": host, "open_ports": open_ports}

# ==========================================================
# 17. SSL/TLS CERTIFICATE INFO
# ==========================================================
def ssl_info(host, port=443):
    """Fetches SSL/TLS certificate details."""
    console.print(f"\n[bold cyan][*] SSL/TLS Cert: {host}:{port}[/bold cyan]")
    
    result = {"host": host, "port": port}
    
    try:
        ctx = ssl.create_default_context()
        ctx.check_hostname = False
        ctx.verify_mode = ssl.CERT_NONE
        
        with socket.create_connection((host, port), timeout=10) as sock:
            with ctx.wrap_socket(sock, server_hostname=host) as ssock:
                cert = ssock.getpeercert()
                result["cipher"] = ssock.cipher()
                result["version"] = ssock.version()
                
                # Get binary cert for more details
                der_cert = ssock.getpeercert(binary_form=True)
                result["cert_size"] = len(der_cert)
                
                if cert:
                    result["subject"] = dict(x[0] for x in cert.get("subject", []))
                    result["issuer"] = dict(x[0] for x in cert.get("issuer", []))
                    result["serial_number"] = cert.get("serialNumber")
                    result["not_before"] = cert.get("notBefore")
                    result["not_after"] = cert.get("notAfter")
                    result["san"] = cert.get("subjectAltName", [])
    except ssl.SSLError as e:
        result["error"] = f"SSL error: {e}"
    except Exception as e:
        result["error"] = str(e)
    
    return result

# ==========================================================
# 18. EMAIL HEADER ANALYSIS
# ==========================================================
def email_header_analysis(filepath):
    """Parses a .eml file and extracts sender, routing, SPF/DKIM hints."""
    console.print(f"\n[bold cyan][*] Email Header Analysis: {filepath}[/bold cyan]")
    
    result = {"file": filepath, "headers": {}, "routing": [], "auth": {}}
    
    try:
        with open(filepath, "rb") as f:
            msg = BytesParser(policy=policy.default).parse(f)
        
        # Key headers
        for h in ["From", "To", "Cc", "Reply-To", "Subject", "Date", "Message-ID", "Return-Path"]:
            if msg.get(h):
                result["headers"][h] = str(msg.get(h))
        
        # Received chain (routing hops)
        for received in msg.get_all("Received", []):
            result["routing"].append(str(received)[:300])
        
        # Authentication results
        auth = msg.get("Authentication-Results", "")
        result["auth"]["raw"] = str(auth) if auth else None
        result["auth"]["spf"] = "pass" if "spf=pass" in str(auth).lower() else ("fail" if "spf=fail" in str(auth).lower() else "unknown")
        result["auth"]["dkim"] = "pass" if "dkim=pass" in str(auth).lower() else ("fail" if "dkim=fail" in str(auth).lower() else "unknown")
        result["auth"]["dmarc"] = "pass" if "dmarc=pass" in str(auth).lower() else ("fail" if "dmarc=fail" in str(auth).lower() else "unknown")
        
        # X-Originating-IP
        result["x_originating_ip"] = msg.get("X-Originating-IP", "Not present")
        result["x_mailer"] = msg.get("X-Mailer", "Not present")
        
    except Exception as e:
        result["error"] = str(e)
    
    return result

# ==========================================================
# REPORTING
# ==========================================================
def print_section(title, content_func):
    console.print(f"\n[bold magenta]═══ {title} ═══[/bold magenta]")
    content_func()

def report_username(data):
    if not data: 
        console.print("[dim]No results[/dim]")
        return
    table = Table(show_header=True, header_style="bold cyan")
    table.add_column("Site")
    table.add_column("URL", style="blue")
    for item in data:
        table.add_row(item["site"], item["url"])
    console.print(table)

def report_dns(data):
    if "error" in data:
        console.print(f"[red]{data['error']}[/red]")
        return
    for rtype, records in data.items():
        if records:
            console.print(f"  [cyan]{rtype}:[/cyan]")
            for r in records[:5]:
                console.print(f"    → {r[:120]}")

def report_whois(data):
    if "error" in data:
        console.print(f"[red]{data['error']}[/red]")
        return
    for k, v in data.items():
        if v and v != "None":
            console.print(f"  [cyan]{k}:[/cyan] {str(v)[:150]}")

def report_crtsh(data):
    if "error" in data:
        console.print(f"[red]{data['error']}[/red]")
        return
    console.print(f"  [cyan]Total certs:[/cyan] {data.get('total_certs', 0)}")
    console.print(f"  [cyan]Subdomains:[/cyan] {len(data.get('subdomains', []))}")
    for s in data.get("subdomains", [])[:20]:
        console.print(f"    → {s}")
    if data.get("emails"):
        console.print(f"  [cyan]Emails in certs:[/cyan]")
        for e in data["emails"][:10]:
            console.print(f"    → {e}")

def report_gravatar(data):
    if data.get("exists"):
        console.print(f"  [green]✓ Gravatar profile found![/green]")
        for k, v in data.get("data", {}).items():
            if v:
                console.print(f"  [cyan]{k}:[/cyan] {v}")
        console.print(f"  [cyan]Avatar URL:[/cyan] {data['avatar_url']}")
    else:
        console.print(f"  [dim]No Gravatar profile for {data['email']}[/dim]")

def report_pgp(data):
    if data.get("key_found"):
        console.print(f"  [green]✓ PGP key found![/green]")
        for kid in data.get("key_ids", [])[:5]:
            console.print(f"    Key ID: {kid}")
        for uid in data.get("uids", [])[:5]:
            console.print(f"    UID: {uid}")
    else:
        console.print(f"  [dim]No PGP key for {data['email']}[/dim]")

def report_wayback(data):
    console.print(f"  [cyan]Total snapshots:[/cyan] {data.get('total', 0)}")
    for s in data.get("snapshots", [])[:10]:
        console.print(f"    [{s['timestamp']}] {s['url'][:80]}")

def report_robots(data):
    if data.get("robots_txt"):
        console.print(f"  [cyan]robots.txt found[/cyan] ({len(data['robots_txt'])} bytes)")
    if data.get("disallowed_paths"):
        console.print(f"  [cyan]Disallowed paths:[/cyan]")
        for p in data["disallowed_paths"][:15]:
            console.print(f"    → {p}")
    if data.get("sitemap_urls"):
        console.print(f"  [cyan]Sitemaps:[/cyan]")
        for s in data["sitemap_urls"][:10]:
            console.print(f"    → {s}")

def report_phone(data):
    if data.get("valid"):
        for k in ["international", "e164", "location", "carrier", "line_type"]:
            console.print(f"  [cyan]{k}:[/cyan] {data.get(k)}")
        console.print(f"  [cyan]timezone:[/cyan] {', '.join(data.get('timezone', []))}")
    else:
        console.print(f"  [red]{data.get('error', 'Invalid')}[/red]")

def report_ports(data):
    if data.get("open_ports"):
        table = Table(show_header=True, header_style="bold red")
        table.add_column("Port")
        table.add_column("Service")
        for p in data["open_ports"]:
            table.add_row(str(p["port"]), p["service"])
        console.print(table)
    else:
        console.print("[dim]No open ports found.[/dim]")

def report_ssl(data):
    if data.get("error"):
        console.print(f"[red]{data['error']}[/red]")
        return
    console.print(f"  [cyan]Version:[/cyan] {data.get('version')}")
    console.print(f"  [cyan]Cipher:[/cyan] {data.get('cipher')}")
    console.print(f"  [cyan]Subject:[/cyan] {data.get('subject')}")
    console.print(f"  [cyan]Issuer:[/cyan] {data.get('issuer')}")
    console.print(f"  [cyan]Valid:[/cyan] {data.get('not_before')} → {data.get('not_after')}")
    sans = data.get("san", [])
    if sans:
        console.print(f"  [cyan]SANs:[/cyan] {', '.join(str(s[1]) for s in sans[:10])}")

def report_email_headers(data):
    if data.get("error"):
        console.print(f"[red]{data['error']}[/red]")
        return
    for k, v in data.get("headers", {}).items():
        console.print(f"  [cyan]{k}:[/cyan] {v}")
    console.print(f"  [cyan]SPF:[/cyan] {data['auth']['spf']}")
    console.print(f"  [cyan]DKIM:[/cyan] {data['auth']['dkim']}")
    console.print(f"  [cyan]DMARC:[/cyan] {data['auth']['dmarc']}")
    console.print(f"  [cyan]X-Originating-IP:[/cyan] {data.get('x_originating_ip')}")

def report_search(data):
    for r in data[:10]:
        if "error" in r:
            console.print(f"[red]{r['error']}[/red]")
            continue
        console.print(f"\n  [bold blue]▸ {r.get('title')}[/bold blue]")
        console.print(f"    [dim]{r.get('url')}[/dim]")
        console.print(f"    {r.get('snippet', '')[:200]}")

# ==========================================================
# EXPORT
# ==========================================================
def export_json(data, filename):
    if not filename.endswith(".json"):
        filename += ".json"
    try:
        with open(filename, "w") as f:
            json.dump(data, f, indent=4, default=str)
        console.print(f"[bold green][+] Exported to {filename}[/bold green]")
    except Exception as e:
        console.print(f"[bold red][-] Export failed: {e}[/bold red]")

# ==========================================================
# MENU
# ==========================================================
MENU_OPTIONS = [
    ("1",  "Username Enumeration (50+ sites)"),
    ("2",  "Search Engine Scrape (DuckDuckGo HTML)"),
    ("3",  "DNS Lookup (A, MX, TXT, NS, SOA, CAA)"),
    ("4",  "WHOIS Lookup"),
    ("5",  "Certificate Transparency (crt.sh subdomains)"),
    ("6",  "Gravatar Lookup (by email)"),
    ("7",  "PGP Keyserver Lookup (by email)"),
    ("8",  "Wayback Machine (historical snapshots)"),
    ("9",  "Cache Check (Google/Bing/Wayback)"),
    ("10", "RSS Feed Fetcher"),
    ("11", "robots.txt + sitemap.xml"),
    ("12", "Phone Number Analysis"),
    ("13", "EXIF Metadata from Image"),
    ("14", "Reverse DNS"),
    ("15", "Port Scan (authorized only!)"),
    ("16", "SSL/TLS Certificate Info"),
    ("17", "Email Header Analysis (.eml)"),
    ("18", "Scrape a Profile URL"),
    ("19", "FULL RECON (auto multi-step)"),
    ("0",  "Exit"),
]

def print_menu():
    console.print("\n[bold cyan]═══ OSINT MASTER TOOL (No API Keys) ═══[/bold cyan]")
    for num, desc in MENU_OPTIONS:
        console.print(f"  [bold yellow][{num:>2}][/bold yellow] {desc}")

def interactive_mode():
    console.print(Panel.fit(
        "[bold green]OSINT Master Tool[/bold green]\n"
        "[italic]100% Free · No API Keys · Public Data Only[/italic]\n"
        "[red]Only use on authorized targets![/red]",
        border_style="green"
    ))
    
    last_data = {}
    
    while True:
        print_menu()
        choice = Prompt.ask("\n[bold]Select option[/bold]", default="0")
        
        if choice == "0":
            console.print("[red]Goodbye![/red]")
            sys.exit(0)
        
        data = {}
        target_type = None
        target = None
        
        try:
            # Determine what input we need
            if choice in ["1"]:
                target = Prompt.ask("Username")
                target_type = "username"
            elif choice in ["2"]:
                target = Prompt.ask("Search query")
                target_type = "search"
            elif choice in ["3", "4", "5", "11", "16"]:
                target = Prompt.ask("Domain")
                target_type = "domain"
            elif choice in ["6", "7"]:
                target = Prompt.ask("Email")
                target_type = "email"
            elif choice == "8":
                target = Prompt.ask("Domain (for wayback)")
                target_type = "domain"
            elif choice == "9":
                target = Prompt.ask("Full URL")
                target_type = "url"
            elif choice == "10":
                target = Prompt.ask("RSS feed URL")
                target_type = "url"
            elif choice == "12":
                target = Prompt.ask("Phone (with country code, e.g. +14155552671)")
                region = Prompt.ask("Default region", default="US")
                target_type = "phone"
            elif choice == "13":
                target = Prompt.ask("Image path")
                target_type = "file"
            elif choice == "14":
                target = Prompt.ask("IP address")
                target_type = "ip"
            elif choice == "15":
                target = Prompt.ask("Host/IP (AUTHORIZED ONLY)")
                confirm = Confirm.ask("[bold red]Do you have explicit permission to scan this host?[/bold red]")
                if not confirm:
                    console.print("[yellow]Aborted.[/yellow]")
                    continue
                target_type = "host"
            elif choice == "17":
                target = Prompt.ask(".eml file path")
                target_type = "file"
            elif choice == "18":
                target = Prompt.ask("Profile URL")
                target_type = "url"
            elif choice == "19":
                target = Prompt.ask("Domain (for full recon)")
                target_type = "domain_full"
            
            with console.status("[bold green]Working...[/bold green]"):
                if choice == "1":
                    data["username_enum"] = username_enum(target)
                elif choice == "2":
                    data["search"] = ddg_search(target)
                elif choice == "3":
                    data["dns"] = dns_lookup(target)
                elif choice == "4":
                    data["whois"] = whois_lookup(target)
                elif choice == "5":
                    data["crtsh"] = crtsh_subdomains(target)
                elif choice == "6":
                    data["gravatar"] = gravatar_lookup(target)
                elif choice == "7":
                    data["pgp"] = pgp_lookup(target)
                elif choice == "8":
                    data["wayback"] = wayback_lookup(target)
                elif choice == "9":
                    data["cache"] = cache_check(target)
                elif choice == "10":
                    data["rss"] = rss_fetch(target)
                elif choice == "11":
                    data["robots"] = robots_sitemap(target)
                elif choice == "12":
                    data["phone"] = phone_analysis(target, region)
                elif choice == "13":
                    data["exif"] = exif_extract(target)
                elif choice == "14":
                    data["reverse_dns"] = reverse_dns(target)
                elif choice == "15":
                    data["ports"] = port_scan(target)
                elif choice == "16":
                    data["ssl"] = ssl_info(target)
                elif choice == "17":
                    data["email_headers"] = email_header_analysis(target)
                elif choice == "18":
                    data["scrape"] = scrape_profile(target)
                elif choice == "19":
                    console.print("[cyan]Running full recon...[/cyan]")
                    data["whois"] = whois_lookup(target)
                    data["dns"] = dns_lookup(target)
                    data["crtsh"] = crtsh_subdomains(target)
                    data["robots"] = robots_sitemap(target)
                    data["ssl"] = ssl_info(target)
                    data["wayback"] = wayback_lookup(target)
                    data["search"] = ddg_search(f'site:{target}')
            
            # Print report
            console.print("\n" + "="*60)
            console.print(Panel.fit(f"[bold green]RESULTS[/bold green] — {target}", border_style="green"))
            console.print("="*60)
            
            if "username_enum" in data:
                print_section("Username Enumeration", lambda: report_username(data["username_enum"]))
            if "search" in data:
                print_section("Search Results", lambda: report_search(data["search"]))
            if "dns" in data:
                print_section("DNS Records", lambda: report_dns(data["dns"]))
            if "whois" in data:
                print_section("WHOIS", lambda: report_whois(data["whois"]))
            if "crtsh" in data:
                print_section("Certificate Transparency", lambda: report_crtsh(data["crtsh"]))
            if "gravatar" in data:
                print_section("Gravatar", lambda: report_gravatar(data["gravatar"]))
            if "pgp" in data:
                print_section("PGP Keyserver", lambda: report_pgp(data["pgp"]))
            if "wayback" in data:
                print_section("Wayback Machine", lambda: report_wayback(data["wayback"]))
            if "cache" in data:
                print_section("Cache Check", lambda: [console.print(f"  [cyan]{k}:[/cyan] {v}") for k, v in data["cache"].items() if v])
            if "rss" in data:
                print_section("RSS Feed", lambda: [console.print(f"  [cyan]▸[/cyan] {i['title']}\n    [dim]{i['link']}[/dim]") for i in data["rss"].get("items", [])[:10]])
            if "robots" in data:
                print_section("robots.txt / sitemap.xml", lambda: report_robots(data["robots"]))
            if "phone" in data:
                print_section("Phone Analysis", lambda: report_phone(data["phone"]))
            if "exif" in data:
                print_section("EXIF Metadata", lambda: [console.print(f"  [cyan]{k}:[/cyan] {v}") for k, v in data["exif"].get("exif", {}).items()] + ([console.print(f"  [green]GPS: {data['exif']['coordinates']}[/green]")] if "coordinates" in data["exif"] else []))
            if "reverse_dns" in data:
                print_section("Reverse DNS", lambda: console.print(f"  [cyan]Hostname:[/cyan] {data['reverse_dns'].get('hostname')}"))
            if "ports" in data:
                print_section("Open Ports", lambda: report_ports(data["ports"]))
            if "ssl" in data:
                print_section("SSL/TLS Certificate", lambda: report_ssl(data["ssl"]))
            if "email_headers" in data:
                print_section("Email Headers", lambda: report_email_headers(data["email_headers"]))
            if "scrape" in data:
                print_section("Scraped Profile", lambda: [console.print(f"  [cyan]{k}:[/cyan] {str(v)[:200]}") for k, v in data["scrape"].get("metadata", {}).items()] + [console.print(f"  [cyan]Followers:[/cyan] {data['scrape'].get('followers')}")])
            
            last_data = data
            
            if Confirm.ask("\n[bold yellow]Export this result?[/bold yellow]", default=False):
                fn = Prompt.ask("Filename", default=f"osint_{int(time.time())}")
                export_json(data, fn)
        
        except KeyboardInterrupt:
            console.print("\n[yellow]Cancelled.[/yellow]")
            continue
        except Exception as e:
            console.print(f"[bold red]Error: {e}[/bold red]")

# ==========================================================
# CLI MODE
# ==========================================================
def main():
    parser = argparse.ArgumentParser(description="OSINT Master Tool — No API keys required")
    parser.add_argument("-u", "--username", help="Enumerate username on 50+ sites")
    parser.add_argument("-d", "--domain", help="Domain for DNS/WHOIS/crt.sh/SSL/robots")
    parser.add_argument("-e", "--email", help="Email for Gravatar + PGP")
    parser.add_argument("-p", "--phone", help="Phone number for analysis")
    parser.add_argument("-i", "--ip", help="IP for reverse DNS")
    parser.add_argument("-s", "--search", help="DuckDuckGo search query")
    parser.add_argument("--wayback", help="Domain for Wayback Machine snapshots")
    parser.add_argument("--exif", help="Image path for EXIF extraction")
    parser.add_argument("--eml", help="Email .eml file for header analysis")
    parser.add_argument("--ssl", help="Hostname for SSL cert info")
    parser.add_argument("-o", "--output", help="Export results to JSON")
    
    args = parser.parse_args()
    
    if len(sys.argv) == 1:
        interactive_mode()
        return
    
    data = {}
    
    with console.status("[bold green]Working...[/bold green]"):
        if args.username:
            data["username"] = username_enum(args.username)
        if args.domain:
            data["whois"] = whois_lookup(args.domain)
            data["dns"] = dns_lookup(args.domain)
            data["crtsh"] = crtsh_subdomains(args.domain)
            data["robots"] = robots_sitemap(args.domain)
            data["ssl"] = ssl_info(args.domain)
        if args.email:
            data["gravatar"] = gravatar_lookup(args.email)
            data["pgp"] = pgp_lookup(args.email)
        if args.phone:
            data["phone"] = phone_analysis(args.phone)
        if args.ip:
            data["reverse_dns"] = reverse_dns(args.ip)
        if args.search:
            data["search"] = ddg_search(args.search)
        if args.wayback:
            data["wayback"] = wayback_lookup(args.wayback)
        if args.exif:
            data["exif"] = exif_extract(args.exif)
        if args.eml:
            data["email_headers"] = email_header_analysis(args.eml)
        if args.ssl:
            data["ssl"] = ssl_info(args.ssl)
    
    # Print all collected sections
    if "username" in data: print_section("Username Enumeration", lambda: report_username(data["username"]))
    if "whois" in data: print_section("WHOIS", lambda: report_whois(data["whois"]))
    if "dns" in data: print_section("DNS", lambda: report_dns(data["dns"]))
    if "crtsh" in data: print_section("Certificate Transparency", lambda: report_crtsh(data["crtsh"]))
    if "robots" in data: print_section("robots.txt", lambda: report_robots(data["robots"]))
    if "ssl" in data: print_section("SSL/TLS", lambda: report_ssl(data["ssl"]))
    if "gravatar" in data: print_section("Gravatar", lambda: report_gravatar(data["gravatar"]))
    if "pgp" in data: print_section("PGP", lambda: report_pgp(data["pgp"]))
    if "phone" in data: print_section("Phone", lambda: report_phone(data["phone"]))
    if "reverse_dns" in data: print_section("Reverse DNS", lambda: console.print(f"  Hostname: {data['reverse_dns'].get('hostname')}"))
    if "search" in data: print_section("Search", lambda: report_search(data["search"]))
    if "wayback" in data: print_section("Wayback", lambda: report_wayback(data["wayback"]))
    if "exif" in data: print_section("EXIF", lambda: [console.print(f"  {k}: {v}") for k, v in data["exif"].get("exif", {}).items()])
    if "email_headers" in data: print_section("Email Headers", lambda: report_email_headers(data["email_headers"]))
    
    if args.output:
        export_json(data, args.output)

if __name__ == "__main__":
    try:
        import urllib3
        urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)
        main()
    except KeyboardInterrupt:
        console.print("\n[red]Interrupted.[/red]")
        sys.exit(0)