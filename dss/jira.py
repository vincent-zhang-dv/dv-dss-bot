"""`jira_*` tools: DSS tickets and their history, read-only, through arch's `DVJira`.

The functions run in the arch venv. `DVJira` takes its API token from AWS Secrets Manager; the
account's email comes from DV_JIRA_EMAIL. Only the fields triage needs are requested, so a ticket
costs a few hundred tokens instead of the whole issue.
"""

import os
import re

from dss.worker import run_in

ISSUE_KEY = re.compile(r'[A-Z][A-Z0-9]+-\d+')
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


def _jira():
	"""arch's Jira client for the DV_JIRA_EMAIL account (arch venv only)."""
	from connectors.dvjira import DVJira

	email = os.environ.get('DV_JIRA_EMAIL')
	if not email:
		raise RuntimeError('set DV_JIRA_EMAIL to the Jira account whose API token arch keeps')
	return DVJira(user_email=email).jira


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
	issue = _jira().issue(key, fields=','.join(TICKET_FIELDS.values()))
	return compact(issue, TICKET_FIELDS)


def search(jql: str, limit: int = 10) -> list[dict]:
	"""Tickets matching *jql*, at most MAX_RESULTS, with the history fields only."""
	fields = ','.join(HISTORY_FIELDS.values())
	page = _jira().enhanced_jql(jql, fields=fields, limit=min(limit, MAX_RESULTS))
	return [compact(issue, HISTORY_FIELDS) for issue in page.get('issues', [])]


def register(server) -> None:
	@server.tool()
	def jira_get_issue(key: str) -> dict:
		"""One ticket's triage fields: summary, description, status, reporter, links, Project
		Path, Toolbox Name, Investigation Outcome, client and DVT."""
		return run_in('arch', __name__, 'get_issue', key=key)

	@server.tool()
	def jira_search(jql: str, limit: int = 10) -> list[dict]:
		"""Tickets matching *jql* (at most 20) with summary, status, created, Toolbox Name and
		Investigation Outcome. Use it to find similar past tickets."""
		return run_in('arch', __name__, 'search', jql=jql, limit=limit)
