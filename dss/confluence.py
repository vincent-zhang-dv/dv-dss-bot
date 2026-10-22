"""`confluence_*` tools: search and read DarkVision's Confluence pages (SOPs, how-tos). Read-only.

Uses a read-only account's API token (Atlassian Cloud REST, basic auth), standard library only:

	CONFLUENCE_URL=https://<site>.atlassian.net  CONFLUENCE_EMAIL=...  CONFLUENCE_TOKEN=...
	CONFLUENCE_SPACES=DD,OPS   (optional: only these spaces)

Without them the tools say what to set instead of failing obscurely.
"""

import base64
import json
import os
import re
import urllib.error
import urllib.parse
import urllib.request
from html.parser import HTMLParser

TIMEOUT_S = 30
MAX_PAGE_CHARS = 30000  # one page into Claude's context
ENV = ('CONFLUENCE_URL', 'CONFLUENCE_EMAIL', 'CONFLUENCE_TOKEN')
HEADINGS = ('h1', 'h2', 'h3', 'h4', 'h5', 'h6')
BREAKS = frozenset({'p', 'br', 'div', 'tr', 'pre', 'table', *HEADINGS})  # tags that end a line


class ConfluenceError(RuntimeError):
	"""A failure Claude should read as it is (not configured, no access, not found)."""


def search(text: str, limit: int = 10) -> list[dict]:
	"""Pages matching *text* (Confluence full-text search), best first."""
	text = text.strip()
	if not text:
		raise ConfluenceError('search text is empty')
	cql = f'type = page AND text ~ {json.dumps(text)}'
	spaces = _spaces()
	if spaces:
		quoted = ', '.join(json.dumps(space) for space in spaces)
		cql += f' AND space in ({quoted})'
	found = _get('/wiki/rest/api/search', {'cql': cql, 'limit': max(1, min(limit, 25))})
	pages = []
	for hit in found.get('results', []):
		content = hit.get('content') or {}
		pages.append(
			{
				'id': content.get('id', ''),
				'title': hit.get('title') or content.get('title', ''),
				'space': (hit.get('resultGlobalContainer') or {}).get('title', ''),
				'excerpt': _strip_highlight(hit.get('excerpt', '')),
				'updated': hit.get('lastModified', ''),
				'url': _base() + '/wiki' + hit.get('url', ''),
			}
		)
	return pages


def page(page_id: str) -> dict:
	"""One page as plain text (headings, lists and table rows kept as lines)."""
	if not re.fullmatch(r'\d{1,20}', str(page_id)):
		raise ConfluenceError(f'page id {page_id!r} must be the number from search results')
	found = _get(f'/wiki/api/v2/pages/{page_id}', {'body-format': 'storage'})
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

		Returns id, title, space, excerpt, updated and url per page; read one with
		confluence_page. Cite the page url when you use it.
		"""
		return search(text, limit)

	@server.tool()
	def confluence_page(page_id: str) -> dict:
		"""The text of the Confluence page *page_id* (an id from confluence_search)."""
		return page(page_id)
