"""Replay mode: every source answers as of the ticket's post, and the `replay` command around it.

Made-up tickets, projects and commits only; every source is faked, as in the other tests.
"""

import json
import ntpath
import os
import subprocess
import sys
import urllib.parse
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
from mcp.server.mcpserver import MCPServer

from dss import __main__ as cli
from dss import cerb, code, confluence, cutoff, jira, matlab, record, replay, runner, slack, thread
from dss.server import ReportingTools

AT = datetime(2026, 10, 22, 10, 0, 5).astimezone()  # the workflow's post, in local time
TS = f'{AT.timestamp():.6f}'
POSTED = '2026-10-22_10-00-05'
ROOT = r'\\server\projects'
PROJECT = ROOT + r'\ACME\DVT1234_2026-10-20_WI_Well-1'
EXAMPLE = json.loads(record.SCHEMA_FILE.with_name('triage_record.example.json').read_text('utf-8'))


def at(hours: float) -> datetime:
	"""*hours* after the post (before it when negative)."""
	return AT + timedelta(hours=hours)


def jira_time(hours: float) -> str:
	return at(hours).strftime(jira.JIRA_TIME)


@pytest.fixture
def replaying(monkeypatch):
	"""Inside a replay of DSS-1234, as `python -m dss replay` sets it up."""
	monkeypatch.setenv('DSS_REPLAY_AT', TS)
	monkeypatch.setenv('DSS_REPLAY_KEY', 'DSS-1234')


def test_the_cutoff_is_the_post_and_code_marks_what_looks_later(replaying, monkeypatch):
	assert cutoff.when() == AT and cutoff.key() == 'DSS-1234'
	assert cutoff.name_time() == POSTED
	assert cutoff.after(at(0.01)) and not cutoff.after(AT)
	later = 'fixed in DSS-1234 on 2026-10-23, after 2026-10-21'
	assert cutoff.leaks('code_log', {}, later) == [
		'names DSS-1234',
		'dates after the ticket: 2026-10-23',
	]
	assert cutoff.leaks('code_log', {}, 'DSS-1200 on 2026-10-22') == []  # an older ticket, same day
	assert cutoff.leaks('code_log', {}, 'see DSS-1240 and DSS-12345') == [
		'names later tickets: DSS-1240, DSS-12345'  # numbered after it, so filed after it
	]
	own = 'created DSS-1234, internal review due 2026-10-30'  # the ticket itself, and its due date
	assert cutoff.leaks('slack_thread', {'ts': TS}, own) == []
	assert cutoff.leaks('jira_get_issue', {'key': 'DSS-1234'}, own) == []
	assert cutoff.leaks('jira_get_issue', {'key': 'DTN-9'}, 'relates to DSS-1234') == [
		'names DSS-1234'
	]
	monkeypatch.delenv('DSS_REPLAY_AT')
	assert cutoff.when() is None and cutoff.name_time() == '' and not cutoff.after(at(1))
	assert cutoff.leaks('code_log', {}, later) == []


# Jira


def issue(key: str, created: float, **fields) -> dict:
	return {'key': key, 'fields': {'created': jira_time(created), **fields}}


DONE_LATER = {
	'histories': [
		{  # before the post: stays
			'created': jira_time(-0.5),
			'items': [{'field': 'status', 'fieldId': 'status', 'fromString': 'New'}],
		},
		{
			'created': jira_time(24),
			'items': [
				{'field': 'status', 'fieldId': 'status', 'fromString': 'Open'},
				{'field': 'resolution', 'fieldId': 'resolution', 'fromString': None},
				{'field': 'Investigation Outcome', 'fieldId': 'customfield_10825'},
				{'field': 'Toolbox Name', 'fieldId': 'customfield_10824'},
				{'field': 'Link', 'to': 'VDI-77', 'toString': 'This issue relates to VDI-77'},
				{'field': 'Fix Version', 'fieldId': 'fixVersions', 'toString': 'CERBERUS v1.1'},
			],
		},
		{
			'created': jira_time(2),
			'items': [
				{'field': 'description', 'fieldId': 'description', 'fromString': 'Plot is off'}
			],
		},
	]
}
TODAY = issue(
	'DSS-1234',
	-0.001,
	summary='Plot is wrong',
	description='Plot is off. Cause: a full F: drive',
	status={'name': 'Done'},
	resolution={'name': 'Done'},
	customfield_10825={'value': 'User Error'},
	customfield_10824={'value': 'CERB-WI'},
	issuelinks=[
		{
			'type': {'outward': 'relates to', 'inward': 'relates to'},
			'outwardIssue': {'key': 'VDI-77', 'fields': {'summary': 'F: drive full'}},
		}
	],
	fixVersions=[{'name': 'CERBERUS v1.1', 'released': True, 'releaseDate': '2026-11-03'}],
	comment={'comments': [{'created': jira_time(-0.0005), 'author': 'Dev', 'body': 'drive full'}]},
	attachment=[
		{'filename': 'first.png', 'created': jira_time(-0.001), 'size': 2048},
		{'filename': 'later.png', 'created': jira_time(3), 'size': 2048},
	],
)


def test_the_replayed_ticket_is_as_first_created_without_its_comments(replaying):
	found = jira.as_posted({**TODAY, 'changelog': DONE_LATER}, jira.TICKET_FIELDS)
	assert found['summary'] == 'Plot is wrong'  # never changed
	assert found['description'] == 'Plot is off'
	assert (found['status'], found['resolution']) == ('Open', None)
	assert (found['outcome'], found['toolbox']) == (None, None)
	assert found['links'] == [] and found['fix_versions'] == []
	assert found['comments'] == []  # its answers are written there, even the early ones
	assert [name.split(' ')[0] for name in found['attachments']] == ['first.png']


def test_other_tickets_are_as_they_stood_and_later_ones_are_refused(replaying):
	older = issue(
		'DTN-9',
		-48,
		status={'name': 'Done'},
		fixVersions=[{'name': 'CERBERUS v1.1', 'released': True, 'releaseDate': '2026-11-03'}],
		comment={
			'comments': [
				{'created': jira_time(-24), 'author': 'Dev', 'body': 'Found the cause'},
				{
					'created': jira_time(-20),
					'updated': jira_time(26),
					'author': 'Dev',
					'body': 'Found the cause. Edit: fixed in 1.1',
				},
				{'created': jira_time(30), 'author': 'Dev', 'body': 'Fixed in 1.1'},
			]
		},
	)
	found = jira.as_posted(older, jira.TICKET_FIELDS)
	bodies = [comment.split(': ', 1)[1] for comment in found['comments']]
	assert bodies == ['Found the cause', jira.EDITED_LATER]  # Jira keeps only the edited text
	assert found['fix_versions'] == ['CERBERUS v1.1']  # Jira's release date is no proof of then
	with pytest.raises(
		jira.JiraError, match='DTN-10 came after the ticket; this replay'
	) as refused:
		jira.as_posted(issue('DTN-10', 1), jira.TICKET_FIELDS)
	assert '11:00' not in str(refused.value)  # never when: that would date the fix


def test_a_long_changelog_is_read_whole(replaying, monkeypatch):
	pages = {
		0: {'values': [DONE_LATER['histories'][0]], 'isLast': False},
		1: {'values': DONE_LATER['histories'][1:], 'isLast': True},
	}
	monkeypatch.setattr(jira, '_get', lambda path, params: pages[params['startAt']])
	cut = {'total': 3, 'histories': DONE_LATER['histories'][:1]}  # Jira sent only the first
	found = jira.as_posted({**TODAY, 'changelog': cut}, jira.TICKET_FIELDS)
	assert (found['status'], found['outcome'], found['links']) == ('Open', None, [])


def test_jira_reads_the_changelog_and_searches_only_older_tickets(replaying, monkeypatch):
	asked = []

	def get(path, params):
		asked.append((path, params))
		if path.endswith('/search/jql'):
			issues = [issue('DSS-1234', -0.001), issue('DTN-10', 0.5), issue('DSS-1100', -200)]
			return {'issues': issues}
		return {**TODAY, 'changelog': DONE_LATER}

	monkeypatch.setattr(jira, '_get', get)
	assert jira.get_issue('DSS-1234')['status'] == 'Open'
	assert asked[0][1]['expand'] == 'changelog' and 'created' in asked[0][1]['fields']
	found = jira.search('text ~ "casing tally" AND project = DSS ORDER BY created DESC', limit=5)
	assert [ticket['key'] for ticket in found] == ['DSS-1100']  # not itself, not a later one
	assert asked[1][1]['jql'] == (
		'((summary ~ "casing tally" OR description ~ "casing tally") AND project = DSS) '
		'AND created < "2026-10-23" ORDER BY created DESC'
	)
	monkeypatch.delenv('DSS_REPLAY_AT')
	jira.search('project = DSS')
	assert asked[-1][1]['jql'] == 'project = DSS' and 'expand' not in asked[-1][1]


@pytest.mark.parametrize(
	'jql, refused',
	[
		('project = DTN AND status = Done', 'the field status'),  # what it became later
		('project = DTN AND resolution is EMPTY', 'the field resolution'),
		('comment ~ "drive full"', 'the field comment'),
		('"Investigation Outcome" = "User Error"', 'a custom field'),
		('key in linkedIssues(DSS-1234)', 'the function linkedIssues()'),
		('project = DSS AND created >= -30d', 'the relative date -30d'),
		('project = DSS AND created >= "-2w"', 'the relative date "-2w"'),
		('project = DSS ORDER BY updated DESC', 'sorting by updated'),
		('status was "In Progress"', 'the field status'),
	],
)
def test_a_replay_searches_jira_only_by_what_a_ticket_keeps(replaying, monkeypatch, jql, refused):
	monkeypatch.setattr(jira, '_get', lambda path, params: {'issues': []})
	with pytest.raises(jira.JiraError, match='could pick tickets by what happened') as error:
		jira.search(jql)
	assert refused in str(error.value)


def test_a_replay_keeps_searches_on_what_a_ticket_keeps(replaying, monkeypatch):
	monkeypatch.setattr(jira, '_get', lambda path, params: {'issues': []})
	steady = (
		'project in (DSS, DTN) AND (summary ~ "plug report" OR description ~ "in the plot") '
		'AND created >= "2026-01-01" AND issuetype = Bug ORDER BY created DESC, key'
	)
	assert jira.search(steady) == []  # "in the plot" is a value, not a field test


def test_a_jira_hit_found_only_by_words_written_later_is_dropped(replaying, monkeypatch):
	def edited(key, summary, description, before):
		later = {
			'created': jira_time(5),
			'items': [{'fieldId': 'description', 'fromString': before}],
		}
		hit = issue(key, -100, summary=summary, description=description)
		return {**hit, 'changelog': {'histories': [later]}}

	hits = [
		edited('DSS-1100', 'Plot is off', 'Plot is off. Cause: casing ID', 'Plot is off'),
		edited('DSS-1101', 'Casing ID wrong', 'Casing ID wrong. Fixed', 'Casing ID wrong'),
		issue('DSS-1102', -50, summary='casing tallies', description='casing ID'),
	]
	monkeypatch.setattr(jira, '_get', lambda path, params: {'issues': hits})
	found = jira.search('text ~ "casing ID"')
	assert [ticket['key'] for ticket in found] == ['DSS-1101', 'DSS-1102']  # not DSS-1100
	assert 'description' not in found[0]  # read only to check the hit


def test_search_words_match_the_words_that_start_as_they_do():
	terms = cutoff.search_terms('The casing tallies and plugs')
	assert terms == ['casi', 'tall', 'plug']
	assert cutoff.matches('Casing tally from the client; plug list', [terms])
	assert not cutoff.matches('Casing tally from the client', [terms])  # no plug
	assert cutoff.matches('anything', [])  # nothing searched


# Confluence


def test_a_pages_age_counts_from_the_ticket_not_from_today(replaying, monkeypatch):
	edited = {'40': at(-24 * 400), '41': at(-24 * 350)}  # hours before the post

	def get(path, params):
		page_id = path.split('/')[5]
		if path.endswith('/versions'):
			return {'results': [{'number': 1, 'createdAt': edited[page_id].isoformat()}]}
		version = {'createdAt': edited[page_id].isoformat()}
		return {'title': 'SOP', 'body': {'storage': {'value': '<p>steps</p>'}}, 'version': version}

	monkeypatch.setattr(confluence, '_get', get)
	assert confluence.page('40')['may_be_out_of_date'].endswith('1.1 years before the ticket')
	# 350 days before the post is over a year before today, but not before the ticket
	assert 'may_be_out_of_date' not in confluence.page('41')


def test_a_much_edited_page_is_paged_back_to_its_version_then(replaying, monkeypatch):
	pages = {
		None: {
			'results': [{'number': 9, 'createdAt': at(2).isoformat()}],
			'_links': {'next': '/wiki/api/v2/pages/42/versions?cursor=abc&limit=250'},
		},
		'abc': {'results': [{'number': 8, 'createdAt': at(-2).isoformat()}], '_links': {}},
	}
	asked = []

	def get(path, params):
		asked.append(params)
		if path.endswith('/versions'):
			return pages[params.get('cursor')]
		return {'title': 'SOP', 'body': {'storage': {'value': '<p>then</p>'}}}

	monkeypatch.setattr(confluence, '_get', get)
	assert confluence.page('42')['text'] == 'then'
	assert asked[-1]['version'] == '8'


def test_confluence_pages_are_searched_and_read_as_they_were(replaying, monkeypatch):
	asked = []
	versions = {  # newest first; 42 and 45 changed after the post, 43 was made after it
		'42': [
			{'number': 3, 'createdAt': at(5).isoformat()},
			{'number': 2, 'createdAt': at(-5).isoformat()},
		],
		'43': [{'number': 1, 'createdAt': at(1).isoformat()}],
		'45': [
			{'number': 2, 'createdAt': at(5).isoformat()},
			{'number': 1, 'createdAt': at(-5).isoformat()},
		],
	}
	then = {  # each page's text at version (id, number)
		('42', 2): ('Pup joint SOP', '<p>Measure pup joints from the collar.</p>'),
		('45', 1): ('Casing SOP', '<p>Casing tallies come from the client.</p>'),
	}

	def get(path, params):
		asked.append((path, params))
		if path.endswith('/search'):
			hits = []
			for page_id, changed in (('42', at(5)), ('44', at(-5)), ('45', at(5)), ('43', at(1))):
				hits.append(
					{
						'content': {'id': page_id},
						'title': 'Today',
						'excerpt': 'today',
						'lastModified': changed.isoformat(),
					}
				)
			return {'results': hits}
		if path.endswith('/versions'):
			return {'results': versions[path.split('/')[-2]]}
		title, body = then[(path.split('/')[-1], int(params['version']))]
		version = {'createdAt': at(-5).isoformat()}
		return {'title': title, 'body': {'storage': {'value': body}}, 'version': version}

	monkeypatch.setattr(confluence, '_get', get)
	found = confluence.search('pup joints')
	assert 'created < "2026-10-23"' in asked[0][1]['cql']
	assert [page['id'] for page in found] == ['42', '44']  # 45 matched only by later words
	assert found[0]['title'] == 'Pup joint SOP'  # the title then
	assert found[0]['excerpt'] == 'Measure pup joints from the collar.'  # from the text then
	assert found[1]['excerpt'] == 'today'  # not changed since: today's hit is the hit then
	read = len(asked)
	assert confluence.page('42')['text'] == 'Measure pup joints from the collar.'
	paths = [path for path, _ in asked[read:]]
	assert paths == ['/wiki/api/v2/pages/42/versions']  # the version's text came from the archive
	with pytest.raises(confluence.ConfluenceError, match='page 43 came after the ticket'):
		confluence.page('43')


# Git


def commit(repo: Path, message: str, hours: float, *, edit: str = '') -> str:
	"""A commit made *hours* after the post, changing cerberus/plots.py to *edit* when given."""
	if edit:
		(repo / 'cerberus' / 'plots.py').write_text(edit + '\n')
		subprocess.run(['git', 'add', '.'], cwd=repo, check=True)
	when = at(hours).isoformat()
	env = {**os.environ, 'GIT_AUTHOR_DATE': when, 'GIT_COMMITTER_DATE': when}
	command = ['git', '-c', 'user.name=t', '-c', 'user.email=t@t', 'commit', '-q']
	subprocess.run([*command, '--allow-empty', '-m', message], cwd=repo, env=env, check=True)
	return git(repo, 'rev-parse', '--short', 'HEAD')


def git(repo: Path, *args: str) -> str:
	return subprocess.run(
		['git', *args], cwd=repo, check=True, capture_output=True, text=True
	).stdout.strip()


@pytest.fixture
def history(tmp_path, monkeypatch):
	"""A Cerberus-like repo around the post: release 1.0.4 and a fix branch before it; the fix,
	release 1.0.5 and another branch after it."""
	repo = tmp_path / 'repo'
	(repo / 'cerberus').mkdir(parents=True)
	git(repo, 'init', '-q', '-b', 'master')
	commit(repo, 'Add plots', -48, edit='y = data.round(2)')
	release = commit(repo, 'release: v1.0.4 (#975)', -24)
	git(repo, 'checkout', '-q', '-b', 'DTN-1-fix')
	commit(repo, 'Stop rounding', -2, edit='y = data')
	commit(repo, 'More after the post', 4, edit='y = data  # checked')
	git(repo, 'checkout', '-q', 'master')
	fix = commit(repo, 'Fix DSS-1234 rounding', 2, edit='y = data')
	commit(repo, 'release: v1.0.5 (#980)', 3)
	git(repo, 'checkout', '-q', '-b', 'DTN-2-later')
	commit(repo, 'Start later work', 5)
	for branch in ('master', 'DTN-1-fix', 'DTN-2-later'):
		git(repo, 'update-ref', f'refs/remotes/origin/{branch}', branch)
	monkeypatch.setenv('DSS_CERB_CWD', str(repo))
	monkeypatch.delenv('DSS_CERB_GIT', raising=False)
	monkeypatch.setenv('DV_CERB_PROD_DIR', str(tmp_path / 'prod'))
	(tmp_path / 'prod').mkdir()
	monkeypatch.setattr(jira, 'versions', lambda project: [])
	return {'repo': repo, 'release': release, 'fix': fix, 'prod': tmp_path / 'prod'}


def test_git_history_stops_at_the_post(history, replaying, monkeypatch):
	monkeypatch.setenv('DV_CERB_REF', history['release'])
	assert code.release_then() == ('1.0.4', history['release'])
	with pytest.raises(LookupError, match='its release came after the ticket'):
		code.release_ref('1.0.5')
	found = code.log_s('round(2)', all_branches=True)
	assert 'Stop rounding' in found and 'Fix DSS-1234' not in found
	assert 'More after' not in code.log('cerberus', all_branches=True)
	branches = code.branches('DTN')
	assert 'origin/DTN-1-fix' in branches and 'Stop rounding' in branches  # its tip then
	assert 'More after' not in branches and 'DTN-2-later' not in branches  # not made yet
	with pytest.raises(LookupError, match=f'commit {history["fix"]} came after the ticket'):
		code.contains(history['fix'], '1.0.4')
	assert 'does not include' in code.contains('DTN-1-fix', '1.0.4')
	assert 'y = data.round(2)' in code.read('cerberus/plots.py')  # the analyst's build
	monkeypatch.delenv('DSS_REPLAY_AT')
	assert 'More after' in code.branches('DTN') and 'DTN-2-later' in code.branches('DTN')


def test_prod_then_counts_a_release_from_its_latest_date(history, replaying, monkeypatch):
	def released(day: str):
		version = {'name': 'CERBERUS v1.0.4', 'released': True, 'releaseDate': day}
		monkeypatch.setattr(jira, 'versions', lambda project: [version])

	released('2026-10-22')  # committed the day before, released in Jira on the ticket's day
	with pytest.raises(LookupError, match='no release on origin/master was out'):
		code.release_then()
	released('2026-10-21')
	assert code.release_then() == ('1.0.4', history['release'])
	wheel = history['prod'] / 'cerberus-1.0.4-py3-none-any.whl'
	wheel.touch()
	os.utime(wheel, (at(1).timestamp(), at(1).timestamp()))  # reached prod after the post
	with pytest.raises(LookupError, match='no release on origin/master was out'):
		code.release_then()


def pushed(hours: float, branch: str, commit: str) -> list[str]:
	"""A row of GitHub's push history: *branch* set to *commit*, *hours* after the post."""
	when = at(hours).astimezone(UTC).strftime('%Y-%m-%dT%H:%M:%SZ')
	return [when, f'refs/heads/{branch}', commit]


def test_git_history_is_what_github_held_at_the_post(history, replaying):
	repo = history['repo']
	release = git(repo, 'rev-parse', history['release'])
	git(repo, 'checkout', '-q', '-b', 'DTN-3-late', release)
	late = commit(repo, 'Early fix, pushed late', -3, edit='y = data  # early')  # made before
	git(repo, 'update-ref', 'refs/remotes/origin/DTN-3-late', 'DTN-3-late')
	git(repo, 'checkout', '-q', 'master')
	rows = [
		pushed(-47, 'master', git(repo, 'rev-parse', 'master~3')),
		pushed(-23, 'master', release),
		pushed(-1.5, 'DTN-1-fix', git(repo, 'rev-parse', 'DTN-1-fix~1')),
		pushed(1, 'DTN-3-late', git(repo, 'rev-parse', 'DTN-3-late')),  # pushed after the post
		pushed(2.5, 'master', git(repo, 'rev-parse', 'master~1')),
		pushed(3.5, 'master', git(repo, 'rev-parse', 'master')),
		pushed(4.5, 'DTN-1-fix', git(repo, 'rev-parse', 'DTN-1-fix')),
		pushed(5.5, 'DTN-2-later', git(repo, 'rev-parse', 'DTN-2-later')),
	]
	code.ACTIVITY_FILE.parent.mkdir(parents=True)
	code.ACTIVITY_FILE.write_text(json.dumps(rows), encoding='utf-8')  # reaches past the post
	stop = git(repo, 'rev-parse', 'DTN-1-fix~1')
	assert code.branches_then() == {'origin/master': release, 'origin/DTN-1-fix': stop}
	assert 'Early fix' not in code.log('cerberus', all_branches=True)  # dated before, pushed after
	branches = code.branches('DTN')
	assert 'origin/DTN-1-fix' in branches and 'Stop rounding' in branches
	assert 'DTN-3-late' not in branches and 'More after' not in branches
	with pytest.raises(LookupError, match=f'commit {late} came after the ticket'):
		code.contains(late, '1.0.4')
	assert 'does not include' in code.contains('DTN-1-fix', '1.0.4')
	assert code.release_then() == ('1.0.4', history['release'])
	code.ACTIVITY_FILE.unlink()  # without the push history, commit dates stand in
	code._BRANCHES_THEN.clear()
	assert 'Early fix' in code.log('cerberus', all_branches=True)  # what the history guards


def test_the_push_history_is_kept_and_only_new_activity_is_asked(monkeypatch):
	newest = (datetime.now(UTC) - timedelta(days=3)).strftime('%Y-%m-%dT%H:%M:%SZ')
	kept = [[newest, 'refs/heads/master', 'a' * 40]]
	code.ACTIVITY_FILE.parent.mkdir(parents=True)
	code.ACTIVITY_FILE.write_text(json.dumps(kept), encoding='utf-8')
	asked = []

	def activity(period):
		asked.append(period)
		return [kept[0], ['2099-01-01T00:00:00Z', 'refs/heads/DTN-9', 'b' * 40]]

	monkeypatch.setattr(code, '_activity', activity)
	assert code.push_history(datetime(2026, 1, 1, tzinfo=UTC)) == kept and asked == []
	assert len(code.push_history()) == 2 and asked == ['week']  # only what came since, once each
	assert json.loads(code.ACTIVITY_FILE.read_text(encoding='utf-8'))[-1][1] == 'refs/heads/DTN-9'

	def down(period):
		raise RuntimeError('gh is not logged in')

	monkeypatch.setattr(code, '_activity', down)
	assert len(code.push_history()) == 2  # the kept history still serves


def test_the_analysts_build_is_exported_from_git_without_a_wheel(history):
	folder = Path(code.export(history['release']))
	assert (folder / 'cerberus' / 'plots.py').read_text() == 'y = data.round(2)\n'
	assert code.export(history['release']) == str(folder)  # written once
	assert not list(folder.parent.glob('*.partial'))


def test_matlab_cat_is_read_as_it_was_at_the_post(replaying, tmp_path, monkeypatch):
	repo = tmp_path / 'DataAnalysis'
	(repo / 'CAT').mkdir(parents=True)
	git(repo, 'init', '-q', '-b', 'master')
	export = repo / 'CAT' / 'cat_export_las.m'
	export.write_text('vals = interp1(depths, vals, dref);\n')
	git(repo, 'add', '.')
	commit(repo, 'Export the LAS', -24)
	export.write_text('vals = resample2const(vals, dref);\n')
	git(repo, 'add', '.')
	commit(repo, 'Keep the maxPen peaks', 2)
	git(repo, 'update-ref', 'refs/remotes/origin/master', 'master')
	monkeypatch.setenv('DSS_MATLAB_GIT', str(repo))
	assert 'CAT/cat_export_las.m:1:vals = interp1' in matlab.grep('interp1')
	assert 'nothing found' in matlab.grep('resample2const')
	assert 'interp1' in matlab.read('CAT/cat_export_las.m')
	history = matlab.log('CAT')
	assert 'Export the LAS' in history and 'Keep the maxPen' not in history
	monkeypatch.setenv('DSS_REPLAY_AT', f'{at(-48).timestamp():.6f}')
	with pytest.raises(LookupError, match='every MATLAB CAT commit came after the ticket'):
		matlab.read('CAT/cat_export_las.m')
	monkeypatch.delenv('DSS_REPLAY_AT')
	assert 'resample2const' in matlab.read('CAT/cat_export_las.m')
	assert 'Keep the maxPen peaks' in matlab.log('CAT')


# The project share


def filetime(moment: datetime) -> str:
	"""*moment* as the index gives date_modified: 100 ns steps since 1601 (UTC)."""
	return str(int((moment - cerb.FILETIME_START).total_seconds() * 10**7))


TELEMETRY = PROJECT + r'\cerberus\telemetry'
OLD_LOG = TELEMETRY + r'\AnalystAnn_telemetry_2026-10-22_09-00-00_11.log'
NEW_LOG = TELEMETRY + r'\AnalystAnn_telemetry_2026-10-22_11-00-00_12.log'
CLIENT_INFO = PROJECT + r'\client_info.xlsm'
NOTES = PROJECT + r'\notes.txt'
PLAN_SHEET = PROJECT + r'\Plan 2026-12.xlsx'  # named for a later month, there before the post
RERUN = PROJECT + r'\Analysis 2026-10-30.docx'  # made after the post, as its name says
TICKET_FOLDER = PROJECT + r'\Deliverables\DSS-1234\stats.xlsx'
LATER_PROJECT = ROOT + r'\ACME\DVT1234_2026-12_PE_Well-1\notes.txt'
SESSION_LINES = (
	'ts=2026-10-22T09:00:01.000 user=AnalystAnn event=session phase=start cerb=1.0.3',
	'ts=2026-10-22T09:30:00.000 event=script_run phase=stop name=casing status=error',
	'ts=2026-10-22T10:30:00.000 event=script_run phase=stop name=plots status=error',
)
SESSION = '\n'.join(SESSION_LINES)


@pytest.fixture
def share(monkeypatch):
	"""A fake project index over {path: (bytes, when it last changed)}; searches give every file
	and folder with its date_modified, so the tools' own filters must pick."""
	files = {
		OLD_LOG: (SESSION.encode(), at(1)),  # the session went on after the post
		NEW_LOG: (b'ts=2026-10-22T11:00:01.000 event=session cerb=1.0.4', at(2)),
		CLIENT_INFO: (b'xlsm', at(20)),  # fixed after the post
		NOTES: (b'casing is wrong', at(-3)),
		PLAN_SHEET: (b'xlsx', at(-5)),
		RERUN: (b'docx', at(200)),
		TICKET_FOLDER: (b'xlsx', at(3)),
		LATER_PROJECT: (b'notes', at(1000)),
	}

	def get(url_path, into=None, part=None):
		if url_path.startswith('/?'):
			hits = []
			folders = {}
			for path, (data, changed) in files.items():
				hits.append(
					{
						'type': 'file',
						'name': ntpath.basename(path),
						'path': ntpath.dirname(path),
						'size': str(len(data)),
						'date_modified': filetime(changed),
					}
				)
				folder = ntpath.dirname(path)
				while cerb._key(folder) != cerb._key(ROOT):  # a folder changes with what is in it
					folders[folder] = max(changed, folders.get(folder, changed))
					folder = ntpath.dirname(folder)
			for folder, changed in folders.items():
				name, parent = ntpath.basename(folder), ntpath.dirname(folder)
				hits.append(
					{
						'type': 'folder',
						'name': name,
						'path': parent,
						'date_modified': filetime(changed),
					}
				)
			return json.dumps({'results': hits}).encode()
		path = '\\'.join(urllib.parse.unquote(piece) for piece in url_path[1:].split('/'))
		if path not in files:
			raise RuntimeError('no such file in the project index')
		if into is not None:
			into.write_bytes(files[path][0])
			return b''
		return files[path][0]

	monkeypatch.setenv('DV_PROJECT_INDEX_ROOT', ROOT)
	monkeypatch.setattr(cerb, '_get', get)
	return files


def test_files_changed_after_the_post_are_marked_and_read_as_they_are_now(
	share, replaying, tmp_path, monkeypatch, mcp_call
):
	monkeypatch.setattr(cerb.tempfile, 'tempdir', str(tmp_path))
	listed = {entry['name']: entry for entry in cerb.list_folder(PROJECT)['entries']}
	assert sorted(listed) == [
		'Deliverables',
		'Plan 2026-12.xlsx',  # unchanged since before the post: shown, whatever its name
		'cerberus',
		'client_info.xlsm',
		'notes.txt',
	]  # not Analysis 2026-10-30.docx: changed since, and named for a later day
	assert listed['client_info.xlsm'] == {
		'name': 'client_info.xlsm',
		'type': 'file',
		'changed_after_ticket': True,
	}  # neither when it changed nor its size now
	assert listed['notes.txt']['size'] == '15' and 'changed_after_ticket' not in listed['notes.txt']
	assert cerb.list_folder(PROJECT + r'\Deliverables')['entries'] == []  # not the DSS-1234 folder
	with pytest.raises(ValueError, match='the folder DSS-1234 came after the ticket'):
		cerb.read_text(TICKET_FOLDER)
	assert cerb.local_copy(CLIENT_INFO).read_bytes() == b'xlsm'  # no longer refused
	failed, text = mcp_call('cerb_read_text', {'path': NOTES})
	assert not failed and 'changed after the ticket' not in text  # as it was then
	share[NOTES] = (b'casing is right now', at(5))  # fixed after the post
	failed, text = mcp_call('cerb_read_text', {'path': NOTES})
	assert (
		'"changed_after_ticket": "changed after the ticket: this is the file as it is now' in text
	)
	assert 'replay.fixed_later' in text and 'casing is right now' in text
	failed, text = mcp_call('cerb_read_text', {'path': OLD_LOG})  # a log the session kept writing
	assert '"cut_at_ticket": true' in text and '"total_lines": 2' in text  # read up to the post
	assert 'changed after the ticket' not in text
	assert cerb.as_now(CLIENT_INFO, 'Sheet1: 3 rows').startswith(
		f'{CLIENT_INFO}: changed after the ticket'
	)
	assert cerb.as_now(CLIENT_INFO, ['an image'])[1] == 'an image'
	assert cerb.find_project(1234)['projects'] == [PROJECT]  # not the project of December
	monkeypatch.delenv('DSS_REPLAY_AT')
	assert 'changed_after_ticket' not in str(cerb.list_folder(PROJECT))
	assert len(cerb.list_folder(PROJECT)['entries']) == 6
	assert cerb.read_text(OLD_LOG)['total_lines'] == 3
	assert len(cerb.find_project(1234)['projects']) == 2


@pytest.mark.parametrize(
	'name, later',
	[
		('DSS-1234', True),
		('dss-1234 rerun', True),
		('DSS-12345', False),  # another ticket
		('Analysis 2026-10-30.docx', True),
		('Analysis 2026-10-22.docx', False),  # the ticket's day
		('stats_20261030.csv', True),
		('Run 2026-11', True),
		('Run 2026-10', False),  # the ticket's month
		('DVT1234_2026-10-20_WI_Well-1', False),
	],
)
def test_names_that_say_they_were_made_after_the_ticket(replaying, name, later):
	assert cutoff.named_later(name) == later


def test_the_last_session_is_the_one_before_the_post_up_to_the_post(share, replaying):
	session = cerb.last_session(PROJECT, 'Ann Analyst', before='2026-10-23_00-00-00')
	assert session['file'] == ntpath.basename(OLD_LOG)  # not the session started after
	assert [run['name'] for run in session['failed_runs']] == ['casing']  # not the 10:30 one
	assert session['cut_at_ticket']  # so Claude knows the later part was left out


def test_prod_in_a_replay_is_the_newest_release_by_then(replaying, monkeypatch):
	monkeypatch.setattr(code, 'release_then', lambda: ('1.0.4', 'abc1234'))
	assert cerb.prod_version() == '1.0.4'


# The tool server and claude


def test_a_replay_stops_answering_after_its_calls_and_traces_leaks(
	replaying, tmp_path, monkeypatch, mcp_call
):
	server = MCPServer('test')
	tools = ReportingTools(server)

	@tools.tool()
	def echo(text: str) -> str:
		"""Echo *text*."""
		return text

	trace = tmp_path / 'trace.jsonl'
	monkeypatch.setenv('DSS_TRACE_FILE', str(trace))
	monkeypatch.setenv('DSS_REPLAY_MAX_CALLS', '2')
	mcp_call('echo', {'text': 'fixed in DSS-1234'}, server)
	mcp_call('echo', {'text': 'nothing later'}, server)
	failed, text = mcp_call('echo', {'text': 'one more'}, server)
	assert failed and 'used its 2 tool calls' in text
	lines = [json.loads(line) for line in trace.read_text(encoding='utf-8').splitlines()]
	assert lines[0]['leaks'] == ['names DSS-1234'] and 'leaks' not in lines[1]
	assert 'used its 2 tool calls' in lines[2]['error']


def test_calls_held_back_until_past_issues_are_searched_do_not_count_toward_the_cap(
	replaying, tmp_path, monkeypatch, mcp_call
):
	server = MCPServer('test')
	tools = ReportingTools(server)

	@tools.tool()
	def code_read(path: str) -> str:
		"""Lines of *path*."""
		return path

	@tools.tool()
	def jira_search(jql: str) -> str:
		"""Tickets matching *jql*."""
		raise jira.JiraError('a replay searches only by fields a ticket keeps')

	@tools.tool()
	def code_log(path: str) -> str:
		"""Commits that change *path*."""
		return path

	trace = tmp_path / 'trace.jsonl'
	monkeypatch.setenv('DSS_TRACE_FILE', str(trace))
	monkeypatch.setenv('DSS_STEP_ORDER', '1')
	monkeypatch.setenv('DSS_REPLAY_MAX_CALLS', '3')
	for _ in range(3):
		failed, text = mcp_call('code_read', {'path': 'cerberus/plots.py'}, server)
		assert failed and 'Not yet' in text  # held back: nothing read, nothing counted
	failed, text = mcp_call('jira_search', {'jql': 'status = Done'}, server)
	assert failed and 'fields a ticket keeps' in text  # refused, yet past issues were searched
	mcp_call('code_log', {'path': 'cerberus'}, server)
	failed, text = mcp_call('code_read', {'path': 'cerberus/plots.py'}, server)
	assert not failed and text == 'cerberus/plots.py'  # the third call that ran
	failed, text = mcp_call('code_read', {'path': 'cerberus/plots.py'}, server)
	assert failed and 'used its 3 tool calls' in text
	assert replay.calls({'trace': str(trace)}) == 4  # the held-back ones are not counted


def test_claude_can_be_stopped_at_a_cost():
	line = runner.command(budget=1.5)
	assert line[line.index('--max-budget-usd') + 1] == '1.50'
	assert '--max-budget-usd' not in runner.command()
	assert line[line.index('--tools') + 1] == '' and 'mcp__dss__*' in line  # still locked down


# The record


@pytest.fixture
def sources(monkeypatch):
	"""Jira, the project index and telemetry, faked as in test_record."""
	ticket = {'key': 'DSS-1234', 'created': jira_time(-0.001), 'reporter': 'Ann Analyst'}
	monkeypatch.setattr(jira, 'get_issue', lambda key, fields=None: {**ticket, 'dvt': 1234.0})
	monkeypatch.setattr(cerb, 'find_project', lambda dvt: {'projects': [PROJECT]})
	session = {'found': True, 'cerb': '1.0.3', 'failed_runs': [], 'last_config': ''}
	terminal_log = {'found': False, 'why': 'Cerberus 1.0.3 writes no terminal log'}
	monkeypatch.setattr(
		cerb, 'last_session', lambda *args: {**session, 'terminal_log': terminal_log}
	)
	monkeypatch.setattr(cerb, 'prod_version', lambda: '1.0.4')
	monkeypatch.setattr(code, 'web_links', lambda text: {})
	monkeypatch.setattr(code, 'refresh', list)  # nothing to note
	monkeypatch.delenv('JIRA_URL', raising=False)
	monkeypatch.delenv('CONFLUENCE_URL', raising=False)


def test_known_causes_learned_from_this_ticket_or_later_are_left_out(
	replaying, tmp_path, monkeypatch
):
	causes = [
		{'slug': 'seen-before', 'cause': 'A', 'dss_tickets': ['DSS-1000', 'DSS-1234']},
		{'slug': 'only-this', 'cause': 'B', 'dss_tickets': ['DSS-1234']},
		{'slug': 'learned-later', 'cause': 'C', 'dss_tickets': ['DSS-1300']},
		{'slug': 'from-docs', 'cause': 'D', 'dss_tickets': []},
	]
	known = tmp_path / 'known_causes.json'
	known.write_text(json.dumps(causes), encoding='utf-8')
	monkeypatch.setattr(record, 'KNOWN_CAUSES_FILE', known)
	prompt = record.system_prompt()
	assert '`seen-before`' in prompt and '`from-docs`' in prompt
	assert '`only-this`' not in prompt and '`learned-later`' not in prompt
	monkeypatch.delenv('DSS_REPLAY_KEY')
	assert '`only-this`' in record.system_prompt()


def test_the_prompt_keeps_general_rules_but_no_line_naming_this_ticket_or_later(
	replaying, tmp_path, monkeypatch
):
	steps = tmp_path / 'triage.md'
	rules = [
		'Gate A: check the install first.',
		'As in DSS-1234, check the casing ID.',  # the replayed ticket's answer
		'As in DSS-1300, check the plugs.',  # learned later
		'As in DSS-1100, check Zscaler.',  # learned before: a rule it could have had
		'## Answer in Markdown',
		'(replaced by the record answer)',
	]
	steps.write_text('\n'.join(rules), encoding='utf-8')
	monkeypatch.setattr(runner, 'SYSTEM', steps)
	prompt = record.system_prompt()
	assert 'Gate A: check the install first.' in prompt and 'DSS-1100' in prompt
	assert 'DSS-1234' not in prompt and 'DSS-1300' not in prompt


FACTS = {'cutoff': AT.isoformat(timespec='seconds'), 'build': '1.0.3', 'code_ref': 'abc1234'}


def replay_claude(answer: dict | str, prompts: list):
	"""A fake Claude that searched past issues (one call leaking the ticket's key) and answers."""

	def ask(prompt, trace=None):
		prompts.append(prompt)
		calls = [
			{'tool': 'jira_search', 'arguments': {'jql': 'project = DSS'}, 'seconds': 0.1},
			{
				'tool': 'code_log_s',
				'arguments': {'text': 'round'},
				'seconds': 0.1,
				'leaks': ['names DSS-1234'],
			},
			{'tool': 'code_read', 'arguments': {'path': 'cerberus/plots.py'}, 'seconds': 0.1},
		]
		trace.write_text(''.join(json.dumps(call) + '\n' for call in calls), encoding='utf-8')
		text = answer if isinstance(answer, str) else json.dumps(answer)
		return {'result': text, 'total_cost_usd': 0.4, 'usage': {}}

	return ask


def test_a_replay_asks_for_its_block_and_the_post_names_each_leak(sources, replaying, tmp_path):
	prompts = []
	leak = {'via': 'jira_get_issue(DTN-9)', 'what': 'a comment written after the ticket'}
	fixed = {'file': CLIENT_INFO, 'fix': 'Casing Record row 3 now has ID 4.55 in, as the tally'}
	block = {'fixed_later': [fixed], 'leaks': [leak]}
	answer = {'triage': EXAMPLE['triage'], 'class': EXAMPLE['class'], 'replay': block}
	folder = tmp_path / 'replays'
	result = record.trial(
		'DSS-1234',
		POSTED,
		ask=replay_claude(answer, prompts),
		replay=FACTS,
		folder=folder,
		trace=folder / 'DSS-1234.jsonl',
	)
	assert 'This is a replay of an old ticket' in prompts[0]
	assert 'replay' in record.answer_schema(replay=True)['required']  # the block's shape
	assert 'as if it were not fixed' in prompts[0]
	assert result['problems'] == []
	assert result['record']['replay'] == {
		**FACTS,
		**block,
		'leaks_found': ['code_log_s(text=round): names DSS-1234'],
	}
	decision = result['dm'].split('\n')
	assert decision[1].startswith('**Fixed later in a file:** ')
	assert decision[1].endswith(
		'client\\_info.xlsm: Casing Record row 3 now has ID 4.55 in, as the tally'
	)
	assert decision[2].startswith('**Possible leak:** a comment written after the ticket (via')
	assert decision[3] == '**Possible leaks found by code:** 1 tool answer, listed in the thread'
	assert result['thread'].endswith(
		'**Possible leaks found by code**\n- code\\_log\\_s(text=round): names DSS-1234'
	)
	assert Path(result['file']).parent == folder


def test_a_replay_answer_without_its_block_or_json_still_says_the_leaks(
	sources, replaying, tmp_path
):
	plain = {'triage': EXAMPLE['triage'], 'class': EXAMPLE['class']}
	result = record.trial('DSS-1234', POSTED, ask=replay_claude(plain, []), replay=FACTS)
	assert result['problems'] == []
	assert result['record']['replay']['fixed_later'] == []
	refused = record.trial('DSS-1234', POSTED, ask=replay_claude('no answer', []), replay=FACTS)
	assert '**Possible leaks found by code:** 1 tool answer' in refused['dm']
	assert '- code\\_log\\_s(text=round): names DSS-1234' in refused['thread']


def after(seconds: float, **message) -> dict:
	"""A thread message *seconds* after the replayed post."""
	return {'ts': f'{float(TS) + seconds:.6f}', **message}


def test_a_replays_thread_is_the_ticket_as_raised_by_its_analyst(replaying, monkeypatch):
	edited = {**workflow_post(), 'edited': {'ts': f'{float(TS) + 3600:.6f}'}}  # by U1, the analyst
	screenshot = after(1, bot_id='B1', text='Screenshot of error:', files=[{'name': 'error.png'}])
	reminder = after(2, bot_id='B1', text="Don't forget to check the troubleshooting page")
	more = after(40, user='U1', text='One more screenshot', files=[{'name': 'more.png'}])
	answer = after(60, user='U2', text='Fixed it: casing ID')  # someone else: the thread stops
	again = after(90, user='U1', text='Thanks, that worked')
	threads = {TS: [edited, screenshot, reminder, more, answer, again]}
	monkeypatch.setenv('DSS_SLACK_BOT_TOKEN', 'xoxb-test')
	monkeypatch.setattr(thread, '_client', lambda: FakeSlack())
	monkeypatch.setattr(thread, '_replies', lambda client, channel, ts: threads[ts])
	found = thread.thread(TS)
	assert found[0].endswith('the post and 3 replies, oldest first, in local time.')
	assert '(edited after the ticket time' in found[1]
	assert 'Screenshot of error:' in found[2] and 'error.png' in found[2]
	assert 'One more screenshot' in found[4] and 'more.png' in found[4]
	assert 'Fixed it' not in str(found) and 'that worked' not in str(found)
	monkeypatch.delenv('DSS_REPLAY_AT')  # outside a replay, the whole thread shows
	assert 'that worked' in str(thread.thread(TS))


def test_a_replay_reads_the_analysts_follow_ups_for_30_minutes(replaying, monkeypatch):
	soon = after(25 * 60, user='U1', text='The plot from the rerun', files=[{'name': 'b.png'}])
	solved = after(45 * 60, user='U1', text='Never mind, fixed it by resetting the casing ID')
	threads = {TS: [workflow_post(), soon, solved]}  # nobody else replied
	monkeypatch.setenv('DSS_SLACK_BOT_TOKEN', 'xoxb-test')
	monkeypatch.setattr(thread, '_client', lambda: FakeSlack())
	monkeypatch.setattr(thread, '_replies', lambda client, channel, ts: threads[ts])
	found = str(thread.thread(TS))
	assert 'The plot from the rerun' in found and 'Never mind' not in found


def test_a_replay_reads_other_tickets_threads_only_as_they_were(replaying, monkeypatch):
	older, later = f'{float(TS) - 86400:.6f}', '1900000000.000001'
	threads = {
		older: [
			workflow_post('DSS-1100', older),
			after(-80000, user='U2', text='Known issue: reset the casing ID'),
			after(60, user='U2', text='Closing it now'),  # after the replayed post
		],
		later: [workflow_post('DSS-1300', later)],
	}
	monkeypatch.setenv('DSS_SLACK_BOT_TOKEN', 'xoxb-test')
	monkeypatch.setattr(thread, '_client', lambda: FakeSlack())
	monkeypatch.setattr(thread, '_replies', lambda client, channel, ts: threads[ts])
	found = str(thread.thread(older))
	assert 'Known issue' in found and 'Closing it now' not in found
	with pytest.raises(ValueError, match='posted after the ticket time'):
		thread.thread(later)


# The replay command


def workflow_post(key: str = 'DSS-1234', ts: str = TS, **extra) -> dict:
	return {'ts': ts, 'bot_id': 'B1', 'text': f'<@U1> created {key}: plot is wrong', **extra}


class FakeSlack:
	"""A slack_sdk WebClient stand-in: channel history, names, links and posts, recorded."""

	def __init__(self, history=(), replies=()):
		self.history, self.replies = list(history), list(replies)
		self.calls: list = []

	def __getattr__(self, method):
		def call(**kwargs):
			self.calls.append((method, kwargs))
			if method == 'conversations_history':
				return {'messages': self.history}
			if method == 'conversations_replies':
				return {'messages': self.replies}
			if method == 'users_info':
				return {'user': {'profile': {'display_name': 'Ann'}}}
			if method == 'chat_getPermalink':
				return {'permalink': 'https://slack/p1'}
			return {'ok': True, 'ts': f'900.{len(self.calls)}'}

		return call


def test_the_workflow_post_is_found_around_the_ticket():
	reply = workflow_post(ts='1.2', thread_ts='1.1')
	client = FakeSlack([workflow_post('DSS-12345'), reply, workflow_post()])
	found = replay.find_post(client, slack.CHANNEL, 'DSS-1234', at(-0.001))
	assert found is not None and found['ts'] == TS
	((_, asked),) = client.calls
	assert float(asked['oldest']) < float(TS) < float(asked['latest'])
	assert replay.find_post(FakeSlack([reply]), slack.CHANNEL, 'DSS-1234', AT) is None


def test_a_plan_reads_the_ticket_as_posted_and_finds_its_code(monkeypatch):
	seen = []

	def build_ticket(key, posted):
		seen.append((posted, os.environ['DSS_REPLAY_AT'], os.environ.get('DV_CERB_REF')))
		return {'cerberus_version': '1.0.3'}, []

	monkeypatch.setenv('DV_CERB_REF', 'from-env')
	monkeypatch.setattr(jira, 'get_issue', lambda key, fields: {'created': jira_time(-0.001)})
	monkeypatch.setattr(record, 'build_ticket', build_ticket)
	monkeypatch.setattr(code, 'release_then', lambda: ('1.0.4', 'rel1004'))
	monkeypatch.setattr(code, 'version_ref', lambda version: f'ref-{version}')
	client = FakeSlack([workflow_post()])
	plan = replay.prepare('DSS-1234', client, slack.Names(client))
	assert seen == [(POSTED, TS, None)]  # as of the post, the code not yet chosen
	assert os.environ['DV_CERB_REF'] == 'from-env' and 'DSS_REPLAY_AT' not in os.environ
	assert (plan['build'], plan['ref'], plan['note']) == ('1.0.3', 'ref-1.0.3', '')
	assert (plan['analyst'], plan['link']) == ('Ann', 'https://slack/p1')
	assert plan['post'].startswith('@Ann created DSS-1234')
	monkeypatch.setattr(
		record, 'build_ticket', lambda key, posted: ({'cerberus_version': None}, [])
	)
	plan = replay.prepare('DSS-1234', client, slack.Names(client))
	assert plan['ref'] == 'rel1004' and 'no Cerberus session' in plan['note']


PLAN = {
	'key': 'DSS-1234',
	'ts': TS,
	'posted': POSTED,
	'post': 'created DSS-1234',
	'link': 'https://slack/p1',
	'analyst': 'Ann',
	'build': '1.0.3',
	'ref': 'abc1234',
	'note': '',
}


def test_a_replay_runs_as_of_the_post_and_a_stopped_one_is_saved(sources, monkeypatch, tmp_path):
	settings = []

	def ask(prompt, trace=None):
		settings.append((os.environ.get('DSS_THREAD_UNTIL'), os.environ['DV_CERB_REF']))
		return replay_claude({'triage': EXAMPLE['triage'], 'class': EXAMPLE['class']}, [])(
			prompt, trace
		)

	result = replay.replay(PLAN, tmp_path, ask)
	# no time cut on the thread (thread._as_raised cuts it at the first reply from someone else),
	# and the analyst's build
	assert settings == [(None, 'abc1234')]
	assert result['record']['replay']['code_ref'] == 'abc1234'
	assert (
		'Replay of DSS-1234 as posted 2026-10-22 10:00 · Cerberus 1.0.3 at abc1234' in result['dm']
	)
	assert 'DSS_REPLAY_AT' not in os.environ
	assert 'settled at' in replay.outcome(result) and '3 calls · 1 leaks' in replay.outcome(result)

	def over_budget(prompt, trace=None):
		raise RuntimeError('claude -p ended without an answer: error_max_budget_usd')

	stopped = replay.replay(PLAN, tmp_path, over_budget)
	assert 'error_max_budget_usd' in stopped['stopped'] and Path(stopped['file']).exists()
	assert replay.outcome(stopped).startswith('DSS-1234: stopped:')


def test_the_command_shows_its_plan_or_runs_and_posts_only_to_the_test_channel(
	monkeypatch, tmp_path
):
	monkeypatch.setattr(code, 'refresh', list)  # nothing to note
	monkeypatch.setattr(replay, 'prepare', lambda key, client, names: {**PLAN, 'key': key})
	ran = []
	saved = tmp_path / 'DSS-1234.json'

	def run(plan, folder, ask):
		ran.append(plan['key'])
		result = {'key': plan['key'], 'dm': 'decision', 'thread': 'proof', 'stopped': 'x'}
		saved.write_text(json.dumps(result), encoding='utf-8')
		return {**result, 'file': str(saved)}

	monkeypatch.setattr(replay, 'replay', run)
	monkeypatch.setattr(replay, 'outcome', lambda result: f'{result["key"]} done')
	said: list[str] = []
	answered = [workflow_post(), after(41 * 60, user='U2', text='Which casing record?')]
	client = FakeSlack(replies=answered)
	replay.main(['DSS-1234', 'DSS-1234'], client=client, say=said.append)
	assert said[0].startswith('note: no GitHub push history, so commit dates stand in')
	assert ran == [] and said[-1].startswith('dry run') and len(said) == 3  # each ticket once
	replay.main(['DSS-1234'], go=True, post_test=True, client=client, say=said.append)
	# the developers' time, from the whole thread, read after the replay and saved with it
	assert json.loads(saved.read_text(encoding='utf-8'))['developers']['response_minutes'] == 41
	posts = [call for method, call in client.calls if method == 'chat_postMessage']
	assert [post['channel'] for post in posts] == [replay.TEST_CHANNEL, replay.TEST_CHANNEL]
	first = [method for method, _ in client.calls].index('chat_postMessage') + 1
	assert 'thread_ts' not in posts[0] and posts[1]['thread_ts'] == f'900.{first}'
	monkeypatch.setenv('DSS_SLACK_TEST_CHANNEL', slack.CHANNEL)
	with pytest.raises(ValueError, match='never go to #data-dev-support'):
		replay.main(['DSS-1234'], go=True, post_test=True, client=client, say=said.append)
	with pytest.raises(RuntimeError, match='never go to #data-dev-support'):
		slack.post(client, slack.CHANNEL, 'decision')


def test_developer_time_runs_from_their_first_reply_to_their_last():
	thread_then = [
		workflow_post(),  # the analyst is U1
		after(1, bot_id='B1', text='Screenshot of error:'),
		after(5 * 60, user='U1', text='One more screenshot'),  # the analyst: not a developer
		after(41 * 60, user='U2', text='Which casing record?'),
		after(52 * 60, user='U1', text='Row 3'),
		after(65 * 60, user='U3', text='Set it to 4.55 in'),
		after(102 * 60, user='U2', text='Closing as User Error'),
	]
	found = replay.developer_time(thread_then, 'U1')
	assert (found['replies'], found['response_minutes'], found['solving_minutes']) == (3, 41, 61)
	assert found['first_reply'].startswith('2026-10-22T10:41:05')
	assert replay._developers_line(found) == ' · developers 1 h 1 min (first reply 41 min in)'
	unanswered = replay.developer_time(thread_then[:3], 'U1')
	assert unanswered['solving_minutes'] is None
	assert replay._developers_line(unanswered) == ' · no developer replied'
	assert [replay._duration(minutes) for minutes in (0.5, 12, 125, 3000)] == [
		'30 s',
		'12 min',
		'2 h 5 min',
		'2 d 2 h',
	]


def test_the_cli_takes_tickets_or_a_golden_set(monkeypatch, tmp_path):
	asked = []
	monkeypatch.setattr(cli, 'load_env', lambda: None)
	monkeypatch.setattr(
		replay, 'main', lambda keys, go, post_test: asked.append((keys, go, post_test))
	)
	golden = tmp_path / 'golden.jsonl'
	golden.write_text('{"key": "DSS-1100"}\n\n{"key": "DSS-1101"}\n', encoding='utf-8')
	monkeypatch.setattr(sys, 'argv', ['dss', 'replay', 'DSS-1234', '--golden', str(golden), '--go'])
	cli.main()
	assert asked == [(['DSS-1234', 'DSS-1100', 'DSS-1101'], True, False)]
	for argv in (['dss', 'replay'], ['dss', 'replay', 'dss-1']):
		monkeypatch.setattr(sys, 'argv', argv)
		with pytest.raises(SystemExit):
			cli.main()


def test_no_setting_file_starts_a_replay(monkeypatch, tmp_path):
	env = tmp_path / '.env'
	env.write_text(f'DSS_REPLAY_AT={TS}\nDSS_REPLAY_KEY=DSS-1234\nJIRA_URL=https://acme.example\n')
	monkeypatch.setattr(os, 'environ', {})
	cli.load_env(env)
	assert os.environ == {'JIRA_URL': 'https://acme.example'}


@pytest.mark.parametrize('command', ['slack', 'triage', 'trial', 'replay'])
def test_only_the_tools_a_replay_starts_run_in_one(command, monkeypatch, capsys):
	monkeypatch.setattr(cli, 'load_env', lambda: None)
	monkeypatch.setenv('DSS_REPLAY_AT', TS)  # in the Windows environment by mistake, say
	monkeypatch.setenv('DSS_REPLAY_KEY', 'DSS-1234')
	monkeypatch.setenv('DSS_REPLAY_MAX_CALLS', '30')
	seen = []

	def regular(*args, **kwargs):
		seen.append(cutoff.when())
		raise SystemExit(0)

	monkeypatch.setattr('dss.slack.main', regular)
	monkeypatch.setattr(cli, 'triage', regular)
	monkeypatch.setattr(record, 'trial', regular)
	monkeypatch.setattr(replay, 'main', regular)
	argv = ['dss', command, 'DSS-1234']
	if command == 'slack':
		argv.pop()
	monkeypatch.setattr(sys, 'argv', argv)
	with pytest.raises(SystemExit):
		cli.main()
	assert seen == [None] and not any(name in os.environ for name in cutoff.SETTINGS)
	assert 'ignored DSS_REPLAY_AT, DSS_REPLAY_KEY, DSS_REPLAY_MAX_CALLS' in capsys.readouterr().err
