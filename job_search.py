#!/usr/bin/env python3
"""Assistant-operated public job source lookup. It never applies or signs in."""
from __future__ import annotations

import argparse
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from html.parser import HTMLParser
import json
from pathlib import Path
import re
import ssl
import urllib.error
import urllib.parse
import urllib.request

ROOT = Path(__file__).resolve().parent
MAX_PAGE_BYTES = 3_000_000


class SearchError(ValueError):
    pass


def load_sources(path=ROOT / 'search_sources.json'):
    data = json.loads(Path(path).read_text(encoding='utf-8'))
    if data.get('schema_version') != 1 or not isinstance(data.get('sources'), list):
        raise SearchError('Unsupported source catalog')
    ids = set()
    for source in data['sources']:
        if not isinstance(source, dict) or not isinstance(source.get('id'), str) or source['id'] in ids:
            raise SearchError('Invalid or duplicate source id')
        ids.add(source['id'])
        url = urllib.parse.urlsplit(source.get('search_url', ''))
        if url.scheme != 'https' or not url.hostname or url.username or url.password or url.fragment:
            raise SearchError('Source must be a public HTTPS URL without credentials')
        if not isinstance(source.get('query_params', {}), dict) or any(not isinstance(k, str) or not isinstance(v, str) for k, v in source.get('query_params', {}).items()):
            raise SearchError('Invalid source query parameters')
        if not isinstance(source.get('modes'), list) or any(m not in {'employment', 'contract'} for m in source['modes']):
            raise SearchError('Invalid source employment modes')
        if source.get('state') not in {'verified', 'limited', 'unusable'}:
            raise SearchError('Source capability must be explicit')
        re.compile(source.get('listing_pattern', r'(?!)'))
    return data['sources']


def search_url(source, query, mode='all'):
    if not isinstance(query, str) or not query.strip() or len(query) > 200:
        raise SearchError('Search query must contain 1–200 characters')
    parts = urllib.parse.urlsplit(source['search_url'])
    params = urllib.parse.parse_qsl(parts.query, keep_blank_values=True)
    params += [(key, value.replace('{query}', query.strip())) for key, value in source.get('query_params', {}).items()]
    params += list(source.get('mode_query_params', {}).get(mode, {}).items())
    return urllib.parse.urlunsplit((parts.scheme, parts.netloc, parts.path, urllib.parse.urlencode(params), ''))


class PublicPage(HTMLParser):
    def __init__(self):
        super().__init__()
        self.ignored = 0
        self.parts, self.links, self.inputs = [], [], []
        self.anchor = None

    def handle_starttag(self, tag, attrs):
        attrs = dict(attrs)
        if tag in {'script', 'style'}:
            self.ignored += 1
        if tag == 'a':
            self.anchor = [attrs.get('href', ''), []]
        if tag == 'input':
            self.inputs.append(attrs.get('value', ''))

    def handle_endtag(self, tag):
        if tag in {'script', 'style'}:
            self.ignored = max(0, self.ignored - 1)
        if tag == 'a' and self.anchor:
            self.links.append((self.anchor[0], ' '.join(self.anchor[1])))
            self.anchor = None

    def handle_data(self, value):
        if self.ignored or not value.strip():
            return
        self.parts.append(value.strip())
        if self.anchor:
            self.anchor[1].append(value.strip())


def analyze(source, query, final_url, html):
    page = PublicPage()
    page.feed(html)
    visible = ' '.join(page.parts)
    lowered = visible.casefold()
    if any(s in lowered for s in ['verify you are human', 'please complete the following challenge', 'access denied', '人間であることを確認']):
        return {'status': 'access_check', 'listings': [], 'note': 'The site requires an access check; no bypass attempted.'}
    expected_host = urllib.parse.urlsplit(source['search_url']).hostname
    if urllib.parse.urlsplit(final_url).hostname != expected_host:
        return {'status': 'redirected', 'listings': [], 'note': 'Destination changed; inspect it before using results.'}
    pattern = re.compile(source.get('listing_pattern', r'(?!)'))
    links = {}
    for href, title in page.links:
        target = urllib.parse.urljoin(final_url, href)
        parsed = urllib.parse.urlsplit(target)
        if parsed.scheme != 'https' or parsed.hostname != expected_host or parsed.username or parsed.password or not pattern.search(parsed.path) or not title.strip():
            continue
        target = urllib.parse.urlunsplit((parsed.scheme, parsed.netloc, parsed.path, parsed.query, ''))
        links.setdefault(target, {'title_excerpt': title[:600], 'url': target})
    filtered = any('{query}' in v for v in source.get('query_params', {}).values())
    echoed = query in page.inputs or query in visible
    if not filtered:
        status = 'browsing_only'
    elif not echoed:
        status = 'query_unconfirmed'
    elif not links:
        status = 'no_listing_extracted'
    else:
        status = 'listings_extracted'
    return {'status': status, 'query_echoed': echoed, 'listings': list(links.values())[:30],
            'note': 'Extracted links are research leads, not verified recommendations. Read each listing and its conditions.',
            'page_excerpt': visible[:24000]}


def fetch_source(source, query, timeout=20, mode='all'):
    url = search_url(source, query, mode)
    result = {'source': source['id'], 'name': source['name'], 'url': url,
              'checked_at': datetime.now(timezone.utc).isoformat(timespec='seconds'), 'catalog_state': source['state']}
    if source['state'] == 'unusable':
        return {**result, 'status': 'not_queried', 'listings': [], 'note': source.get('notes', '')}
    try:
        request = urllib.request.Request(url, headers={'User-Agent': 'LATTE public job research/1.0'})
        with urllib.request.urlopen(request, timeout=timeout, context=ssl.create_default_context()) as response:
            content = response.read(MAX_PAGE_BYTES + 1)
            if len(content) > MAX_PAGE_BYTES:
                return {**result, 'status': 'response_too_large', 'listings': []}
            charset = response.headers.get_content_charset() or 'utf-8'
            analysis = analyze(source, query, response.url, content.decode(charset, errors='replace'))
            return {**result, 'http_status': response.status, **analysis}
    except urllib.error.HTTPError as error:
        return {**result, 'status': 'http_error', 'http_status': error.code, 'listings': []}
    except (urllib.error.URLError, TimeoutError, OSError):
        return {**result, 'status': 'connection_error', 'listings': []}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('query')
    parser.add_argument('--mode', choices=['all', 'employment', 'contract'], default='all')
    parser.add_argument('--source', action='append', default=[])
    parser.add_argument('--catalog', default=str(ROOT / 'search_sources.json'))
    parser.add_argument('--output', help='Save the research report locally; never publish it automatically')
    parser.add_argument('--list-only', action='store_true', help='Print configured routes without network requests')
    args = parser.parse_args()
    sources = load_sources(args.catalog)
    if set(args.source) - {s['id'] for s in sources}:
        parser.error('Unknown source id')
    sources = [s for s in sources if (not args.source or s['id'] in args.source) and (args.mode == 'all' or args.mode in s['modes'])]
    if args.list_only:
        reports = [{'source': s['id'], 'url': search_url(s, args.query, args.mode), 'state': s['state'], 'notes': s.get('notes', '')} for s in sources]
    else:
        with ThreadPoolExecutor(max_workers=3) as pool:
            reports = list(pool.map(lambda source: fetch_source(source, args.query, mode=args.mode), sources))
    report = json.dumps({'query': args.query, 'mode': args.mode, 'sources': reports}, ensure_ascii=False, indent=2)
    if args.output:
        path = Path(args.output)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(report, encoding='utf-8')
        path.chmod(0o600)
        print(json.dumps({'saved': str(path), 'sources': [{'source': r['source'], 'status': r.get('status', r.get('state')), 'links': len(r.get('listings', []))} for r in reports]}, ensure_ascii=False))
    else:
        print(report)


if __name__ == '__main__':
    main()
