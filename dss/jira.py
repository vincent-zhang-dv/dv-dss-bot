"""`jira_*` tools: DSS tickets and their history, read-only, through the Jira Cloud REST API.

Uses a read-only account's API token (basic auth), standard library only. The settings are
JIRA_URL, JIRA_EMAIL and JIRA_TOKEN; each one falls back to its CONFLUENCE_* value, since one
Atlassian site and token serve both. Only the fields triage needs are requested, so a ticket
costs a few hundred tokens instead of the whole issue.
"""

import base64
import json
import os
import re
import urllib.error
import urllib.parse
import urllib.request

TIMEOUT_S = 30
ISSUE_KEY = re.compile(r'[A-Z][A-Z0-9]+-\d+')
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
	'reporter': 'reporter',
	'links': 'issuelinks',
	'project_path': 'customfield_10823',
	'toolbox': 'customfield_10824',
	'outcome': 'customfield_10825',
	'client': 'customfield_13663',
	'dvt': 'customfield_13664',
}
HISTORY_FIELDS = {
	'summary': 'summary',
	'status': 'status',
	'created': 'created',
	'toolbox': 'customfield_10824',
	'outcome': 'customfield_10825',
}
MAX_RESULTS = 20
DESCRIPTION_CHARS = 4000


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


def compact(issue: dict, fields: dict[str, str]) -> dict:
	"""*issue* (Jira REST JSON) as {name: plain value} for the *fields* asked for."""
	raw = issue.get('fields') or {}
	found = {'key': issue['key']}
	for name, field_id in fields.items():
		value = raw.get(field_id)
		if name == 'links':
			found[name] = _links(value)
		elif name == 'description' and value:
			found[name] = str(value)[:DESCRIPTION_CHARS]
		else:
			found[name] = plain(value)
	return found


def get_issue(key: str) -> dict:
	"""The triage fields of ticket *key*; the key is checked before anything is sent."""
	if not ISSUE_KEY.fullmatch(key):
		raise ValueError(f'{key!r} is not an issue key like DSS-1222')
	issue = _get(f'/rest/api/2/issue/{key}', {'fields': ','.join(TICKET_FIELDS.values())})
	return compact(issue, TICKET_FIELDS)


def search(jql: str, limit: int = 10) -> list[dict]:
	"""Tickets matching *jql*, at most MAX_RESULTS, with the history fields only."""
	jql = jql.strip()
	if not jql:
		raise JiraError('the JQL query is empty')
	params = {
		'jql': jql,
		'fields': ','.join(HISTORY_FIELDS.values()),
		'maxResults': max(1, min(limit, MAX_RESULTS)),
	}
	page = _get('/rest/api/2/search/jql', params)
	return [compact(issue, HISTORY_FIELDS) for issue in page.get('issues', [])]


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
		"""One ticket's triage fields: summary, description, status, reporter, links, Project
		Path, Toolbox Name, Investigation Outcome, client and DVT."""
		return get_issue(key)

	@server.tool()
	def jira_search(jql: str, limit: int = 10) -> list[dict]:
		"""Tickets matching *jql* (at most 20) with summary, status, created, Toolbox Name and
		Investigation Outcome. Use it to find similar past tickets."""
		return search(jql, limit)
