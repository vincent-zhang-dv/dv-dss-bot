"""`jira_*` tools: DSS tickets and their history, read-only, through the Jira Cloud REST API.

Uses a read-only account's API token (basic auth), standard library only. The settings are
JIRA_URL, JIRA_EMAIL and JIRA_TOKEN; each one falls back to its CONFLUENCE_* value, since one
Atlassian site and token serve both. Only the fields triage needs are requested, so a ticket
costs a few hundred tokens instead of the whole issue.

In a replay (:mod:`dss.cutoff`) each ticket is shown as it stood when the replayed ticket was
posted (:func:`as_posted`), and tickets created later are not shown.
"""

import base64
import json
import os
import re
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timedelta

from dss import cutoff

TIMEOUT_S = 30
ISSUE_KEY = re.compile(r'[A-Z][A-Z0-9]+-\d+')
PROJECT_KEY = re.compile(r'[A-Z][A-Z0-9]+')
# setting -> the Confluence setting it falls back to
ENV = {
	'JIRA_URL': 'CONFLUENCE_URL',
	'JIRA_EMAIL': 'CONFLUENCE_EMAIL',
	'JIRA_TOKEN': 'CONFLUENCE_TOKEN',
}
# name in the answer -> Jira field id
TICKET_FIELDS = {
	'summary': 'summary',
	'description': 'description',
	'status': 'status',
	'resolution': 'resolution',
	'fix_versions': 'fixVersions',
	'reporter': 'reporter',
	'links': 'issuelinks',
	'comments': 'comment',
	'attachments': 'attachment',
	'project_path': 'customfield_10823',
	'toolbox': 'customfield_10824',
	'outcome': 'customfield_10825',
	'client': 'customfield_13663',
	'dvt': 'customfield_13664',
}
HISTORY_FIELDS = {
	'summary': 'summary',
	'status': 'status',
	'resolution': 'resolution',
	'fix_versions': 'fixVersions',
	'created': 'created',
	'toolbox': 'customfield_10824',
	'outcome': 'customfield_10825',
}
TEXT_FIELDS = {'summary': 'summary', 'description': 'description'}  # what `text ~` searches
SEARCHED_FIELDS = {**HISTORY_FIELDS, 'description': 'description'}  # a replay checks its hits
MAX_RESULTS = 20
DESCRIPTION_CHARS = 4000
MAX_COMMENTS = 10  # the newest: a long discussion ends with what was found and done
COMMENT_CHARS = 1500
JIRA_TIME = '%Y-%m-%dT%H:%M:%S.%f%z'  # 2026-10-22T10:00:00.000-0700
MAX_CHANGELOG_PAGES = 20  # of 100 changes each, read when a ticket's changelog came cut
EDITED_LATER = '(edited after the ticket; its text then is not known)'
# A replay's JQL tests only fields a ticket keeps once filed: status, resolution, the Outcome, fix
# versions, labels or links would pick tickets by what they became later. `text ~` is kept to the
# summary and description, as it also matches comments, most written later.
STEADY_FIELDS = {'project', 'key', 'issuekey', 'issuetype', 'type', 'created', 'createddate'}
STEADY_FIELDS |= {'reporter', 'summary', 'description'}
STEADY_ORDER = {'created', 'createddate', 'key', 'issuekey'}
SET_WORDS = {'in', 'and', 'or', 'not'}  # words before "(" that are no function
TEXT_SEARCH = re.compile(r'\btext\s*~\s*("(?:[^"\\]|\\.)*"|\'(?:[^\'\\]|\\.)*\')', re.IGNORECASE)
QUOTED = re.compile(r'"(?:[^"\\]|\\.)*"|\'(?:[^\'\\]|\\.)*\'')
JQL_TEST = re.compile(
	r'([\w.\[\]"]+)\s*(?:!=|!~|>=|<=|=|~|>|<)|([\w.\[\]"]+)\s+(?:not\s+in|in|is|was|changed)\b',
	re.IGNORECASE,
)
JQL_CALL = re.compile(r'(\w+)\s*\(')
RELATIVE_DATE = re.compile(r'^[-+]?\d+\s*[wdhm]$', re.IGNORECASE)  # -30d: from today, not then
SEARCHED = re.compile(r'~\s*("(?:[^"\\]|\\.)*"|\'(?:[^\'\\]|\\.)*\')')  # what a ~ looks for
ORDER_BY = re.compile(r'\border\s+by\b.*$', re.IGNORECASE | re.DOTALL)


class JiraError(RuntimeError):
	"""A failure Claude should read as it is (not configured, no access, not found)."""


def plain(value):
	"""A Jira field value without its envelope: options, statuses and users become their name."""
	if isinstance(value, list):
		return [plain(item) for item in value]
	if isinstance(value, dict):
		for key in ('value', 'displayName', 'name', 'key'):
			if key in value:
				return value[key]
	return value


def _links(links: list[dict] | None) -> list[str]:
	found = []
	for link in links or []:
		for side, verb in (('outwardIssue', 'outward'), ('inwardIssue', 'inward')):
			if side in link:
				other = link[side]
				found.append(f'{link["type"][verb]} {other["key"]}: {other["fields"]["summary"]}')
	return found


def _versions(versions: list[dict] | None) -> list[str]:
	"""Fix versions as "CERBERUS v1.1 (not released, due 2026-11-03)".

	In a replay only the name is shown: Jira's release dates are often days off, and whether a
	release was out then is what `cerb_prod_version` says.
	"""
	found = []
	for version in versions or []:
		if cutoff.when() is not None:
			found.append(str(version.get('name')))
			continue
		if version.get('released'):
			state = 'released'
			if version.get('releaseDate'):
				state += f' {version["releaseDate"]}'
		else:
			state = 'not released'
			if version.get('releaseDate'):
				state += f', due {version["releaseDate"]}'
		found.append(f'{version.get("name")} ({state})')
	return found


def _comments(field: dict | None) -> list[str]:
	"""The newest MAX_COMMENTS comments, oldest first, as "<date> <author>: <text>"."""
	comments = (field or {}).get('comments') or []
	found = []
	if len(comments) > MAX_COMMENTS:
		found.append(f'({len(comments) - MAX_COMMENTS} older comments left out)')
	for comment in comments[-MAX_COMMENTS:]:
		author = plain(comment.get('author')) or 'unknown'
		text = str(comment.get('body') or '')[:COMMENT_CHARS]
		found.append(f'{str(comment.get("created", ""))[:10]} {author}: {text}')
	return found


def _attachments(attachments: list[dict] | None) -> list[str]:
	"""Attached files as "name (type, size, date)"."""
	found = []
	for attachment in attachments or []:
		size = int(attachment.get('size') or 0) // 1024
		created = str(attachment.get('created', ''))[:10]
		found.append(
			f'{attachment.get("filename")} ({attachment.get("mimeType")}, {size:,} KB, {created})'
		)
	return found


def compact(issue: dict, fields: dict[str, str]) -> dict:
	"""*issue* (Jira REST JSON) as {name: plain value} for the *fields* asked for."""
	raw = issue.get('fields') or {}
	found = {'key': issue['key']}
	for name, field_id in fields.items():
		value = raw.get(field_id)
		if name == 'links':
			found[name] = _links(value)
		elif name == 'fix_versions':
			found[name] = _versions(value)
		elif name == 'comments':
			found[name] = _comments(value)
		elif name == 'attachments':
			found[name] = _attachments(value)
		elif name == 'description' and value:
			found[name] = str(value)[:DESCRIPTION_CHARS]
		else:
			found[name] = plain(value)
	return found


def get_issue(key: str, fields: dict[str, str] = TICKET_FIELDS) -> dict:
	"""The triage *fields* of ticket *key*; the key is checked before anything is sent. In a
	replay, as the ticket stood then (:func:`as_posted`)."""
	if not ISSUE_KEY.fullmatch(key):
		raise ValueError(f'{key!r} is not an issue key like DSS-1222')
	path = f'/rest/api/2/issue/{key}'
	if cutoff.when() is None:
		return compact(_get(path, {'fields': ','.join(fields.values())}), fields)
	asked = list(fields.values())
	if 'created' not in asked:
		asked.append('created')
	return as_posted(_get(path, {'fields': ','.join(asked), 'expand': 'changelog'}), fields)


def as_posted(issue: dict, fields: dict[str, str]) -> dict:
	"""*issue* (Jira REST JSON with its changelog) as :func:`compact` gives it, as it stood at the
	replay's cutoff.

	A ticket created later is refused (JiraError). Changes the changelog records after the cutoff
	are undone, newest first. Comments and attachments added later are left out, and the replayed
	ticket shows no comments at all: its answers are written there.
	"""
	raw = issue.get('fields') or {}
	created = _time(raw.get('created'))
	if created is not None and cutoff.after(created) and issue['key'] != cutoff.key():
		raise JiraError(cutoff.refusal(issue['key']))
	comments = []
	if issue['key'] != cutoff.key():
		for comment in (raw.get('comment') or {}).get('comments') or []:
			if _later(comment.get('created')):
				continue
			if _later(comment.get('updated')):  # Jira keeps only the edited text
				comment = {**comment, 'body': EDITED_LATER}
			comments.append(comment)
	attachments = []
	for attachment in raw.get('attachment') or []:
		if not _later(attachment.get('created')):
			attachments.append(attachment)
	then = {**raw, 'comment': {'comments': comments}, 'attachment': attachments}
	found = compact({**issue, 'fields': then}, fields)
	later = []
	for history in _histories(issue):
		moment = _time(history.get('created'))
		if moment is not None and cutoff.after(moment):
			later.append((moment, history['items']))
	later.sort(key=lambda change: change[0], reverse=True)
	names = {}
	for name, field_id in fields.items():
		names[field_id] = name
	for _, items in later:
		for item in items:
			_undo(found, names, item)
	return found


def _histories(issue: dict) -> list[dict]:
	"""Every change of *issue*: the changelog it came with, and the rest of it from Jira when that
	was cut (a long-lived ticket has more changes than Jira sends with it)."""
	changelog = issue.get('changelog') or {}
	histories = list(changelog.get('histories') or [])
	if int(changelog.get('total') or 0) <= len(histories):
		return histories
	histories = []
	for _ in range(MAX_CHANGELOG_PAGES):
		page = _get(f'/rest/api/2/issue/{issue["key"]}/changelog', {'startAt': len(histories)})
		histories.extend(page.get('values') or [])
		if page.get('isLast', True) or not page.get('values'):
			break
	return histories


def _undo(found: dict, names: dict[str, str], item: dict) -> None:
	"""Undo in *found* one changelog *item*; *names* maps Jira field ids to names in *found*."""
	name = names.get(item.get('fieldId') or '')
	if item.get('field') == 'Link':  # the changelog gives links no field id
		name = 'links'
	if name not in found or name in ('comments', 'attachments'):  # those are cut by their date
		return
	if name in ('links', 'fix_versions'):
		found[name] = _undone(found[name], item, name == 'links')
		return
	value = item.get('fromString') or None
	if name == 'description' and value:
		value = value[:DESCRIPTION_CHARS]
	found[name] = value


def _undone(values: list[str], item: dict, link: bool) -> list[str]:
	"""Links ("relates to DTN-1: summary") or fix versions ("v1.1 (released)") before the change
	*item*: what it added is taken out, and what it removed is put back."""
	added = item.get('toString')
	if link:
		added = item.get('to')  # the other ticket's key
	kept = []
	for value in values:
		name = value.rsplit(' (', 1)[0]  # a fix version, with or without its state
		if link:
			name = value.split(': ', 1)[0].rsplit(' ', 1)[-1]
		if not added or name != added:
			kept.append(value)
	if item.get('fromString'):
		kept.append(item['fromString'])
	return kept


def _time(value) -> datetime | None:
	"""A Jira time (JIRA_TIME); None when it is missing or not one."""
	try:
		return datetime.strptime(str(value), JIRA_TIME)
	except ValueError:
		return None


def _later(value) -> bool:
	"""Whether the Jira time *value* came after the replay's cutoff."""
	moment = _time(value)
	return moment is not None and cutoff.after(moment)


def versions(project: str) -> list[dict]:
	"""The versions of Jira *project* (name, released, releaseDate), as Jira gives them."""
	if not PROJECT_KEY.fullmatch(project):
		raise ValueError(f'{project!r} is not a Jira project key like DTN')
	return _get(f'/rest/api/2/project/{project}/versions', {})  # type: ignore[return-value]


def browse_url(key: str) -> str:
	"""The Jira page of ticket *key*."""
	return f'{_base()}/browse/{key}'


def search(jql: str, limit: int = 10) -> list[dict]:
	"""Tickets matching *jql*, at most MAX_RESULTS, with the history fields only.

	In a replay, Jira matches today's text, so a hit whose summary or description was edited
	after the cutoff is kept only if its text then still has the searched words
	(:func:`dss.cutoff.matches`).
	"""
	jql = jql.strip()
	if not jql:
		raise JiraError('the JQL query is empty')
	limit = max(1, min(limit, MAX_RESULTS))
	params = {'jql': jql, 'fields': ','.join(HISTORY_FIELDS.values()), 'maxResults': limit}
	then = cutoff.when()
	if then is None:
		page = _get('/rest/api/2/search/jql', params)
		return [compact(issue, HISTORY_FIELDS) for issue in page.get('issues', [])]
	jql = _jql_then(jql, then)
	phrases = []
	for literal in SEARCHED.findall(jql):
		phrases.append(cutoff.search_terms(literal[1:-1]))
	fields = ','.join(SEARCHED_FIELDS.values())
	params.update(jql=jql, fields=fields, maxResults=MAX_RESULTS, expand='changelog')
	found = []
	for issue in _get('/rest/api/2/search/jql', params).get('issues', []):
		if issue['key'] == cutoff.key():  # the replayed ticket is no past issue
			continue
		try:
			hit = as_posted(issue, SEARCHED_FIELDS)
		except JiraError:  # created after the cutoff, on its day
			continue
		now = compact(issue, TEXT_FIELDS)
		text = f'{hit["summary"]} {hit["description"] or ""}'
		edited = (now['summary'], now['description']) != (hit['summary'], hit['description'])
		if edited and not cutoff.matches(text, phrases):
			continue  # it matches only by words written after the ticket
		del hit['description']
		found.append(hit)
	return found[:limit]


def _jql_then(jql: str, then: datetime) -> str:
	"""A replay's *jql*: tickets created by the day of *then*, the cutoff (:func:`as_posted` drops
	the rest of that day), with `text ~` kept to the summary and description. JQL that could pick
	tickets by what happened later is refused (:func:`_steady`)."""
	jql = TEXT_SEARCH.sub(r'(summary ~ \1 OR description ~ \1)', jql)
	_steady(jql)
	order = ORDER_BY.search(jql)
	where, ordered = jql.strip(), ''
	if order:
		where, ordered = jql[: order.start()].strip(), ' ' + order[0]
	day = (then + timedelta(days=1)).strftime('%Y-%m-%d')
	created = f'created < "{day}"'
	if where:
		created = f'({where}) AND {created}'
	return created + ordered


def _steady(jql: str) -> None:
	"""Refuse (JiraError) a replay's *jql* that tests or sorts by a field a ticket can change once
	filed, calls a function (`linkedIssues()` answers with today's links) or uses a date counted
	from today (-30d)."""
	refused = []
	masked = QUOTED.sub('"_"', jql)  # a quoted value or custom field name
	for value in QUOTED.findall(jql):
		if RELATIVE_DATE.match(value[1:-1].strip()):
			refused.append(f'the relative date {value}')
	order = ORDER_BY.search(masked)
	where = masked
	if order:
		where = masked[: order.start()]
		words = order[0].split(None, 2)  # ORDER, BY, the sort fields
		sorts = ''
		if len(words) == 3:
			sorts = words[2]
		for part in sorts.split(','):
			field = ''
			if part.split():
				field = part.split()[0].lower()
			if field not in STEADY_ORDER:
				refused.append(f'sorting by {field or "nothing"}')
	for found in JQL_TEST.finditer(where):
		field = (found[1] or found[2]).lower()
		if field == '"_"':
			refused.append('a custom field')
		elif field not in STEADY_FIELDS:
			refused.append(f'the field {field}')
	for call in JQL_CALL.finditer(where):
		if call[1].lower() not in SET_WORDS:
			refused.append(f'the function {call[1]}()')
	for word in where.split():
		if RELATIVE_DATE.match(word.strip('()')):
			refused.append(f'the relative date {word}')
	if refused:
		raise JiraError(
			f'a replay searches only by {", ".join(sorted(STEADY_FIELDS))} and absolute dates, '
			f'and sorts only by created or key; {"; ".join(dict.fromkeys(refused))} could pick '
			'tickets by what happened after the ticket'
		)


def setting(name: str) -> str:
	"""JIRA_<name>, or the CONFLUENCE_* value it falls back to; empty when neither is set."""
	return os.environ.get(name) or os.environ.get(ENV[name], '')


def _get(path: str, params: dict) -> dict:
	missing = []
	for name in ENV:
		if not setting(name):
			missing.append(name)
	if missing:
		raise JiraError(
			f'Jira is not set up on this machine: set {", ".join(missing)} '
			'(a read-only account and its API token; the CONFLUENCE_* values are used when set).'
		)
	auth = f'{setting("JIRA_EMAIL")}:{setting("JIRA_TOKEN")}'
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
			400: 'Jira rejected the request (check the JQL)',
			401: 'the Jira token was refused',
			403: 'this account cannot read that project',
			404: 'no such ticket (or no access to it)',
		}.get(error.code, f'Jira answered {error.code} {error.reason}')
		raise JiraError(reason) from error
	except (urllib.error.URLError, TimeoutError) as error:
		raise JiraError(f'cannot reach Jira at {_base()}: {error}') from error


def _base() -> str:
	return setting('JIRA_URL').rstrip('/').removesuffix('/wiki')


def register(server) -> None:
	@server.tool()
	def jira_get_issue(key: str) -> dict:
		"""One ticket's triage fields: summary, description, status, resolution, fix versions
		(released or not), reporter, links, the newest comments (where developers write the cause
		and the fix), attachment names, Project Path, Toolbox Name, Investigation Outcome, client
		and DVT."""
		return get_issue(key)

	@server.tool()
	def jira_search(jql: str, limit: int = 10) -> list[dict]:
		"""Tickets matching *jql* (at most 20) with summary, status, resolution, fix versions,
		created, Toolbox Name and Investigation Outcome. Use it to find similar past tickets."""
		return search(jql, limit)
