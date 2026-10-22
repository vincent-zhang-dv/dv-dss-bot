"""DSS triage tools: Jira fields flattened, project files through the index, code at the release."""

import base64
import io
import json
import ntpath
import subprocess
import sys
import tempfile
import urllib.error
import urllib.parse
from email.message import Message
from types import SimpleNamespace

import pytest

from dss import cerb, code, jira


def test_jira_issue_keeps_only_plain_triage_fields():
	issue = {
		'key': 'DSS-1',
		'fields': {
			'summary': 'Plot is wrong',
			'description': 'x' * 5000,
			'status': {'name': 'To Do', 'id': '1'},
			'reporter': {'displayName': 'Jane Doe', 'accountId': 'abc'},
			'issuelinks': [
				{
					'type': {'inward': 'is caused by', 'outward': 'causes'},
					'outwardIssue': {'key': 'DTN-2', 'fields': {'summary': 'Fix it'}},
				}
			],
			'customfield_10824': {'value': 'CERB-PE', 'id': '9'},
			'customfield_13664': 1234.0,
		},
	}
	found = jira.compact(issue, jira.TICKET_FIELDS)
	assert found['status'] == 'To Do' and found['reporter'] == 'Jane Doe'
	assert found['toolbox'] == 'CERB-PE' and found['dvt'] == 1234.0
	assert found['links'] == ['causes DTN-2: Fix it']
	assert len(found['description']) == jira.DESCRIPTION_CHARS
	assert found['outcome'] is None
	assert jira.plain([{'value': 'CERB-WI'}, {'name': 'Done'}, 7]) == ['CERB-WI', 'Done', 7]


def test_jira_rejects_a_key_before_connecting():
	with pytest.raises(ValueError, match='not an issue key'):
		jira.get_issue('DSS-1 OR project = HR')


@pytest.fixture
def dvjira(monkeypatch):
	"""arch's DVJira, faked: records every request and answers with one ticket."""
	asked: list[tuple] = []

	class FakeJira:
		def issue(self, key, fields):
			asked.append(('issue', key, fields))
			return {'key': key, 'fields': {'summary': 'Plot is wrong', 'labels': ['x']}}

		def enhanced_jql(self, jql, fields, limit):
			asked.append(('search', jql, fields, limit))
			ticket = {'key': 'DSS-7', 'fields': {'summary': 'Old plot', 'created': '2026-01-02'}}
			return {'issues': [ticket]}

	class FakeDVJira:
		def __init__(self, user_email):
			asked.append(('login', user_email))
			self.jira = FakeJira()

	monkeypatch.setitem(sys.modules, 'connectors.dvjira', SimpleNamespace(DVJira=FakeDVJira))
	monkeypatch.setenv('DV_JIRA_EMAIL', 'bot@acme.test')
	return asked


def test_jira_asks_only_for_the_triage_fields(dvjira, monkeypatch):
	ticket = jira.get_issue('DSS-1234')
	assert ticket['summary'] == 'Plot is wrong' and 'labels' not in ticket
	found = jira.search('project = DSS AND text ~ "plot"', limit=100)
	assert found == [
		{
			'key': 'DSS-7',
			'summary': 'Old plot',
			'status': None,
			'created': '2026-01-02',
			'toolbox': None,
			'outcome': None,
		}
	]
	login, issue, search = dvjira[0], dvjira[1], dvjira[3]
	assert login == ('login', 'bot@acme.test')
	assert issue == ('issue', 'DSS-1234', ','.join(jira.TICKET_FIELDS.values()))
	assert search[2] == ','.join(jira.HISTORY_FIELDS.values())
	assert search[3] == jira.MAX_RESULTS
	monkeypatch.delenv('DV_JIRA_EMAIL')
	with pytest.raises(RuntimeError, match='set DV_JIRA_EMAIL'):
		jira.get_issue('DSS-1234')


def test_jira_tools_answer_through_mcp(dvjira, monkeypatch, mcp_call):
	def in_this_process(app, module, func, **kwargs):  # instead of the arch venv
		assert (app, module) == ('arch', 'dss.jira')
		return getattr(jira, func)(**kwargs)

	monkeypatch.setattr(jira, 'run_in', in_this_process)
	failed, ticket = mcp_call('jira_get_issue', {'key': 'DSS-1234'})
	assert not failed and 'Plot is wrong' in ticket
	failed, found = mcp_call('jira_search', {'jql': 'project = DSS', 'limit': 5})
	assert not failed and 'DSS-7' in found
	assert dvjira[-1] == ('search', 'project = DSS', ','.join(jira.HISTORY_FIELDS.values()), 5)


ROOT = r'\\nas\projects'
PROJECT = ROOT + r'\ACME\DVT1234_2026-10_PE_Well'
TELEMETRY = PROJECT + r'\cerberus\telemetry'


@pytest.fixture
def index(monkeypatch):
	"""A fake project index over {UNC path: bytes}.

	Every search answers with every file and folder, so the tools' own filters must pick.
	"""
	files: dict[str, bytes] = {}

	def get(url_path):
		if not url_path.startswith('/?'):
			parts = [urllib.parse.unquote(part) for part in url_path[1:].split('/')]
			return files['\\'.join(parts)]
		hits, folders = [], set()
		for path, data in files.items():
			hits.append(
				{'type': 'file', 'name': ntpath.basename(path), 'path': ntpath.dirname(path)}
			)
			hits[-1]['size'] = str(len(data))
			folder = ntpath.dirname(path)
			while cerb._key(folder) != cerb._key(ROOT):
				folders.add(folder)
				folder = ntpath.dirname(folder)
		for folder in folders:
			hits.append(
				{'type': 'folder', 'name': ntpath.basename(folder), 'path': ntpath.dirname(folder)}
			)
		return json.dumps({'results': hits}).encode()

	monkeypatch.setenv('DV_PROJECT_INDEX_ROOT', ROOT)
	monkeypatch.setattr(cerb, '_get', get)
	return files


def test_last_session_is_the_reporters_newest_before_the_ticket(index):
	index[TELEMETRY + r'\DoeJane_telemetry_2026-10-20_09-00-00_11.log'] = (
		b'event=session phase=start schema=1 cerb=1.0.3\n'
		b'event=config_saved name=deliv config="configs/pe deliv.json"\n'
		b'event=script_run phase=stop name=make_plots status=ok\n'
		b'event=script_run phase=stop name=make_doc status=failed result_detail="KeyError: \'x\'"'
	)
	index[TELEMETRY + r'\DoeJane_telemetry_2026-10-20_13-00-00_12.log'] = b'cerb=9.9.9'
	index[TELEMETRY + r'\RoeRick_telemetry_2026-10-20_10-00-00_13.log'] = b'cerb=8.8.8'
	index[TELEMETRY + r'\old\DoeJane_telemetry_2026-10-20_11-00-00_14.log'] = b'cerb=7.7.7'
	index[PROJECT + r'\DVT1234_2026-10_PE_Well copy\notes.txt'] = b''  # not a project folder
	found = cerb.last_session(PROJECT, 'Jane Doe', '2026-10-20_12-17-30')
	assert found['file'] == 'DoeJane_telemetry_2026-10-20_09-00-00_11.log'
	assert found['cerb'] == '1.0.3' and found['last_config'] == 'configs/pe deliv.json'
	assert [run['name'] for run in found['failed_runs']] == ['make_doc']
	assert cerb.find_project(1234) == {'projects': [PROJECT]}


def test_last_session_says_when_there_is_none_and_checks_its_input(index):
	index[TELEMETRY + r'\DoeJane_telemetry_2026-10-20_13-00-00_12.log'] = b'cerb=9.9.9'
	found = cerb.last_session(PROJECT, 'Jane Doe', '2026-10-20_12-17-30')
	assert found == {
		'found': False,
		'folder': TELEMETRY,
		'user': 'Jane Doe',
		'before': '2026-10-20_12-17-30',
	}
	with pytest.raises(ValueError, match='before must look like'):
		cerb.last_session(PROJECT, 'Jane Doe', '2026-10-20 12:17')
	with pytest.raises(ValueError, match='no name to match'):
		cerb.last_session(PROJECT, '42')


def test_project_files_are_listed_read_and_copied(index, tmp_path, monkeypatch):
	index[PROJECT + r'\DVT1234_client_info.xlsm'] = b'PK\x03\x04\0\0'
	index[PROJECT + r'\configs\pe deliv.json'] = '\n'.join(
		f'line {number}' for number in range(1, 501)
	).encode()
	listed = [(entry['name'], entry['type']) for entry in cerb.list_folder(PROJECT)['entries']]
	assert listed == [('configs', 'folder'), ('DVT1234_client_info.xlsm', 'file')]
	page = cerb.read_text(PROJECT + r'\configs\pe deliv.json', first_line=401)
	assert page['total_lines'] == 500 and page['text'].splitlines() == [
		f'line {number}' for number in range(401, 501)
	]
	with pytest.raises(ValueError, match='not a text file'):
		cerb.read_text(PROJECT + r'\DVT1234_client_info.xlsm')
	monkeypatch.setattr(tempfile, 'tempdir', str(tmp_path))
	copy = cerb.local_copy(PROJECT + r'\DVT1234_client_info.xlsm')
	assert copy == tmp_path / 'dv_project_index' / 'ACME' / PROJECT.rsplit('\\', 1)[1] / copy.name
	assert copy.read_bytes() == b'PK\x03\x04\0\0'


def test_project_tools_read_only_inside_the_share(index):
	for path in (r'\\nas\other\x.txt', r'\\hades-vfs\projects\x.txt', r'C:\x.txt', ROOT):
		with pytest.raises(ValueError, match='outside the project share'):
			cerb.read_text(path)


def test_the_index_says_which_login_to_set(monkeypatch):
	for name in cerb.INDEX_ENV:
		monkeypatch.delenv(name, raising=False)
	with pytest.raises(RuntimeError, match='set DV_PROJECT_INDEX_USER, DV_PROJECT_INDEX_PASSWORD'):
		cerb.find_project(1234)


def test_index_requests_carry_the_login_and_explain_each_failure(monkeypatch):
	monkeypatch.setenv('DV_PROJECT_INDEX_USER', 'ana')
	monkeypatch.setenv('DV_PROJECT_INDEX_PASSWORD', 'secret')
	monkeypatch.setenv('DV_PROJECT_INDEX_URL', 'http://index.test/')  # read at each call
	monkeypatch.setenv('DV_PROJECT_INDEX_ROOT', ROOT)
	sent = []
	outcome: dict = {}

	def urlopen(request, timeout):
		sent.append(request)
		if 'error' in outcome:
			raise outcome['error']
		return io.BytesIO(outcome.get('body', b'{"results": []}'))

	monkeypatch.setattr(cerb.urllib.request, 'urlopen', urlopen)
	assert cerb.find_project(1234) == {'projects': []}
	assert sent[0].get_method() == 'GET'
	assert sent[0].full_url.startswith('http://index.test/?search=folder')
	login = base64.b64encode(b'ana:secret').decode()
	assert sent[0].get_header('Authorization') == f'Basic {login}'
	failures = [
		(urllib.error.HTTPError('u', 401, 'Unauthorized', Message(), None), 'refused the login'),
		(urllib.error.HTTPError('u', 404, 'Not Found', Message(), None), 'no such file'),
		(urllib.error.HTTPError('u', 503, 'Busy', Message(), None), 'answered 503 Busy'),
		(TimeoutError('timed out'), 'did not answer within 60 s'),
		(urllib.error.URLError(TimeoutError('timed out')), 'did not answer within 60 s'),
		(urllib.error.URLError('refused'), 'cannot reach the project index at http://index.test'),
	]
	for error, message in failures:
		outcome['error'] = error
		with pytest.raises(RuntimeError, match=message):
			cerb.find_project(1234)
	outcome.clear()
	outcome['body'] = b'x' * 11
	monkeypatch.setattr(cerb, 'MAX_FETCH_BYTES', 10)
	with pytest.raises(RuntimeError, match='too big to read'):
		cerb.read_text(PROJECT + r'\notes.txt')


def test_prod_version_is_the_highest_wheel(tmp_path, monkeypatch):
	monkeypatch.setenv('DV_CERB_PROD_DIR', str(tmp_path))
	with pytest.raises(FileNotFoundError, match='no Cerberus wheel'):
		cerb.prod_version()
	for name in ('cerberus-1.0.4-py3-none-any.whl', 'cerberus-1.0.10-py3-none-any.whl'):
		(tmp_path / name).touch()
	(tmp_path / 'cerberus-latest.whl').touch()  # not a release wheel
	assert cerb.prod_version() == '1.0.10'


class FakeFrame:
	def __init__(self, rows):
		self.rows = rows

	def to_dict(self, orient):
		assert orient == 'records'
		return self.rows


class FakeClientInfo:
	"""Cerberus's DHClientInfo, faked: *sheets* are the sheets the client info file has."""

	sheets: dict = {'casing_record': {}}

	def __init__(self, ci_path):
		self.ci_path = ci_path

	def initialize_client_info(self):
		self.version = '1.6'
		self.sheetmaps = self.sheets
		return self

	def get_wellinfo(self):
		return {'well_name': 'Test 1'}

	def get_casingrecord(self):
		joints = FakeFrame([{'joint': '1', 'd_top': 0.0, 'd_bot': 40.0}])
		strings = FakeFrame([{'csg_od': 5.5, 'd_top': 0.0, 'd_bot': 40.0}])
		return joints, 'ft', 'in', 'psi', strings


def test_client_info_reads_the_casing_record_when_the_file_has_one(monkeypatch):
	clientinfo = SimpleNamespace(DHClientInfo=FakeClientInfo)
	monkeypatch.setitem(sys.modules, 'cerberus.modules.common.clientinfo', clientinfo)
	found = cerb.client_info('DVT1234_client_info.xlsm')
	assert found == {
		'version': '1.6',
		'wellinfo': {'well_name': 'Test 1'},
		'units': {'depth': 'ft', 'diameter': 'in', 'pressure': 'psi'},
		'strings': [{'csg_od': 5.5, 'd_top': 0.0, 'd_bot': 40.0}],
		'joints': [{'joint': '1', 'd_top': 0.0, 'd_bot': 40.0}],
	}
	monkeypatch.setattr(FakeClientInfo, 'sheets', {})  # an older client info version
	assert set(cerb.client_info('DVT1234_client_info.xlsm')) == {'version', 'wellinfo'}


def git(repo, *args):
	command = ['git', '-c', 'user.name=t', '-c', 'user.email=t@t', *args]
	subprocess.run(command, cwd=repo, check=True, capture_output=True)


@pytest.fixture
def released(tmp_path, monkeypatch):
	"""A Cerberus-like repo: release v1.0.4, then a fix on origin/master, then a local edit."""
	repo = tmp_path / 'repo'
	(repo / 'cerberus').mkdir(parents=True)
	module = repo / 'cerberus' / 'plots.py'
	git(repo, 'init', '-q')
	module.write_text('y = data.round(2)\n')
	git(repo, 'add', '.')
	git(repo, 'commit', '-q', '-m', 'Add plots')
	git(repo, 'commit', '-q', '--allow-empty', '-m', 'release: v1.0.4 (#975)')
	module.write_text('y = data\n')
	git(repo, 'commit', '-q', '-am', 'Stop rounding post-frac points')
	git(repo, 'update-ref', 'refs/remotes/origin/master', 'HEAD')
	module.write_text('y = local_edit\n')  # the working tree must never be read
	(tmp_path / 'cerberus-1.0.4-py3-none-any.whl').touch()
	monkeypatch.setenv('DV_CERB_PROD_DIR', str(tmp_path))
	monkeypatch.setenv('DSS_CERB_CWD', str(repo))
	monkeypatch.delenv('DV_CERB_REF', raising=False)
	return repo


def test_code_reads_the_prod_release_not_later_commits_or_the_working_tree(released):
	assert 'y = data.round(2)' in code.read('cerberus/plots.py')
	assert 'cerberus/plots.py:1:y = data.round(2)' in code.grep('.round(2)')
	assert 'cerberus/plots.py:1:' in code.grep('round[(][0-9][)]', regex=True)
	assert 'nothing found' in code.grep('local_edit')
	released_log = code.log_s('round(2)')
	assert 'Add plots' in released_log and 'Stop rounding' not in released_log
	assert 'Stop rounding' in code.log_s('round(2)', all_branches=True)


def test_code_reads_another_commit_when_told_and_says_what_is_missing(released, monkeypatch):
	monkeypatch.setenv('DV_CERB_REF', 'origin/master')
	assert 'y = data\n' in code.read('cerberus/plots.py') + '\n'
	assert '1\ty = data' in code.read('cerberus/plots.py', start=1, end=1)
	with pytest.raises(RuntimeError, match='git show failed'):
		code.read('cerberus/missing.py')
	monkeypatch.delenv('DV_CERB_REF')
	(released.parent / 'cerberus-1.0.5-py3-none-any.whl').touch()  # released, not fetched yet
	with pytest.raises(LookupError, match='no "release: v1.0.5" commit'):
		code.read('cerberus/plots.py')


def test_code_history_search_skips_text_filters_on_binary_files(released):
	# A text filter for .docx that fails, as Git for Windows' astextplain did on Office lock files
	(released / '.gitattributes').write_bytes(b'*.docx diff=broken\n')
	(released / 'cerberus' / 'report.docx').write_bytes(b'PK\x03\x04 binary\x00')
	git(released, 'config', 'diff.broken.textconv', 'false')
	git(released, 'add', '.gitattributes', 'cerberus/report.docx')
	git(released, 'commit', '-q', '-m', 'Add the report template')
	git(released, 'update-ref', 'refs/remotes/origin/master', 'HEAD')
	plain = subprocess.run(
		['git', 'log', '-Sround(2)', '--remotes', '--', 'cerberus'],
		cwd=released,
		capture_output=True,
		check=False,
	)
	assert plain.returncode != 0  # the filter breaks a search that uses it
	found = code.log_s('round(2)', all_branches=True)
	assert 'Add plots' in found and 'Add the report template' not in found


def test_long_code_answers_are_cut_with_a_hint(monkeypatch):
	monkeypatch.setattr(code, 'MAX_LINES', 2)
	hint = '... 1 more lines; narrow the pattern or the path'
	assert code._capped('Matches', ['a', 'b', 'c']) == f'Matches:\na\nb\n{hint}'
	assert code._capped('Matches', []) == 'Matches: nothing found'


def test_project_and_code_tools_answer_through_mcp(index, released, mcp_call):
	log = TELEMETRY + r'\DoeJane_telemetry_2026-10-20_09-00-00_11.log'
	index[log] = b'event=session phase=start cerb=1.0.3'
	failed, listing = mcp_call('cerb_list', {'folder': PROJECT})
	assert not failed and '"cerberus"' in listing
	failed, text = mcp_call('cerb_read_text', {'path': log, 'first_line': 1})
	assert not failed and 'cerb=1.0.3' in text
	session = {'project': PROJECT, 'user': 'Jane Doe', 'before': '2026-10-21_00-00-00'}
	failed, facts = mcp_call('cerb_last_session', session)
	assert not failed and '"cerb": "1.0.3"' in facts
	failed, found = mcp_call('code_grep', {'pattern': '.round(2)'})
	assert not failed and found.endswith('cerberus/plots.py:1:y = data.round(2)')
	failed, lines = mcp_call('code_read', {'path': 'cerberus/plots.py', 'start': 1, 'end': 1})
	assert not failed and lines.endswith('1\ty = data.round(2)')
	failed, commits = mcp_call('code_log_s', {'text': 'round(2)'})
	assert not failed and 'Add plots' in commits


def test_code_paths_stay_inside_the_repo(released):
	with pytest.raises(ValueError, match='not a path inside'):
		code.read('../secrets.txt')
	with pytest.raises(ValueError, match='not a path inside'):
		code.grep('x', '--output=/tmp/x')
