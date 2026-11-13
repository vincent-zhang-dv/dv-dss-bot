"""`confluence_*` tools: search and read DarkVision's Confluence pages (SOPs, how-tos). Read-only.

Uses a read-only account's API token (Atlassian Cloud REST, basic auth), standard library only:

	CONFLUENCE_URL=https://<site>.atlassian.net  CONFLUENCE_EMAIL=...  CONFLUENCE_TOKEN=...
	CONFLUENCE_SPACES=DD,OPS   (optional: only these spaces)

Without them the tools say what to set instead of failing obscurely. In a replay
(:mod:`dss.cutoff`) pages created later are not found, a page is read as it was then, and a search
hit changed since is judged and shown by its text then. The versions read are kept in ARCHIVE_DIR
(a page's version never changes), so the archive of pages as they were grows with each replay.
"""

import base64
import json
import os
import re
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timedelta
from html.parser import HTMLParser

from dss import cutoff
from dss.worker import ROOT

TIMEOUT_S = 30
MAX_PAGE_CHARS = 30000  # one page into Claude's context
MAX_VERSIONS = 250  # a page's versions per request in a replay, newest first
MAX_VERSION_PAGES = 8
ARCHIVE_DIR = ROOT / 'out' / 'archive' / 'confluence'  # <page id>-<version>.json, as read
EXCERPT_CHARS = 120  # each side of the first searched word, in a replay's excerpt
# A page last edited this long before the ticket may describe an older Cerberus
OUT_OF_DATE_AFTER = timedelta(days=365)
ENV = ('CONFLUENCE_URL', 'CONFLUENCE_EMAIL', 'CONFLUENCE_TOKEN')
HEADINGS = ('h1', 'h2', 'h3', 'h4', 'h5', 'h6')
BREAKS = frozenset({'p', 'br', 'div', 'tr', 'pre', 'table', *HEADINGS})  # tags that end a line


class ConfluenceError(RuntimeError):
	"""A failure Claude should read as it is (not configured, no access, not found)."""


def search(text: str, limit: int = 10) -> list[dict]:
	"""Pages matching *text* (Confluence full-text search), best first.

	In a replay, only pages created by the cutoff's day are searched. Confluence matches today's
	text, so a page changed since is kept only if its version then still has the searched words
	(:func:`dss.cutoff.matches`), and it shows that version's title and an excerpt from it.
	"""
	text = text.strip()
	if not text:
		raise ConfluenceError('search text is empty')
	cql = f'type = page AND text ~ {json.dumps(text)}'
	spaces = _spaces()
	if spaces:
		quoted = ', '.join(json.dumps(space) for space in spaces)
		cql += f' AND space in ({quoted})'
	then = cutoff.when()
	if then is not None:
		cql += f' AND created < "{(then + timedelta(days=1)).strftime("%Y-%m-%d")}"'
	found = _get('/wiki/rest/api/search', {'cql': cql, 'limit': max(1, min(limit, 25))})
	terms = [cutoff.search_terms(text)]
	pages = []
	for hit in found.get('results', []):
		content = hit.get('content') or {}
		shown = {
			'id': content.get('id', ''),
			'title': hit.get('title') or content.get('title', ''),
			'space': (hit.get('resultGlobalContainer') or {}).get('title', ''),
			'excerpt': _strip_highlight(hit.get('excerpt', '')),
			'updated': hit.get('lastModified', ''),
			'url': _base() + '/wiki' + hit.get('url', ''),
		}
		if _later(shown['updated']):  # judged and shown by the version it had then
			try:
				old = page(shown['id'])
			except ConfluenceError:  # created after the cutoff, on its day
				continue
			if not cutoff.matches(f'{old["title"]}\n{old["text"]}', terms):
				continue  # it matches only by words written after the ticket
			shown.update(title=old['title'], updated=old['updated'])
			shown['excerpt'] = _excerpt(old['text'], terms[0])
		pages.append(_aged(shown))
	return pages


def page(page_id: str) -> dict:
	"""One page as plain text (headings, lists and table rows kept as lines), marked when it may be
	out of date (:func:`_aged`); in a replay, the version it had at the cutoff, from ARCHIVE_DIR
	once read."""
	if not re.fullmatch(r'\d{1,20}', str(page_id)):
		raise ConfluenceError(f'page id {page_id!r} must be the number from search results')
	if cutoff.when() is None:
		found = _get(f'/wiki/api/v2/pages/{page_id}', {'body-format': 'storage'})
		return _aged(_shown(page_id, found))
	number = _version_then(page_id)
	kept = ARCHIVE_DIR / f'{page_id}-{number}.json'
	if kept.exists():
		return _aged(json.loads(kept.read_text(encoding='utf-8')))
	params = {'body-format': 'storage', 'version': str(number)}
	found = _shown(page_id, _get(f'/wiki/api/v2/pages/{page_id}', params))
	ARCHIVE_DIR.mkdir(parents=True, exist_ok=True)
	kept.write_text(json.dumps(found), encoding='utf-8')
	return _aged(found)


def _aged(shown: dict) -> dict:
	"""*shown* (a page or a search hit) with `may_be_out_of_date` when it was last edited more than
	OUT_OF_DATE_AFTER before the ticket: before the replay's cutoff, else before now. Such a page
	may describe an older Cerberus."""
	try:
		edited = datetime.fromisoformat(shown.get('updated', '')).astimezone()  # naive: local
	except ValueError:  # no time given
		return shown
	ticket = cutoff.when()
	if ticket is None:  # a live triage: the ticket is new
		ticket = datetime.now().astimezone()
	if ticket - edited <= OUT_OF_DATE_AFTER:
		return shown
	years = (ticket - edited).days / 365
	note = f'last edited {edited.astimezone():%Y-%m-%d}, {years:.1f} years before the ticket'
	return {**shown, 'may_be_out_of_date': note}


def _shown(page_id: str, found: dict) -> dict:
	"""A page as `confluence_page` gives it, from the API's *found*."""
	text = to_text(found.get('body', {}).get('storage', {}).get('value', ''))
	if len(text) > MAX_PAGE_CHARS:
		text = text[:MAX_PAGE_CHARS] + f'\n[... cut at {MAX_PAGE_CHARS} characters]'
	links = found.get('_links', {})
	return {
		'id': str(page_id),
		'title': found.get('title', ''),
		'updated': (found.get('version') or {}).get('createdAt', ''),
		'url': _base() + '/wiki' + links.get('webui', ''),
		'text': text,
	}


def _excerpt(text: str, terms: list[str]) -> str:
	"""The part of *text* around the first word that starts with one of *terms*; its start when
	none does."""
	first = 0
	if terms:
		words = '|'.join(re.escape(term) for term in terms)
		found = re.search(rf'\b(?:{words})', text.lower())
		if found:
			first = found.start()
	start = max(0, first - EXCERPT_CHARS)
	return ' '.join(text[start : first + EXCERPT_CHARS].split())


def _version_then(page_id: str) -> int:
	"""The number of the version page *page_id* had at the replay's cutoff, paging back through
	its versions (newest first) as far as MAX_VERSION_PAGES."""
	params: dict[str, object] = {'limit': MAX_VERSIONS}
	for _ in range(MAX_VERSION_PAGES):
		found = _get(f'/wiki/api/v2/pages/{page_id}/versions', params)
		for version in found.get('results', []):
			if version.get('createdAt') and not _later(version['createdAt']):
				return int(version['number'])
		following = urllib.parse.urlsplit((found.get('_links') or {}).get('next', ''))
		cursor = urllib.parse.parse_qs(following.query).get('cursor')
		if not cursor:
			break
		params = {'limit': MAX_VERSIONS, 'cursor': cursor[0]}
	raise ConfluenceError(cutoff.refusal(f'page {page_id}'))


def _later(value: str) -> bool:
	"""Whether the Confluence time *value* (ISO, as 2026-10-26T18:25:21.778Z) came after the
	replay's cutoff; False when it is not a time."""
	try:
		moment = datetime.fromisoformat(value)
	except ValueError:
		return False
	return cutoff.after(moment)


def to_text(storage: str) -> str:
	"""Confluence storage XHTML as readable text."""
	parser = _Text()
	parser.feed(storage)
	lines = [' '.join(line.split()) for line in ''.join(parser.out).splitlines()]
	return re.sub(r'\n{3,}', '\n\n', '\n'.join(lines)).strip()


class _Text(HTMLParser):
	def __init__(self) -> None:
		super().__init__(convert_charrefs=True)
		self.out: list[str] = []

	def handle_starttag(self, tag, attrs) -> None:
		if tag in BREAKS:
			self.out.append('\n')
		if tag in HEADINGS:
			self.out.append('#' * int(tag[1]) + ' ')
		elif tag == 'li':
			self.out.append('\n- ')
		elif tag in ('td', 'th'):
			self.out.append(' | ')

	def handle_endtag(self, tag) -> None:
		if tag in BREAKS:
			self.out.append('\n')

	def handle_data(self, data) -> None:
		self.out.append(data)


def _spaces() -> list[str]:
	"""The spaces named in CONFLUENCE_SPACES; empty means every space."""
	spaces = []
	for space in os.environ.get('CONFLUENCE_SPACES', '').split(','):
		if space.strip():
			spaces.append(space.strip())
	return spaces


def _get(path: str, params: dict) -> dict:
	missing = []
	for name in ENV:
		if not os.environ.get(name):
			missing.append(name)
	if missing:
		raise ConfluenceError(
			f'Confluence is not set up on this machine: set {", ".join(missing)} '
			'(a read-only account and its API token).'
		)
	auth = f'{os.environ["CONFLUENCE_EMAIL"]}:{os.environ["CONFLUENCE_TOKEN"]}'
	request = urllib.request.Request(
		f'{_base()}{path}?{urllib.parse.urlencode(params)}',
		headers={
			'Authorization': 'Basic ' + base64.b64encode(auth.encode()).decode(),
			'Accept': 'application/json',
		},
	)
	try:
		with urllib.request.urlopen(request, timeout=TIMEOUT_S) as response:
			return json.loads(response.read().decode('utf-8'))
	except urllib.error.HTTPError as error:
		reason = {
			401: 'the Confluence token was refused',
			403: 'this account cannot read that space',
			404: 'no such page (or no access to it)',
		}.get(error.code, f'Confluence answered {error.code} {error.reason}')
		raise ConfluenceError(reason) from error
	except (urllib.error.URLError, TimeoutError) as error:
		raise ConfluenceError(f'cannot reach Confluence at {_base()}: {error}') from error


def _base() -> str:
	return os.environ.get('CONFLUENCE_URL', '').rstrip('/').removesuffix('/wiki')


def _strip_highlight(excerpt: str) -> str:
	return ' '.join(re.sub(r'@@@(end)?hl@@@', '', to_text(excerpt)).split())


def register(server) -> None:
	@server.tool()
	def confluence_search(text: str, limit: int = 10) -> list[dict]:
		"""Search DarkVision's Confluence (SOPs, how-tos, known issues) for *text*.

		Returns id, title, space, excerpt, updated and url per page, and may_be_out_of_date for
		a page last edited over a year before the ticket; read one with confluence_page. Cite the
		page url when you use it.
		"""
		return search(text, limit)

	@server.tool()
	def confluence_page(page_id: str) -> dict:
		"""The text of the Confluence page *page_id* (an id from confluence_search), with
		may_be_out_of_date when it was last edited over a year before the ticket."""
		return page(page_id)
