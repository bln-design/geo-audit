![geo-audit](assets/banner.png)

# geo-audit

[![Python](https://img.shields.io/badge/python-3.10%2B-blue)](https://www.python.org/)
[![License: MIT](https://img.shields.io/badge/license-MIT-green)](LICENSE)
[![Dependencies](https://img.shields.io/badge/dependencies-none-lightgrey)](geo_audit.py)

A single-file, read-only script that checks whether AI search engines can crawl, read and cite your website.

## Why

More and more traffic starts in an AI answer instead of a list of links: ChatGPT search, Perplexity, Claude, Gemini, Microsoft Copilot and Google AI Overviews. These systems depend on things classic SEO tools rarely check together. Is `OAI-SearchBot` allowed in `robots.txt`? Does your WAF return 403 to `PerplexityBot`? Does your FAQPage markup match the text on the page? Is there a visible date, a source link, a number worth quoting?

`geo-audit` checks those in one run, prints a score out of 100 and lists the five fixes that matter most.

## Quick start

```sh
python geo_audit.py https://example.com
```

There is nothing to install. It runs on the Python 3.10+ standard library and finishes within about two minutes (10 s per request, 110 s total budget, at most 30 pages from your sitemap).

```
python geo_audit.py https://example.com --json          # machine-readable output
python geo_audit.py https://example.com --max 10        # audit fewer pages
python geo_audit.py https://example.com --banned b.txt  # fail pages containing listed phrases
python geo_audit.py https://example.com --lang tr       # Turkish output
```

## Example output

Real run against `https://example.com` (shortened):

```
GEO audit: https://example.com  (2026-10-23 21:56, 1.7 s)
----------------------------------------------------------------------
! robots.txt: not found (HTTP 404)  → add robots.txt: 'User-agent: *' + 'Allow: /' + 'Sitemap: <url>'
✘ sitemap.xml: could not read: https://example.com/sitemap.xml: HTTP 404  → generate sitemap.xml at the root, ...
· llms.txt: missing (optional; Google does not use it, citation effect unproven)
· Accept: text/markdown: returns HTML (optional: Cloudflare Markdown for Agents / .md copies)
✔ HTTP 200: 1/1 pages pass
✔ bot UA access: 1/1 pages return 200 to every bot UA
✘ JSON-LD: 0/1 pages pass  → add Organization+WebSite (home) and Article/FAQPage (content) JSON-LD; it must parse
✘ question-style subheadings: 0/1 pages pass  → turn real user questions into h2/h3 with a 1-2 sentence answer ...
✘ external sources: 0/1 pages pass  → link to official/primary sources (GEO paper: citing sources up to +28%)
...
----------------------------------------------------------------------
Pages (✔ = no issues):
! /  — title 14ch; desc 0ch; h1=0; no canonical; no JSON-LD; 0 question headings; 27 words; no visible date; ...
----------------------------------------------------------------------
SCORE: 43/100
Top fixes:
  1. sitemap.xml: generate sitemap.xml at the root, reference it in robots.txt, submit to GSC + Bing Webmaster
  2. JSON-LD: add Organization+WebSite (home) and Article/FAQPage (content) JSON-LD; it must parse
  3. question-style subheadings: turn real user questions into h2/h3 with a 1-2 sentence answer right below
  4. external sources: link to official/primary sources (GEO paper: citing sources up to +28%)
  5. title 30-65 chars: make the title 30-65 characters, one line naming the page's question/topic
```

`✔` pass, `!` warning, `✘` fail, `·` informational (not scored).

## What it checks

| Check | What it looks at | Weight |
|---|---|---|
| robots.txt: OAI-SearchBot | ChatGPT search crawler allowed on `/` | 6 |
| robots.txt: AI search bots | ChatGPT-User, Bingbot, Googlebot, PerplexityBot, Perplexity-User, Claude-SearchBot, Claude-User, DuckAssistBot, MistralAI-User | 4 |
| robots.txt: training bots | GPTBot, ClaudeBot, Google-Extended, CCBot and others; reported only, blocking them is your choice | info |
| robots.txt / Sitemap line | file exists and points to a sitemap | 2 / 1 |
| sitemap.xml | readable (index files followed), every URL has `<lastmod>`, single host | 4 / 1 |
| Bot user-agent access | each page fetched as Googlebot, Bingbot, OAI-SearchBot, ClaudeBot, PerplexityBot; any non-200 fails. Cloudflare is detected and named in the fix | 5 |
| noindex | `meta robots` and `X-Robots-Tag` | 5 |
| llms.txt | present, and follows the llmstxt.org shape (H1, `>` summary, `##` sections, links) | info |
| JSON-LD | present and parses; required fields for Article, BlogPosting, NewsArticle, Organization, WebSite, FAQPage | 3 / 2 |
| FAQPage consistency | every `Question.name` in FAQPage appears in the visible text | 3 |
| Visible date | "Last updated" style text or `<time>`, and it matches JSON-LD `dateModified` | 2 / 1 |
| Question headings | at least one h2/h3 phrased as a question (English and Turkish patterns) | 3 |
| External sources | links to other hosts; links to .gov/.edu/Wikipedia reported separately | 3 |
| Quote/statistic signals | `<blockquote>`/`<q>` or at least 3 numbers, percentages or standard references | 2 |
| Internal links | at least 2 same-host links per page | 1 |
| Basics | title 30-65 chars, description 70-160 chars, one h1, canonical, `lang`, 300+ words | 1-4 |
| Accept: text/markdown | whether the home page serves Markdown on request | info |
| Banned phrases | optional `--banned` file; any hit fails the page | 6 |

The score is the weighted share of passing checks, with warnings counted as half.

## Background

The content checks (sources, quotations, statistics) follow *GEO: Generative Engine Optimization* by Aggarwal, Murahari, Rajpurohit, Kalyan, Narasimhan and Deshpande ([arXiv:2311.09735](https://arxiv.org/abs/2311.09735), KDD 2024). The paper reports visibility gains of up to 40% in generative engine responses, with quotation addition, statistics addition and source citation among the strongest methods.

Those numbers come from a controlled benchmark with a simulated generative engine. They are not guarantees for any production AI search product, and the percentages in the fix hints should be read that way. Never add quotes or statistics you cannot verify.

## IndexNow (optional)

```sh
python geo_audit.py https://example.com --indexnow YOUR_KEY
```

This submits every sitemap URL to `api.indexnow.org` (used by Bing, and through Bing by Copilot and ChatGPT search). The key file must be served at `https://example.com/YOUR_KEY.txt` and contain the key; the script warns when it is not. This is the only option that sends data anywhere; everything else is read-only GET requests.

## Limits

- Bot user-agent tests run from your machine's IP. A site can allow the user-agent string but block or verify by IP (Cloudflare, WAFs), so a pass here does not prove the real crawler gets in. For authoritative answers use Cloudflare AI Crawl Control, Bing Webmaster Tools URL Inspection and Google Search Console URL Inspection.
- Pages are analysed as raw HTML. JavaScript is not executed, so client-rendered content counts as missing, which is also how many AI crawlers see it.
- At most 30 sitemap URLs are audited per run.
- Question, date and number detection is pattern-based (English and Turkish). Treat "answer in first 150 words" as a hint.

## CI example

Run the audit after each deploy and keep the JSON report:

```yaml
name: geo-audit
on:
  workflow_dispatch:
  schedule:
    - cron: "0 6 * * 1"
jobs:
  audit:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4
      - uses: actions/setup-python@v5
        with:
          python-version: "3.12"
      - run: python geo_audit.py https://example.com --json > geo-report.json
      - uses: actions/upload-artifact@v4
        with:
          name: geo-report
          path: geo-report.json
```

To fail the job below a threshold, read `score` from the JSON, e.g. `python -c "import json,sys; sys.exit(json.load(open('geo-report.json'))['score'] < 70)"`.

## License

MIT. See [LICENSE](LICENSE).

Turkish output: `--lang tr`
