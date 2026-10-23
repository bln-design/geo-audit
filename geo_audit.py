#!/usr/bin/env python3
"""geo-audit: check whether a website can be crawled, read and cited by AI search
(ChatGPT, Perplexity, Claude, Gemini, Copilot, Google AI Overviews).

Usage:
    python geo_audit.py https://example.com [--lang en|tr] [--json] [--max 30]
                        [--banned phrases.txt] [--indexnow KEY]

Read-only by default. With --indexnow, sitemap URLs are submitted to api.indexnow.org.
--banned: one phrase per line (brand/legal filter); a page containing one fails.
Standard library only, Python 3.10+. Each request <=10 s, whole run <=~2 min.
"""
from __future__ import annotations

import argparse
import concurrent.futures as cf
import gzip
import json
import re
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
import urllib.robotparser
import xml.etree.ElementTree as ET
from html.parser import HTMLParser

__version__ = "1.0.0"

TIMEOUT = 10
TOTAL_BUDGET = 110  # seconds; leaves room for the final report
BODY_LIMIT = 2_000_000
START = time.monotonic()
LANG = "en"

BROWSER_UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
              "(KHTML, like Gecko) Chrome/128.0 Safari/537.36")
BOT_UA = {
    "Googlebot": "Mozilla/5.0 (compatible; Googlebot/2.1; +http://www.google.com/bot.html)",
    "Bingbot": "Mozilla/5.0 (compatible; bingbot/2.0; +http://www.bing.com/bingbot.htm)",
    "OAI-SearchBot": "Mozilla/5.0 AppleWebKit/537.36 (KHTML, like Gecko); compatible; OAI-SearchBot/1.0; +https://openai.com/searchbot",
    "ClaudeBot": "Mozilla/5.0 AppleWebKit/537.36 (KHTML, like Gecko; compatible; ClaudeBot/1.0; +claudebot@anthropic.com)",
    "PerplexityBot": "Mozilla/5.0 AppleWebKit/537.36 (KHTML, like Gecko; compatible; PerplexityBot/1.0; +https://perplexity.ai/perplexitybot)",
}
# robots.txt user agents: (name, needed for AI search?, note). Extra names from the ai.robots.txt list.
ROBOTS_BOTS = [
    ("ChatGPT-User", True, "ChatGPT user-triggered fetch"),
    ("Bingbot", True, "Bing + Copilot; secondary source for ChatGPT"),
    ("Googlebot", True, "Google + AI Overviews"),
    ("PerplexityBot", True, "Perplexity search"),
    ("Perplexity-User", True, "Perplexity user-triggered fetch"),
    ("Claude-SearchBot", True, "Claude search"),
    ("Claude-User", True, "Claude user-triggered fetch"),
    ("DuckAssistBot", True, "DuckDuckGo AI answers"),
    ("MistralAI-User", True, "Mistral Le Chat fetch"),
    ("GPTBot", False, "OpenAI training"),
    ("ClaudeBot", False, "Anthropic training"),
    ("Google-Extended", False, "Gemini training/grounding"),
    ("Applebot-Extended", False, "Apple training"),
    ("CCBot", False, "Common Crawl"),
    ("Meta-ExternalAgent", False, "Meta training"),
    ("Amazonbot", False, "Amazon"),
    ("Bytespider", False, "ByteDance"),
]
QUESTION_WORD = re.compile(
    r"^(ne|neden|nasıl|niçin|kim|kime|hangi|kaç|nerede|ne zaman|ne kadar|what|why|how|who|"
    r"which|when|where|can|does|is|are|should)\b", re.I)
QUESTION_SUFFIX = re.compile(r"\b(mı|mi|mu|mü|mıdır|midir|mudur|müdür)\b", re.I)
DATE_PATTERN = re.compile(
    r"(son güncelleme|güncelleme|güncellendi|yayın tarihi|last updated|updated|published)"
    r"[^0-9]{0,30}(\d{1,2}[./-]\d{1,2}[./-]\d{2,4}|\d{4}-\d{2}-\d{2}|\d{1,2}\s+\w+\s+\d{4})", re.I)
OFFICIAL_HOST = re.compile(r"(\.gov(\.[a-z]{2})?|\.edu(\.[a-z]{2})?|\.mil|\.int|wikipedia\.org|europa\.eu)$", re.I)
NUMBER_PATTERN = re.compile(r"(%\s?\d+|\d+(?:[.,]\d+)?\s?%|\b\d{1,3}(?:[.,]\d{3})+\b|\b\d+[.,]\d+\b|"
                            r"\bm\.\s?\d+|\bmadde\s+\d+|\b\d+\s+sayılı|\b\d+\s?(?:gün|hafta|ay|yıl|"
                            r"days?|weeks?|months?|years?|km|kg|tl|₺|\$|€))", re.I)
REFERENCE_PATTERN = re.compile(r"\b[A-ZÇĞİÖŞÜ]{2,6}\s?\d+(?:/\d+)?\b")  # ISO 9001, RFC 9110

SYMBOL = {"ok": "✔", "fail": "✘", "warn": "!", "info": "·"}

# ---------------- messages (en, tr) ----------------
MSG: dict[str, tuple[str, str]] = {
    # check names
    "n_robots": ("robots.txt", "robots.txt"),
    "n_robots_sitemap": ("robots Sitemap line", "robots Sitemap satırı"),
    "n_oai": ("robots OAI-SearchBot (ChatGPT search)", "robots OAI-SearchBot (ChatGPT arama)"),
    "n_search_bots": ("robots other AI search bots", "robots diğer arama botları"),
    "n_training_bots": ("robots training bots", "robots eğitim botları"),
    "n_sitemap": ("sitemap.xml", "sitemap.xml"),
    "n_sitemap_host": ("sitemap host", "sitemap alan adı"),
    "n_llms": ("llms.txt", "llms.txt"),
    "n_markdown": ("Accept: text/markdown", "Accept: text/markdown"),
    "n_http": ("HTTP 200", "HTTP 200"),
    "n_bot_ua": ("bot UA access", "bot UA erişimi"),
    "n_noindex": ("no noindex", "noindex yok"),
    "n_banned": ("no banned phrases", "yasak ifade yok"),
    "n_title": ("title 30-65 chars", "title 30-65 kr"),
    "n_desc": ("description 70-160 chars", "description 70-160 kr"),
    "n_h1": ("single h1", "tek h1"),
    "n_canonical": ("canonical", "canonical"),
    "n_lang": ("lang", "lang"),
    "n_jsonld": ("JSON-LD", "JSON-LD"),
    "n_schema": ("schema required fields", "şema zorunlu alanlar"),
    "n_faq": ("FAQPage = visible text", "FAQPage = görünür metin"),
    "n_qheading": ("question-style subheadings", "soru biçimli ara başlık"),
    "n_words": ("≥300 words", "≥300 kelime"),
    "n_date": ("visible date", "görünür tarih"),
    "n_date_match": ("date consistency", "tarih uyumu"),
    "n_external": ("external sources", "dış kaynak atfı"),
    "n_quote": ("quote/statistic signals", "alıntı/istatistik izi"),
    "n_internal": ("≥2 internal links", "≥2 iç bağlantı"),
    "n_official": ("official source links", "resmî kaynak bağlantısı"),
    "n_early": ("answer in first 150 words", "ilk 150 kelimede cevap"),
    "n_scope": ("scope", "kapsam"),
    "n_time": ("time budget", "süre"),
    # messages
    "m_not_found": ("not found ({e})", "bulunamadı ({e})"),
    "m_robots_ok": ("present ({lines} lines, {sm} Sitemap lines)", "var ({lines} satır, {sm} Sitemap satırı)"),
    "m_no_sitemap_line": ("no Sitemap line in robots.txt", "robots.txt'de Sitemap satırı yok"),
    "m_open": ("allowed", "açık"),
    "m_oai_blocked": ("BLOCKED: you will not appear in ChatGPT search", "ENGELLİ — ChatGPT aramasında çıkmazsın"),
    "m_blocked": ("BLOCKED: {bots}", "ENGELLİ: {bots}"),
    "m_bots_open": ("{n} bots allowed ({bots}…)", "{n} bot açık ({bots}…)"),
    "m_training_blocked": ("blocked: {bots} (a choice; does not affect search)",
                           "engelli: {bots} (tercih; aramayı etkilemez)"),
    "m_training_open": ("{n} training bots allowed (GPTBot, ClaudeBot, CCBot…)",
                        "{n} eğitim botu açık (GPTBot, ClaudeBot, CCBot…)"),
    "m_unreadable": ("could not read: {e}", "okunamadı: {e}"),
    "m_xml_error": ("XML error: {e}", "XML hatası: {e}"),
    "m_sitemap_ok": ("{n} URLs, lastmod {lm}/{n}", "{n} URL, lastmod {lm}/{n}"),
    "m_foreign_host": ("{n} URLs on a different host (e.g. {u})", "{n} URL farklı host (ör. {u})"),
    "m_llms_none": ("missing (optional; Google does not use it, citation effect unproven)",
                    "yok (isteğe bağlı; Google kullanmıyor, alıntı etkisi kanıtsız)"),
    "m_llms": ("present: H1 {h1}, summary {s}, {h2} sections, {links} links",
               "var: H1 {h1}, özet {s}, {h2} bölüm, {links} bağlantı"),
    "m_llms_missing": (" (missing: {m}; llmstxt.org format)", " — eksik: {m} (llmstxt.org biçimi)"),
    "m_md_yes": ("returns Markdown", "Markdown dönüyor"),
    "m_md_no": ("returns HTML (optional: Cloudflare Markdown for Agents / .md copies)",
                "HTML dönüyor (isteğe bağlı: Cloudflare Markdown for Agents / .md kopyaları)"),
    "m_ratio": ("{ok}/{n} pages pass", "{ok}/{n} sayfada uygun"),
    "m_bot_ratio": ("{ok}/{n} pages return 200 to every bot UA", "{ok}/{n} sayfa tüm bot UA'larıyla 200"),
    "m_official": ("{ok}/{n} pages link to .gov/.edu/Wikipedia etc.",
                   "{ok}/{n} sayfada .gov/.edu/Wikipedia vb. bağlantı"),
    "m_early": ("{ok}/{n} pages have at least 2 full sentences (heuristic; check by hand)",
                "{ok}/{n} sayfada en az 2 tam cümle (sezgisel; elle bak)"),
    "m_scope": ("audited the first {k} of {n} URLs", "{n} URL'den ilk {k} sayfa denetlendi"),
    "m_time": ("time budget reached, some bot tests were skipped",
               "toplam süre sınırına ulaşıldı, bazı bot testleri atlandı"),
    "m_parse_error": ("HTML parse: {e}", "HTML ayrıştırma: {e}"),
    # fixes
    "f_robots": ("add robots.txt: 'User-agent: *' + 'Allow: /' + 'Sitemap: <url>'",
                 "robots.txt ekle: 'User-agent: *' + 'Allow: /' + 'Sitemap: <url>'"),
    "f_sitemap_line": ("append 'Sitemap: {root}/sitemap.xml' to robots.txt",
                       "robots.txt sonuna 'Sitemap: {root}/sitemap.xml' ekle"),
    "f_oai": ("add 'User-agent: OAI-SearchBot' + 'Allow: /' to robots.txt (takes ~24 h to apply)",
              "robots.txt'de 'User-agent: OAI-SearchBot' + 'Allow: /' (değişiklik ~24 saatte işler)"),
    "f_search_bots": ("remove 'Disallow: /' for these bots (Bingbot especially: feeds Copilot and ChatGPT)",
                      "Bu botlar için 'Disallow: /' kaldır (özellikle Bingbot: Copilot + ChatGPT sigortası)"),
    "f_sitemap": ("generate sitemap.xml at the root, reference it in robots.txt, submit to GSC + Bing Webmaster",
                  "sitemap.xml üret, köke koy, robots.txt'e Sitemap satırı ekle, GSC + Bing Webmaster'a gönder"),
    "f_lastmod": ("add a real <lastmod> to every <url> (freshness signal for Bing/IndexNow)",
                  "her <url>'e gerçek <lastmod> ekle (Bing/IndexNow tazelik sinyali)"),
    "f_host": ("use one canonical host in the sitemap (do not mix www and bare domain)",
               "sitemap'te tek kanonik host kullan (www/çıplak karışmasın)"),
    "f_http": ("fix broken sitemap URLs or remove them from the sitemap",
               "kırık sitemap URL'lerini düzelt ya da sitemap'ten çıkar"),
    "f_bot_ua": ("bot UAs are rejected: {bots}", "bot UA'ları reddediliyor: {bots}"),
    "f_bot_cf": (" (allow these bots in Cloudflare > AI Crawl Control / Bot Fight Mode)",
                 " — Cloudflare > AI Crawl Control / Bot Fight Mode'da bu botlara izin ver"),
    "f_bot_waf": (" (remove the bot block in your WAF/security plugin)",
                  " — WAF/güvenlik eklentisinde bot engelini kaldır"),
    "f_noindex": ("remove noindex (meta/X-Robots-Tag) from pages meant to be public",
                  "yayında olması gereken sayfalardan noindex (meta/X-Robots-Tag) kaldır"),
    "f_banned": ("remove phrases listed in the banned-phrases file",
                 "yasak listesindeki ifadeleri sayfadan çıkar"),
    "f_title": ("make the title 30-65 characters, one line naming the page's question/topic",
                "title'ı 30-65 karakter, sayfanın sorusunu/konusunu içeren tek cümle yap"),
    "f_desc": ("meta description 70-160 characters summarising the page's answer",
               "meta description 70-160 karakter, sayfanın net cevabını özetlesin"),
    "f_h1": ("exactly one h1 per page", "her sayfada tam bir h1"),
    "f_canonical": ("add <link rel=\"canonical\" href=\"full-url\">",
                    "<link rel=\"canonical\" href=\"tam-url\"> ekle"),
    "f_lang": ("add <html lang=\"en\"> (or the correct language)",
               "<html lang=\"tr\"> (ya da doğru dil) ekle"),
    "f_jsonld": ("add Organization+WebSite (home) and Article/FAQPage (content) JSON-LD; it must parse",
                 "Organization+WebSite (ana sayfa), Article/FAQPage (içerik) JSON-LD ekle; json.loads geçmeli"),
    "f_schema": ("Article: headline+dateModified, Organization/WebSite: name+url, FAQPage: mainEntity",
                 "Article: headline+dateModified, Organization/WebSite: name+url, FAQPage: mainEntity"),
    "f_faq": ("every FAQPage question must appear verbatim on the page (fake markup is penalised by Google)",
              "FAQPage'deki her soru sayfada birebir görünsün (sahte şema Google'da ceza)"),
    "f_qheading": ("turn real user questions into h2/h3 with a 1-2 sentence answer right below",
                   "kullanıcının gerçekten sorduğu soruları h2/h3 yap, hemen altında 1-2 cümlelik net cevap"),
    "f_words": ("expand thin pages with original information (numbers, tables, examples) or merge them",
                "ince sayfaları özgün bilgiyle (rakam, tablo, örnek) genişlet ya da birleştir"),
    "f_date": ("show 'Last updated: YYYY-MM-DD' (or <time>) on the page plus JSON-LD dateModified",
               "sayfada 'Son güncelleme: GG.AA.YYYY' (ya da <time>) ve JSON-LD dateModified göster"),
    "f_date_match": ("visible date and JSON-LD dateModified should be the same day",
                     "görünür tarih ile JSON-LD dateModified aynı gün olsun"),
    "f_external": ("link to official/primary sources (GEO paper: citing sources up to +28%)",
                   "resmî/özgün kaynağa bağlantı ver (Princeton GEO: kaynak gösterme +%28)"),
    "f_quote": ("add real quotes (<blockquote>) or verified numbers (GEO paper: quotes +43%, statistics +33%); never invent them",
                "gerçek alıntı (<blockquote>) ya da doğrulanmış sayı ekle (GEO: alıntı +%43, istatistik +%33); uydurma yok"),
    "f_internal": ("at least 2 internal links per page (crawler discovery + context)",
                   "sayfalar arası en az 2 iç bağlantı (bot keşfi + bağlam)"),
    "f_time": ("lower the page count with --max", "--max ile sayfa sayısını düşür"),
    # per-page issues
    "p_banned": ("BANNED PHRASE: {x}", "YASAK İFADE: {x}"),
    "p_title": ("title {n}ch", "title {n}kr"),
    "p_desc": ("desc {n}ch", "desc {n}kr"),
    "p_no_canonical": ("no canonical", "canonical yok"),
    "p_no_lang": ("no lang", "lang yok"),
    "p_no_jsonld": ("no JSON-LD", "JSON-LD yok"),
    "p_bad_jsonld": ("broken JSON-LD", "JSON-LD bozuk"),
    "p_schema": ("schema missing {x}", "şema eksik {x}"),
    "p_faq": ("FAQ {a}/{b} questions not on page", "FAQ {a}/{b} soru sayfada yok"),
    "p_qheading": ("0 question headings", "soru başlık 0"),
    "p_words": ("{n} words", "{n} kelime"),
    "p_no_date": ("no visible date", "tarih görünmüyor"),
    "p_date_meta_only": (" (only in JSON-LD/sitemap)", " (yalnız JSON-LD/sitemap'te)"),
    "p_date_mismatch": ("visible date ≠ dateModified", "görünür tarih ≠ dateModified"),
    "p_external": ("0 external sources", "dış kaynak 0"),
    "p_quote": ("0 quotes, {n} numbers", "alıntı 0, sayı {n}"),
    "p_internal": ("{n} internal links", "iç bağlantı {n}"),
    # IndexNow
    "i_warn": (" (WARNING: {u} missing or mismatched; IndexNow will return 403)",
               " (UYARI: {u} bulunamadı/eşleşmiyor; IndexNow 403 döner)"),
    "i_200": ("accepted", "kabul"),
    "i_202": ("received, key validation pending", "alındı, anahtar doğrulaması bekliyor"),
    "i_400": ("bad request", "geçersiz istek"),
    "i_403": ("invalid key", "anahtar geçersiz"),
    "i_422": ("URLs do not match host", "URL'ler host ile uyuşmuyor"),
    "i_429": ("too many requests", "çok sık istek"),
    # report
    "r_header": ("GEO audit: {root}  ({ts}, {s} s)", "GEO denetimi: {root}  ({ts}, {s} sn)"),
    "r_pages": ("Pages (✔ = no issues):", "Sayfalar (sorun yoksa ✔):"),
    "r_score": ("SCORE: {p}/100", "PUAN: {p}/100"),
    "r_top": ("Top fixes:", "En önemli düzeltmeler:"),
    "r_note": ("Note: bot UA tests ran from this machine's IP; they do not show real bot-IP verification "
               "(Cloudflare/WAF IP-based rules). Authoritative sources: Cloudflare AI Crawl Control, "
               "Bing Webmaster URL Inspection, Google Search Console URL Inspection. Content checks use raw HTML "
               "(JavaScript is not executed).",
               "Not: bot UA testi bu bilgisayarın IP'sinden yapıldı; gerçek bot IP'si doğrulamasını "
               "(Cloudflare/WAF IP tabanlı kural) göstermez. Kesin bilgi: Cloudflare AI Crawl Control, "
               "Bing Webmaster URL Inspection, GSC URL Denetimi. İçerik denetimi ham HTML'den (JS çalıştırılmaz)."),
}


def t(key: str, **kw) -> str:
    en, tr = MSG[key]
    return (tr if LANG == "tr" else en).format(**kw)


def time_left() -> bool:
    return time.monotonic() - START < TOTAL_BUDGET


def fetch(url: str, ua: str = BROWSER_UA, data: bytes | None = None,
          headers: dict | None = None) -> dict:
    h = {"User-Agent": ua, "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
         "Accept-Encoding": "gzip"}
    if headers:
        h.update(headers)
    req = urllib.request.Request(url, data=data, headers=h, method="POST" if data else "GET")
    try:
        with urllib.request.urlopen(req, timeout=TIMEOUT) as r:
            body = r.read(BODY_LIMIT)
            if r.headers.get("Content-Encoding") == "gzip":
                try:
                    body = gzip.decompress(body)
                except OSError:
                    pass
            return {"status": r.status, "headers": dict(r.headers), "body": body,
                    "final_url": r.geturl(), "error": None}
    except urllib.error.HTTPError as e:
        try:
            body = e.read(20000)
        except Exception:
            body = b""
        return {"status": e.code, "headers": dict(e.headers or {}), "body": body,
                "final_url": url, "error": f"HTTP {e.code}"}
    except Exception as e:  # timeout, DNS, TLS
        return {"status": 0, "headers": {}, "body": b"", "final_url": url, "error": str(e)[:120]}


def header(resp: dict, name: str) -> str:
    for k, v in resp["headers"].items():
        if k.lower() == name.lower():
            return str(v)
    return ""


def text(resp: dict) -> str:
    m = re.search(r"charset=([\w-]+)", header(resp, "Content-Type"))
    try:
        return resp["body"].decode(m.group(1) if m else "utf-8", errors="replace")
    except LookupError:
        return resp["body"].decode("utf-8", errors="replace")


class Result:
    def __init__(self):
        self.checks: list[dict] = []

    def add(self, status: str, name: str, message: str, fix: str = "", weight: int = 1):
        self.checks.append({"status": status, "name": name, "message": message,
                            "fix": fix, "weight": weight})


# ---------------- robots.txt ----------------
def check_robots(root: str, s: Result) -> list[str]:
    y = fetch(root + "/robots.txt")
    if y["status"] != 200:
        s.add("warn", t("n_robots"), t("m_not_found", e=y["error"] or y["status"]), t("f_robots"), 2)
        return []
    lines = text(y).splitlines()
    rp = urllib.robotparser.RobotFileParser()
    rp.parse(lines)
    sitemaps = [l.split(":", 1)[1].strip() for l in lines if l.lower().startswith("sitemap:")]
    s.add("ok", t("n_robots"), t("m_robots_ok", lines=len(lines), sm=len(sitemaps)), "", 1)
    if not sitemaps:
        s.add("warn", t("n_robots_sitemap"), t("m_no_sitemap_line"), t("f_sitemap_line", root=root), 1)
    # ChatGPT search has its own line, highest weight
    if rp.can_fetch("OAI-SearchBot", root + "/"):
        s.add("ok", t("n_oai"), t("m_open"), "", 6)
    else:
        s.add("fail", t("n_oai"), t("m_oai_blocked"), t("f_oai"), 6)
    search_blocked, training_blocked = [], []
    for name, critical, _ in ROBOTS_BOTS:
        if not rp.can_fetch(name, root + "/"):
            (search_blocked if critical else training_blocked).append(name)
    critical_bots = [a for a, k, _ in ROBOTS_BOTS if k]
    if search_blocked:
        s.add("fail", t("n_search_bots"), t("m_blocked", bots=", ".join(search_blocked)), t("f_search_bots"), 4)
    else:
        s.add("ok", t("n_search_bots"),
              t("m_bots_open", n=len(critical_bots), bots=", ".join(critical_bots[:5])), "", 4)
    training = [a for a, k, _ in ROBOTS_BOTS if not k]
    s.add("info", t("n_training_bots"),
          t("m_training_blocked", bots=", ".join(training_blocked)) if training_blocked
          else t("m_training_open", n=len(training)), "", 0)
    return sitemaps


# ---------------- sitemap ----------------
def read_sitemap(url: str) -> tuple[list[tuple[str, str | None]], list[str], str | None]:
    y = fetch(url)
    if y["status"] != 200:
        return [], [], y["error"] or str(y["status"])
    try:
        tree = ET.fromstring(y["body"])
    except ET.ParseError as e:
        return [], [], t("m_xml_error", e=e)
    ns = lambda tag: tag.split("}")[-1]
    urls, children = [], []
    for el in tree:
        loc = lastmod = None
        for c in el:
            if ns(c.tag) == "loc":
                loc = (c.text or "").strip()
            elif ns(c.tag) == "lastmod":
                lastmod = (c.text or "").strip()
        if not loc:
            continue
        if ns(el.tag) == "sitemap":
            children.append(loc)
        else:
            urls.append((loc, lastmod))
    return urls, children, None


def check_sitemap(root: str, candidates: list[str], s: Result) -> list[tuple[str, str | None]]:
    candidates = candidates or [root + "/sitemap.xml"]
    allurls: list[tuple[str, str | None]] = []
    errors = []
    for sm in candidates[:3]:
        u, children, err = read_sitemap(sm)
        if err:
            errors.append(f"{sm}: {err}")
            continue
        allurls += u
        for c in children[:5]:
            u2, _, e2 = read_sitemap(c)
            if e2:
                errors.append(f"{c}: {e2}")
            allurls += u2
    if not allurls:
        s.add("fail", t("n_sitemap"), t("m_unreadable", e="; ".join(errors)[:150]), t("f_sitemap"), 4)
        return []
    lm = sum(1 for _, l in allurls if l)
    status = "ok" if lm == len(allurls) else "warn"
    s.add(status, t("n_sitemap"), t("m_sitemap_ok", n=len(allurls), lm=lm),
          "" if status == "ok" else t("f_lastmod"), 2)
    host = urllib.parse.urlparse(root).netloc
    foreign = [u for u, _ in allurls if urllib.parse.urlparse(u).netloc != host]
    if foreign:
        s.add("warn", t("n_sitemap_host"), t("m_foreign_host", n=len(foreign), u=foreign[0]), t("f_host"), 1)
    return allurls


# ---------------- llms.txt + markdown ----------------
def check_llms(root: str, s: Result):
    y = fetch(root + "/llms.txt")
    body = text(y) if y["status"] == 200 else ""
    if not body or "<html" in body[:500].lower():
        s.add("info", t("n_llms"), t("m_llms_none"), "", 0)
        return
    lines = body.splitlines()
    h1 = any(l.startswith("# ") for l in lines[:5])
    summary = any(l.startswith(">") for l in lines[:15])
    h2 = sum(1 for l in lines if l.startswith("## "))
    links = len(re.findall(r"\[[^\]]+\]\((https?://[^)]+)\)", body))
    missing = [n for n, v in (("H1", h1), ("> summary", summary), ("## section", h2), ("[name](url)", links)) if not v]
    msg = t("m_llms", h1="✓" if h1 else "✗", s="✓" if summary else "✗", h2=h2, links=links)
    if missing:
        msg += t("m_llms_missing", m=", ".join(missing))
    s.add("ok" if not missing else "info", t("n_llms"), msg, "", 0)


def check_markdown(root: str, s: Result):
    y = fetch(root + "/", headers={"Accept": "text/markdown, text/html;q=0.5"})
    ct = header(y, "Content-Type").lower()
    s.add("info", t("n_markdown"), t("m_md_yes") if "markdown" in ct else t("m_md_no"), "", 0)


# ---------------- page parsing ----------------
class PageParser(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.title = ""
        self.lang = None
        self.meta: dict[str, str] = {}
        self.canonical = None
        self.h1 = 0
        self.headings: list[str] = []
        self.jsonld: list[str] = []
        self.has_time = False
        self.links: list[str] = []
        self.quotes = 0
        self._stack: list[str] = []
        self._buf: list[str] = []
        self._visible: list[str] = []
        self._skip = 0

    def handle_starttag(self, tag, attrs):
        a = {k.lower(): (v or "") for k, v in attrs}
        if tag == "html" and a.get("lang"):
            self.lang = a["lang"]
        elif tag == "meta":
            k = (a.get("name") or a.get("property") or "").lower()
            if k:
                self.meta[k] = a.get("content", "")
        elif tag == "link" and "canonical" in a.get("rel", "").lower().split():
            self.canonical = a.get("href")
        elif tag == "time":
            self.has_time = True
        elif tag == "a" and a.get("href"):
            self.links.append(a["href"])
        elif tag in ("blockquote", "q"):
            self.quotes += 1
        if tag in ("script", "style", "noscript", "template", "svg"):
            self._skip += 1
            if tag == "script" and "ld+json" in a.get("type", "").lower():
                self._stack.append("jsonld")
                self._buf = []
                return
        if tag in ("title", "h1", "h2", "h3"):
            self._stack.append(tag)
            self._buf = []

    def handle_endtag(self, tag):
        if self._stack and (self._stack[-1] == tag or (tag == "script" and self._stack[-1] == "jsonld")):
            top = self._stack.pop()
            content = " ".join("".join(self._buf).split())
            if top == "title":
                self.title = content
            elif top == "h1":
                self.h1 += 1
            elif top in ("h2", "h3"):
                self.headings.append(content)
            elif top == "jsonld":
                self.jsonld.append("".join(self._buf))
        if tag in ("script", "style", "noscript", "template", "svg") and self._skip:
            self._skip -= 1

    def handle_data(self, data):
        if self._stack:
            self._buf.append(data)
        if not self._skip:
            self._visible.append(data)

    def visible_text(self) -> str:
        return " ".join(" ".join(self._visible).split())


REQUIRED = {"Article": ("headline", "dateModified"), "BlogPosting": ("headline", "dateModified"),
            "NewsArticle": ("headline", "dateModified"), "Organization": ("name", "url"),
            "WebSite": ("name", "url"), "FAQPage": ("mainEntity",)}


def parse_jsonld(blocks: list[str]) -> dict:
    types, missing, questions, dates = [], [], [], []
    broken = False

    def walk(o):
        if isinstance(o, dict):
            ty = o.get("@type")
            tl = [ty] if isinstance(ty, str) else [str(x) for x in ty] if isinstance(ty, list) else []
            types.extend(tl)
            for tt in tl:
                for field in REQUIRED.get(tt, ()):
                    if not o.get(field):
                        missing.append(f"{tt}.{field}")
                if tt == "Question" and isinstance(o.get("name"), str):
                    questions.append(o["name"])
            if isinstance(o.get("dateModified"), str):
                dates.append(o["dateModified"][:10])
            for v in o.values():
                walk(v)
        elif isinstance(o, list):
            for v in o:
                walk(v)

    for b in blocks:
        try:
            walk(json.loads(b))
        except json.JSONDecodeError:
            broken = True
    return {"types": sorted(set(types)), "missing": sorted(set(missing)), "questions": questions,
            "dates": dates, "broken": broken}


def is_question(h: str) -> bool:
    h = h.strip()
    return h.endswith("?") or bool(QUESTION_WORD.match(h)) or bool(QUESTION_SUFFIX.search(h))


def norm(s: str) -> str:
    return re.sub(r"\W+", " ", s.lower()).strip()


def visible_date_iso(vt: str) -> str | None:
    m = DATE_PATTERN.search(vt)
    if not m:
        return None
    d = m.group(2)
    if re.fullmatch(r"\d{4}-\d{2}-\d{2}", d):
        return d
    p = re.split(r"[./-]", d)
    if len(p) == 3 and all(x.isdigit() for x in p):
        day, month, year = p
        year = year if len(year) == 4 else "20" + year
        return f"{year}-{int(month):02d}-{int(day):02d}"
    return None


def check_page(url: str, sitemap_lastmod: str | None, banned: list[str]) -> dict:
    y = fetch(url)
    r = {"url": url, "status": y["status"], "error": y["error"], "bot": {}}
    if y["status"] != 200:
        return r
    html = text(y)
    p = PageParser()
    try:
        p.feed(html)
    except Exception as e:
        r["error"] = t("m_parse_error", e=e)
    vt = p.visible_text()
    vt_n = norm(vt)
    ld = parse_jsonld(p.jsonld)
    robots_meta = (p.meta.get("robots", "") + " " + header(y, "X-Robots-Tag")).lower()
    host = urllib.parse.urlparse(url).netloc
    ext_hosts, internal = set(), 0
    for h in p.links:
        u = urllib.parse.urlparse(urllib.parse.urljoin(url, h))
        if u.scheme not in ("http", "https"):
            continue
        if u.netloc == host:
            internal += 1
        else:
            ext_hosts.add(u.netloc.lower())
    faq_missing = [q for q in ld["questions"] if norm(q) not in vt_n]
    vd = visible_date_iso(vt)
    first150 = " ".join(vt.split()[:150])
    r.update({
        "title": p.title, "title_len": len(p.title),
        "desc_len": len(p.meta.get("description", "")),
        "h1": p.h1, "canonical": p.canonical, "lang": p.lang,
        "jsonld": ld["types"], "jsonld_missing": ld["missing"], "jsonld_broken": ld["broken"],
        "faq_missing": len(faq_missing), "faq_questions": len(ld["questions"]),
        "question_headings": sum(1 for h in p.headings if is_question(h)),
        "subheadings": len(p.headings), "words": len(vt.split()),
        "noindex": "noindex" in robots_meta,
        "date_visible": bool(vd) or bool(DATE_PATTERN.search(vt)) or p.has_time,
        "date_meta": bool(ld["dates"]) or "article:modified_time" in p.meta or bool(sitemap_lastmod),
        "date_mismatch": bool(vd and ld["dates"] and vd not in ld["dates"]),
        "external_sources": len(ext_hosts),
        "official_sources": sum(1 for d in ext_hosts if OFFICIAL_HOST.search(d.split(":")[0])),
        "internal_links": internal, "quotes": p.quotes,
        "numbers": len(NUMBER_PATTERN.findall(vt)) + len(REFERENCE_PATTERN.findall(vt)),
        "early_answer": len(re.findall(r"[.!?](\s|$)", first150)) >= 2,
        "banned": [b for b in banned if b.lower() in vt.lower()],
    })
    return r


def check_bots(url: str) -> dict:
    out = {}
    for name, ua in BOT_UA.items():
        if not time_left():
            break
        y = fetch(url, ua=ua)
        out[name] = {"status": y["status"], "cloudflare": "cloudflare" in header(y, "Server").lower()}
    return out


# ---------------- per-page summary ----------------
def page_issues(r: dict) -> list[str]:
    if r["status"] != 200:
        return [f"HTTP {r['status'] or r['error']}"]
    s = []
    if r["banned"]:
        s.append(t("p_banned", x=", ".join(r["banned"])))
    if r["noindex"]:
        s.append("NOINDEX")
    if not 30 <= r["title_len"] <= 65:
        s.append(t("p_title", n=r["title_len"]))
    if not 70 <= r["desc_len"] <= 160:
        s.append(t("p_desc", n=r["desc_len"]))
    if r["h1"] != 1:
        s.append(f"h1={r['h1']}")
    if not r["canonical"]:
        s.append(t("p_no_canonical"))
    if not r["lang"]:
        s.append(t("p_no_lang"))
    if not r["jsonld"]:
        s.append(t("p_no_jsonld"))
    if r["jsonld_broken"]:
        s.append(t("p_bad_jsonld"))
    if r["jsonld_missing"]:
        s.append(t("p_schema", x=",".join(r["jsonld_missing"])))
    if r["faq_missing"]:
        s.append(t("p_faq", a=r["faq_missing"], b=r["faq_questions"]))
    if r["question_headings"] == 0:
        s.append(t("p_qheading"))
    if r["words"] < 300:
        s.append(t("p_words", n=r["words"]))
    if not r["date_visible"]:
        s.append(t("p_no_date") + (t("p_date_meta_only") if r["date_meta"] else ""))
    if r["date_mismatch"]:
        s.append(t("p_date_mismatch"))
    if r["external_sources"] == 0:
        s.append(t("p_external"))
    if r["quotes"] == 0 and r["numbers"] < 3:
        s.append(t("p_quote", n=r["numbers"]))
    if r["internal_links"] < 2:
        s.append(t("p_internal", n=r["internal_links"]))
    bad = [b for b, v in r.get("bot", {}).items() if v["status"] != 200]
    if bad:
        s.append("bot " + ",".join(f"{b}:{r['bot'][b]['status']}" for b in bad))
    return s


def site_checks(pages: list[dict], s: Result, has_banned: bool):
    ok = [r for r in pages if r["status"] == 200]
    n = len(pages)
    if not n:
        return

    def count(f):
        return sum(1 for r in ok if f(r))

    def check(name, passed, fix, weight, fail_below=0.5):
        status = "ok" if passed == n else ("fail" if passed / n < fail_below else "warn")
        s.add(status, t(name), t("m_ratio", ok=passed, n=n), "" if status == "ok" else t(fix), weight)

    check("n_http", len(ok), "f_http", 4, 0.9)
    bot_tested = sum(1 for r in ok if r.get("bot"))
    if bot_tested:
        bot_ok = count(lambda r: r.get("bot") and all(v["status"] == 200 for v in r["bot"].values()))
        blocked = sorted({b for r in ok for b, v in r.get("bot", {}).items() if v["status"] != 200})
        cf_ = any(v["cloudflare"] for r in ok for v in r.get("bot", {}).values())
        status = "ok" if bot_ok == bot_tested else "fail"
        fix = t("f_bot_ua", bots=", ".join(blocked)) + (t("f_bot_cf") if cf_ else t("f_bot_waf"))
        s.add(status, t("n_bot_ua"), t("m_bot_ratio", ok=bot_ok, n=bot_tested),
              "" if status == "ok" else fix, 5)
    check("n_noindex", count(lambda r: not r["noindex"]), "f_noindex", 5, 0.95)
    if has_banned:
        check("n_banned", count(lambda r: not r["banned"]), "f_banned", 6, 1.01)
    check("n_title", count(lambda r: 30 <= r["title_len"] <= 65), "f_title", 2)
    check("n_desc", count(lambda r: 70 <= r["desc_len"] <= 160), "f_desc", 2)
    check("n_h1", count(lambda r: r["h1"] == 1), "f_h1", 2)
    check("n_canonical", count(lambda r: bool(r["canonical"])), "f_canonical", 2)
    check("n_lang", count(lambda r: bool(r["lang"])), "f_lang", 1)
    check("n_jsonld", count(lambda r: bool(r["jsonld"]) and not r["jsonld_broken"]), "f_jsonld", 3)
    check("n_schema", count(lambda r: not r["jsonld_missing"]), "f_schema", 2)
    check("n_faq", count(lambda r: not r["faq_missing"]), "f_faq", 3, 0.9)
    check("n_qheading", count(lambda r: r["question_headings"] > 0), "f_qheading", 3)
    check("n_words", count(lambda r: r["words"] >= 300), "f_words", 2)
    check("n_date", count(lambda r: r["date_visible"]), "f_date", 2)
    check("n_date_match", count(lambda r: not r["date_mismatch"]), "f_date_match", 1)
    check("n_external", count(lambda r: r["external_sources"] > 0), "f_external", 3)
    check("n_quote", count(lambda r: r["quotes"] > 0 or r["numbers"] >= 3), "f_quote", 2)
    check("n_internal", count(lambda r: r["internal_links"] >= 2), "f_internal", 1)
    s.add("info", t("n_official"), t("m_official", ok=count(lambda r: r["official_sources"] > 0), n=n), "", 0)
    s.add("info", t("n_early"), t("m_early", ok=count(lambda r: r["early_answer"]), n=n), "", 0)


def score(s: Result) -> int:
    total = sum(k["weight"] for k in s.checks if k["status"] != "info")
    got = sum(k["weight"] * (1 if k["status"] == "ok" else 0.5 if k["status"] == "warn" else 0)
              for k in s.checks if k["status"] != "info")
    return round(100 * got / total) if total else 0


def top_fixes(s: Result, n=5) -> list[str]:
    order = sorted((k for k in s.checks if k["status"] in ("fail", "warn") and k["fix"]),
                   key=lambda k: (k["status"] != "fail", -k["weight"]))
    return [f"{k['name']}: {k['fix']}" for k in order[:n]]


# ---------------- IndexNow ----------------
def submit_indexnow(root: str, key: str, urls: list[str]) -> str:
    host = urllib.parse.urlparse(root).netloc
    kf = fetch(f"{root}/{key}.txt")
    warn = ""
    if kf["status"] != 200 or key not in text(kf):
        warn = t("i_warn", u=f"{root}/{key}.txt")
    body = json.dumps({"host": host, "key": key, "keyLocation": f"{root}/{key}.txt",
                       "urlList": urls[:10000]}).encode()
    y = fetch("https://api.indexnow.org/indexnow", data=body,
              headers={"Content-Type": "application/json; charset=utf-8"})
    code = y["status"]
    meaning = t(f"i_{code}") if f"i_{code}" in MSG else (y["error"] or "")
    return f"IndexNow: HTTP {code} {meaning} ({len(urls)} URL){warn}"


# ---------------- main ----------------
def main(argv: list[str] | None = None):
    global LANG
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except Exception:
        pass
    ap = argparse.ArgumentParser(
        prog="geo_audit.py",
        description="GEO audit: check whether AI search engines (ChatGPT, Perplexity, Claude, Gemini, "
                    "Copilot, Google AI Overviews) can crawl, read and cite a website. Read-only by default.",
        epilog="Example: python geo_audit.py https://example.com --lang en")
    ap.add_argument("site", help="site URL or domain, e.g. https://example.com")
    ap.add_argument("--lang", choices=("en", "tr"), default="en", help="output language (default: en)")
    ap.add_argument("--json", action="store_true", help="print machine-readable JSON instead of text")
    ap.add_argument("--max", type=int, default=30, help="max pages to audit from the sitemap (<=30, default 30)")
    ap.add_argument("--banned", metavar="FILE",
                    help="file with one banned phrase per line; pages containing one fail the audit")
    ap.add_argument("--indexnow", metavar="KEY",
                    help="submit sitemap URLs to IndexNow (key file must be served at /<KEY>.txt)")
    ap.add_argument("--version", action="version", version=f"%(prog)s {__version__}")
    a = ap.parse_args(argv)
    LANG = a.lang
    site = a.site.strip()
    if not site.startswith("http"):
        site = "https://" + site
    pu = urllib.parse.urlparse(site)
    root = f"{pu.scheme}://{pu.netloc}"
    banned: list[str] = []
    if a.banned:
        with open(a.banned, encoding="utf-8") as f:
            banned = [l.strip() for l in f if l.strip() and not l.startswith("#")]

    s = Result()
    sitemaps = check_robots(root, s)
    allurls = check_sitemap(root, sitemaps, s)
    check_llms(root, s)
    check_markdown(root, s)

    targets = allurls[: max(1, min(a.max, 30))] or [(root + "/", None)]
    pages: list[dict] = []
    with cf.ThreadPoolExecutor(max_workers=8) as ex:
        fut = [ex.submit(check_page, u, lm, banned) for u, lm in targets]
        for f in cf.as_completed(fut):
            pages.append(f.result())
        botf = {ex.submit(check_bots, r["url"]): r for r in pages if r["status"] == 200}
        for f in cf.as_completed(botf):
            botf[f]["bot"] = f.result()
    order = {u: i for i, (u, _) in enumerate(targets)}
    pages.sort(key=lambda r: order.get(r["url"], 0))
    site_checks(pages, s, bool(banned))
    if len(allurls) > len(targets):
        s.add("info", t("n_scope"), t("m_scope", n=len(allurls), k=len(targets)), "", 0)
    if not time_left():
        s.add("warn", t("n_time"), t("m_time"), t("f_time"), 0)

    inx = submit_indexnow(root, a.indexnow, [u for u, _ in allurls] or [root + "/"]) if a.indexnow else None
    p = score(s)
    fixes = top_fixes(s)
    secs = round(time.monotonic() - START, 1)
    note = t("r_note")

    if a.json:
        print(json.dumps({"site": root, "score": p, "checks": s.checks, "pages": pages,
                          "top_fixes": fixes, "indexnow": inx, "seconds": secs, "note": note,
                          "version": __version__}, ensure_ascii=False, indent=1))
        return
    print(t("r_header", root=root, ts=time.strftime("%Y-%m-%d %H:%M"), s=secs))
    print("-" * 70)
    for k in s.checks:
        line = f"{SYMBOL[k['status']]} {k['name']}: {k['message']}"
        if k["fix"] and k["status"] in ("fail", "warn"):
            line += f"  → {k['fix']}"
        print(line)
    print("-" * 70 + "\n" + t("r_pages"))
    for r in pages:
        issues = page_issues(r)
        path = urllib.parse.urlparse(r["url"]).path or "/"
        severe = r["status"] != 200 or r.get("noindex") or r.get("banned")
        print(f"{'✔' if not issues else '✘' if severe else '!'} {path}"
              + ("" if not issues else "  — " + "; ".join(issues)))
    print("-" * 70)
    if inx:
        print(inx)
    print(t("r_score", p=p))
    if fixes:
        print(t("r_top"))
        for i, o in enumerate(fixes, 1):
            print(f"  {i}. {o}")
    print(note)


if __name__ == "__main__":
    main()
