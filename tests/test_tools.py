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
import zipfile
from datetime import datetime, timedelta
from email.message import Message
from pathlib import Path
from types import SimpleNamespace
from typing import ClassVar

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
			'resolution': {'name': 'Done', 'id': '1'},
			'fixVersions': [
				{'name': 'CERBERUS v1.1', 'released': False, 'releaseDate': '2026-11-03'},
				{'name': 'CERBERUS v1.0.4', 'released': True, 'releaseDate': '2026-10-09'},
				{'name': 'Backlog', 'released': False},
			],
			'comment': {
				'total': 12,
				'comments': [
					{
						'author': {'displayName': f'Dev {number}'},
						'created': f'2026-10-{number + 10}T11:04:58.443-0700',
						'body': f'Comment {number}',
					}
					for number in range(1, 13)
				],
			},
			'attachment': [
				{
					'filename': 'plot.png',
					'mimeType': 'image/png',
					'size': 2048,
					'created': '2026-10-27T15:43:15.061-0700',
				}
			],
		},
	}
	found = jira.compact(issue, jira.TICKET_FIELDS)
	assert found['status'] == 'To Do' and found['reporter'] == 'Jane Doe'
	assert found['toolbox'] == 'CERB-PE' and found['dvt'] == 1234.0
	assert found['links'] == ['causes DTN-2: Fix it']
	assert len(found['description']) == jira.DESCRIPTION_CHARS
	assert found['outcome'] is None
	assert found['resolution'] == 'Done'
	assert found['fix_versions'] == [
		'CERBERUS v1.1 (not released, due 2026-11-03)',
		'CERBERUS v1.0.4 (released 2026-10-09)',
		'Backlog (not released)',
	]
	assert found['comments'][0] == '(2 older comments left out)'  # the newest 10 are kept
	assert found['comments'][1] == '2026-10-13 Dev 3: Comment 3'
	assert found['comments'][-1] == '2026-10-22 Dev 12: Comment 12'
	assert found['attachments'] == ['plot.png (image/png, 2 KB, 2026-10-27)']
	assert jira.plain([{'value': 'CERB-WI'}, {'name': 'Done'}, 7]) == ['CERB-WI', 'Done', 7]


def test_long_jira_comments_are_cut():
	issue = {'key': 'DTN-1', 'fields': {'comment': {'comments': [{'body': 'x' * 5000}]}}}
	(comment,) = jira.compact(issue, jira.TICKET_FIELDS)['comments']
	assert comment == ' unknown: ' + 'x' * jira.COMMENT_CHARS


def test_jira_rejects_a_key_before_connecting():
	with pytest.raises(ValueError, match='not an issue key'):
		jira.get_issue('DSS-1 OR project = HR')


@pytest.fixture
def jira_site(monkeypatch):
	"""Jira's REST API, faked: records every request and answers with one ticket."""
	for name, value in zip(jira.ENV, ('https://dv.atlassian.net/', 'bot@acme.test', 't')):
		monkeypatch.setenv(name, value)
	requests = []

	def answer(request, timeout):
		requests.append(request)
		if '/issue/DSS-404' in request.full_url:
			raise urllib.error.HTTPError(request.full_url, 404, 'Not Found', Message(), None)
		body: dict
		if '/search/jql' in request.full_url:
			ticket = {'key': 'DSS-7', 'fields': {'summary': 'Old plot', 'created': '2026-01-02'}}
			body = {'issues': [ticket], 'isLast': True}
		else:
			body = {'key': 'DSS-1234', 'fields': {'summary': 'Plot is wrong', 'labels': ['x']}}
		return io.BytesIO(json.dumps(body).encode())

	monkeypatch.setattr(jira.urllib.request, 'urlopen', answer)
	return requests


def test_jira_asks_only_for_the_triage_fields(jira_site):
	ticket = jira.get_issue('DSS-1234')
	assert ticket['summary'] == 'Plot is wrong' and 'labels' not in ticket
	found = jira.search('project = DSS AND text ~ "plot"', limit=100)
	assert found == [
		{
			'key': 'DSS-7',
			'summary': 'Old plot',
			'status': None,
			'resolution': None,
			'fix_versions': [],
			'created': '2026-01-02',
			'toolbox': None,
			'outcome': None,
		}
	]
	issue, search = jira_site
	assert issue.full_url.startswith('https://dv.atlassian.net/rest/api/2/issue/DSS-1234?')
	assert issue.get_header('Authorization') == 'Basic Ym90QGFjbWUudGVzdDp0'
	asked = urllib.parse.parse_qs(urllib.parse.urlsplit(issue.full_url).query)
	assert asked['fields'] == [','.join(jira.TICKET_FIELDS.values())]
	assert search.full_url.startswith('https://dv.atlassian.net/rest/api/2/search/jql?')
	asked = urllib.parse.parse_qs(urllib.parse.urlsplit(search.full_url).query)
	assert asked['jql'] == ['project = DSS AND text ~ "plot"']
	assert asked['fields'] == [','.join(jira.HISTORY_FIELDS.values())]
	assert asked['maxResults'] == [str(jira.MAX_RESULTS)]


def test_jira_errors_say_what_happened(jira_site, monkeypatch):
	with pytest.raises(jira.JiraError, match='no such ticket'):
		jira.get_issue('DSS-404')
	with pytest.raises(jira.JiraError, match='JQL query is empty'):
		jira.search('  ')
	monkeypatch.delenv('JIRA_TOKEN')
	with pytest.raises(jira.JiraError, match='set JIRA_TOKEN'):
		jira.get_issue('DSS-1234')


def test_jira_falls_back_to_the_confluence_login(jira_site, monkeypatch):
	for name, fallback in jira.ENV.items():
		monkeypatch.delenv(name)
		if name == 'JIRA_URL':
			monkeypatch.setenv(fallback, 'https://dv.atlassian.net/wiki')
		else:
			monkeypatch.setenv(fallback, 'x')
	assert jira.get_issue('DSS-1234')['summary'] == 'Plot is wrong'
	assert jira_site[-1].full_url.startswith('https://dv.atlassian.net/rest/api/2/issue/')


def test_jira_tools_answer_through_mcp(jira_site, mcp_call):
	failed, ticket = mcp_call('jira_get_issue', {'key': 'DSS-1234'})
	assert not failed and 'Plot is wrong' in ticket
	failed, found = mcp_call('jira_search', {'jql': 'project = DSS', 'limit': 5})
	assert not failed and 'DSS-7' in found
	asked = urllib.parse.parse_qs(urllib.parse.urlsplit(jira_site[-1].full_url).query)
	assert asked['maxResults'] == ['5']
	failed, text = mcp_call('jira_get_issue', {'key': 'DSS-404'})
	assert failed and 'no such ticket' in text


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

	sheets: ClassVar[dict] = {'casing_record': {}}

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


class FakeWorksheet:
	def __init__(self, title, rows):
		self.title, self.rows, self.reset = title, rows, False

	def reset_dimensions(self):
		self.reset = True

	def iter_rows(self, min_row, values_only):
		assert min_row == 1 and values_only
		return iter(self.rows)


class FakeWorkbook:
	"""openpyxl's read-only workbook, faked: its worksheets, and whether it was closed."""

	def __init__(self, worksheets):
		self.worksheets, self.closed = worksheets, False

	def __getitem__(self, name):
		for worksheet in self.worksheets:
			if worksheet.title == name:
				return worksheet
		raise KeyError(name)

	def close(self):
		self.closed = True


STATS = [  # a made-up statistics sheet as openpyxl yields it: row 1 first, gaps padded
	('Stage', 'Design', 'Perforation', 'Volume (bbl)', None),
	(1, '7x4', '1.1.1', None),
	(1, '7x4', None, 0),
	(),
	(2, '6x3', '2.1.1', 12.5),
	(2, '6X3', '2.1.2', 3.0),
	(datetime(2026, 10, 8), 'note\twith a tab\nand a line break', 'x' * 300),
	(None, None),
]


@pytest.fixture
def openpyxl(monkeypatch):
	"""openpyxl in the Cerberus venv, faked; `cerb.run_in` calls the function in this process."""
	books = []

	def load_workbook(path, read_only, data_only):
		assert read_only and data_only  # fast, and formulas as Excel saved them
		books.append(FakeWorkbook([FakeWorksheet('Well Info', [('Well', 'Test 1')]), stats]))
		return books[-1]

	stats = FakeWorksheet('Perforation Statistics', STATS)
	monkeypatch.setitem(sys.modules, 'openpyxl', SimpleNamespace(load_workbook=load_workbook))

	def run_in(app, module, func, **kwargs):
		assert (app, module) == ('cerb', 'dss.cerb')
		return json.loads(json.dumps(getattr(cerb, func)(**kwargs)))  # as the venv sends it

	monkeypatch.setattr(cerb, 'run_in', run_in)
	return books


def test_workbook_rows_keep_excel_numbers(openpyxl):
	assert cerb.workbook('stats.xlsx') == {
		'sheets': [
			{'name': 'Well Info', 'rows': 1, 'columns': 2},
			{'name': 'Perforation Statistics', 'rows': 7, 'columns': 4},
		]
	}
	cells = cerb.workbook('stats.xlsx', 'Perforation Statistics')['cells']
	assert cells[0] == ['Stage', 'Design', 'Perforation', 'Volume (bbl)']  # trailing empty cut
	assert cells[3] == [] and len(cells) == 7  # an empty row keeps its place; trailing ones go
	assert cells[6][0] == '2026-10-08T00:00:00'
	assert all(book.closed for book in openpyxl)
	assert openpyxl[-1]['Perforation Statistics'].reset  # a stale size record can't cut rows
	with pytest.raises(ValueError, match="no sheet 'Stats'; the sheets are: Well Info, Perfor"):
		cerb.workbook('stats.xlsx', 'Stats')


def stats_cells(rows: int = 7) -> list[list]:
	"""STATS as :func:`cerb.workbook` sends it, cut to its first *rows* rows."""
	return cerb._sheet_rows(FakeWorksheet('Perforation Statistics', STATS))[:rows]


def numbers(page: list[str]) -> list[str]:
	"""The row numbers of a page's rows, after its summary line."""
	found = []
	for line in page[1:]:
		found.append(line.split('\t', 1)[0])
	return found


def test_a_sheet_page_shows_rows_by_their_excel_numbers():
	page = cerb.sheet_page(stats_cells()).splitlines()
	assert page[0] == '7 rows, 4 columns. Rows 1-7 below. End of sheet.'
	assert page[1] == '1\tStage\tDesign\tPerforation\tVolume (bbl)'
	assert page[2] == '2\t1\t7x4\t1.1.1'
	assert page[3] == '3\t1\t7x4\t\t0'  # an empty cell keeps its column
	assert page[4] == '4' and page[6] == '6\t2\t6X3\t2.1.2\t3'  # a whole float loses its .0
	date, note, long_text = page[7].split('\t')[1:]
	assert date == '2026-10-08T00:00:00' and note == 'note with a tab and a line break'
	assert long_text == 'x' * cerb.CELL_CHARS + '…'


def test_a_sheet_filter_keeps_matching_rows_and_the_column_names():
	cells = stats_cells(6)
	page = cerb.sheet_page(cells, contains='6x3').splitlines()
	assert page[0] == (
		'6 rows, 4 columns. 2 rows from row 1 on contain "6x3"; 2 below, '
		"with the sheet's top rows for the column names. End of sheet."
	)
	assert numbers(page) == ['1', '2', '3', '5', '6']
	page = cerb.sheet_page(cells, contains='7x4').splitlines()
	assert numbers(page) == ['1', '2', '3']  # matches among the top rows show once
	assert '2 rows from row 1 on contain "7x4"' in page[0]


def test_long_sheets_come_in_pages(monkeypatch):
	cells = stats_cells(6)
	monkeypatch.setattr(cerb, 'SHEET_ROWS', 2)
	page = cerb.sheet_page(cells).splitlines()
	assert page[0] == '6 rows, 4 columns. Rows 1-2 below. More from first_row=3.'
	assert numbers(page) == ['1', '2']
	page = cerb.sheet_page(cells, first_row=3).splitlines()
	assert page[0].endswith(
		"Rows 3-4 below, with the sheet's top rows for the column names. More from first_row=5."
	)
	assert numbers(page) == ['1', '2', '3', '4']
	filtered = cerb.sheet_page(cells, contains='x', first_row=2).splitlines()[0]
	assert '4 rows from row 2 on contain "x"; 2 below' in filtered
	assert filtered.endswith('More from first_row=5.')
	monkeypatch.setattr(cerb, 'SHEET_CHARS', 5)  # a row longer than a page still shows
	assert cerb.sheet_page(cells).splitlines()[0].endswith('Rows 1-1 below. More from first_row=2.')
	assert (
		cerb.sheet_page(cells, first_row=50)
		.splitlines()[0]
		.startswith('6 rows, 4 columns. No rows from row 50 on')
	)


def test_workbooks_on_the_share_are_read_through_the_index(
	index, openpyxl, mcp_call, tmp_path, monkeypatch
):
	monkeypatch.setattr(tempfile, 'tempdir', str(tmp_path))
	stats = PROJECT + r'\Deliverables\DVT1234 Perforation Statistics.xlsx'
	index[stats] = b'PK\x03\x04 made-up workbook'
	assert cerb.read_sheet(stats) == (
		'DVT1234 Perforation Statistics.xlsx: 2 sheets: "Well Info" 1 rows x 2 columns; '
		'"Perforation Statistics" 7 rows x 4 columns.'
	)
	page = cerb.read_sheet(stats, 'Perforation Statistics', contains='7X4')
	assert page.startswith('DVT1234 Perforation Statistics.xlsx [Perforation Statistics]: 7 rows')
	failed, text = mcp_call(
		'cerb_read_sheet', {'path': stats, 'sheet': 'Perforation Statistics', 'first_row': 5}
	)
	assert not failed and '\n5\t2\t6x3\t2.1.1\t12.5\n' in text
	for path in (PROJECT + r'\notes.csv', PROJECT + r'\old.xls'):
		with pytest.raises(ValueError, match=r'is not an \.xlsx or \.xlsm workbook'):
			cerb.read_sheet(path)
	with pytest.raises(ValueError, match='outside the project share'):
		cerb.read_sheet(r'C:\Users\x\stats.xlsx')
	index[PROJECT + r'\~$DVT1234_client_info.xlsm'] = b'not a zip'
	with pytest.raises(ValueError, match='the lock file Excel keeps'):
		cerb.read_sheet(PROJECT + r'\~$DVT1234_client_info.xlsm')


PAGES = ['Cover\nACME Well-1', '', 'Eccentricity (in)\n0.0 0.1 0.2 0.2', 'Ovality (%)', 'End']


def test_document_pages_are_shown_with_where_to_go_next(monkeypatch):
	page = cerb.document_page(PAGES, 'page')
	assert page.startswith('5 pages.\n--- page 1\nCover\nACME Well-1\n--- page 2\n(no text on')
	assert 'likely an image, which this tool cannot show' in page
	assert page.endswith('--- page 5\nEnd\nEnd of document.')
	found = cerb.document_page(PAGES, 'page', contains='ECCENTRICITY')
	assert found == (
		'5 pages, 1 from page 1 on contain "ECCENTRICITY".\n'
		'--- page 3\nEccentricity (in)\n0.0 0.1 0.2 0.2\nEnd of document.'
	)
	monkeypatch.setattr(cerb, 'DOC_PAGES', 2)
	first = cerb.document_page(PAGES, 'page')
	assert '--- page 2' in first and '--- page 3' not in first
	assert first.endswith('More from first=3.')
	assert cerb.document_page(PAGES, 'page', first=4).endswith('--- page 5\nEnd\nEnd of document.')
	assert cerb.document_page(['a b'], 'part').startswith('1 parts.\n--- part 1\na b')


def test_documents_on_the_share_are_read_through_the_index(index, monkeypatch, tmp_path):
	monkeypatch.setattr(tempfile, 'tempdir', str(tmp_path))
	report = PROJECT + r'\Deliverables\DVT1234 Report.pdf'
	index[report] = b'%PDF-1.7 made-up'
	reads = []

	def run_in(app, module, func, **kwargs):
		reads.append((app, module, func))
		return {'unit': 'page', 'parts': PAGES}

	monkeypatch.setattr(cerb, 'run_in', run_in)
	assert cerb.read_document(report, contains='ovality') == (
		'DVT1234 Report.pdf: 5 pages, 1 from page 1 on contain "ovality".\n'
		'--- page 4\nOvality (%)\nEnd of document.'
	)
	assert reads == [('cerb', 'dss.cerb', 'document')]
	with pytest.raises(ValueError, match=r'is not a \.pdf or \.docx file'):
		cerb.read_document(PROJECT + r'\old.doc')
	with pytest.raises(ValueError, match='the lock file Word keeps'):
		cerb.read_document(PROJECT + r'\~$Report.docx')
	with pytest.raises(ValueError, match='outside the project share'):
		cerb.read_document(r'C:\Users\x\report.pdf')


def git(repo, *args) -> str:
	command = ['git', '-c', 'user.name=t', '-c', 'user.email=t@t', *args]
	return subprocess.run(command, cwd=repo, check=True, capture_output=True, text=True).stdout


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
	with pytest.raises(LookupError, match=r'no "release: v1\.0\.5" commit'):
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


def test_history_tools_find_a_fix_and_whether_a_build_has_it(released, mcp_call):
	fix = git(released, 'rev-parse', '--short', 'origin/master').strip()
	release = git(released, 'rev-parse', '--short', 'origin/master~1').strip()
	git(released, 'update-ref', 'refs/remotes/origin/DTN-1234-work-stop-rounding', fix)
	# the analyst's build: the release plus a version commit on a local branch only
	uat = git(
		released,
		'commit-tree',
		f'{release}^{{tree}}',
		'-p',
		release,
		'-m',
		'Set version 1.0.4+uat1',
	).strip()
	git(released, 'update-ref', 'refs/heads/uat', uat)

	released_log = code.log('cerberus')
	assert 'Add plots' in released_log and 'Stop rounding' not in released_log
	assert 'Stop rounding' in code.log('cerberus/plots.py', all_branches=True)
	later = (datetime.now() + timedelta(days=2)).strftime('%Y-%m-%d')
	assert 'nothing found' in code.log('cerberus', since=later, all_branches=True)
	with pytest.raises(ValueError, match='not a date'):
		code.log('cerberus', since='last week')

	branches = code.branches('dtn-1234')
	assert 'origin/DTN-1234-work-stop-rounding' in branches and 'Stop rounding' in branches
	assert 'nothing found' in code.branches('DTN-9999')

	assert 'does not include' in code.contains(fix, '1.0.4')
	assert 'does not include' in code.contains(fix, 'prod')
	answer = code.contains(release, '1.0.4+uat1')
	assert 'includes' in answer and 'release: v1.0.4' in answer
	assert code.contains(fix, '1.0.4+uat1').startswith(f'Cerberus 1.0.4+uat1 ({uat[:7]} ')
	# branch names work on both sides: is the fix branch in a release, is a commit on a branch
	assert 'does not include' in code.contains('origin/DTN-1234-work-stop-rounding', '1.0.4')
	assert 'includes' in code.contains(release, 'DTN-1234-work-stop-rounding')
	with pytest.raises(ValueError, match='not a commit hash or a remote branch'):
		code.contains('--all', '1.0.4')
	with pytest.raises(ValueError, match='not a commit hash or a remote branch'):
		code.contains('origin/no-such-branch', '1.0.4')
	with pytest.raises(ValueError, match='not a Cerberus version'):
		code.contains(fix, '1.0.4 --all')
	with pytest.raises(LookupError, match='no commit names the build'):
		code.contains(fix, '1.0.4+uat2')
	failed, text = mcp_call('code_contains', {'commit': fix, 'version': 'prod'})
	assert not failed and 'does not include' in text


def fake_wheel(folder, version):
	"""A made-up Cerberus wheel in the prod folder *folder*: the package, its metadata and a
	file outside both."""
	path = folder / f'cerberus-{version}-py3-none-any.whl'
	with zipfile.ZipFile(path, 'w') as archive:
		archive.writestr('cerberus/__init__.py', f'RELEASE = {version!r}\n')
		archive.writestr('cerberus/modules/__init__.py', '')
		meta = f'Metadata-Version: 2.1\nName: cerberus\nVersion: {version}\n'
		archive.writestr(f'cerberus-{version}.dist-info/METADATA', meta)
		archive.writestr('scripts/not_cerberus.py', '')
	return path


def test_the_prod_wheel_is_unpacked_once_per_version(tmp_path, monkeypatch):
	prod, code_dir = tmp_path / 'prod', tmp_path / 'prod_code'
	prod.mkdir()
	fake_wheel(prod, '1.0.4')
	monkeypatch.setenv('DV_CERB_PROD_DIR', str(prod))
	monkeypatch.delenv('DSS_CERB_PROD_CODE', raising=False)
	assert cerb.prod_code() == ''  # not set up: the venv's own Cerberus
	monkeypatch.setenv('DSS_CERB_PROD_CODE', str(code_dir))
	folder = Path(cerb.prod_code())
	assert folder == code_dir / '1.0.4'
	assert (folder / 'cerberus' / '__init__.py').read_text() == "RELEASE = '1.0.4'\n"
	assert (folder / 'cerberus-1.0.4.dist-info' / 'METADATA').exists()
	assert not (folder / 'scripts').exists()
	unpacked_at = (folder / 'cerberus' / '__init__.py').stat().st_mtime_ns
	assert cerb.prod_code() == str(folder)
	assert (folder / 'cerberus' / '__init__.py').stat().st_mtime_ns == unpacked_at  # not again
	fake_wheel(prod, '1.0.5')  # a new release goes next to the old one
	assert cerb.prod_code() == str(code_dir / '1.0.5')
	assert (code_dir / '1.0.4' / 'cerberus').exists()


def test_client_info_runs_the_prod_cerberus_not_the_venvs(tmp_path, monkeypatch):
	prod = tmp_path / 'prod'
	prod.mkdir()
	fake_wheel(prod, '1.0.4')
	monkeypatch.setenv('DV_CERB_PROD_DIR', str(prod))
	monkeypatch.setenv('DSS_CERB_PROD_CODE', str(tmp_path / 'prod_code'))
	monkeypatch.setenv('DSS_CERB_PY', sys.executable)
	checkout = tmp_path / 'checkout'  # the developer's checkout, with its own Cerberus
	(checkout / 'cerberus').mkdir(parents=True)
	(checkout / 'cerberus' / '__init__.py').write_text("RELEASE = 'dev'\n", encoding='utf-8')
	monkeypatch.setenv('DSS_CERB_CWD', str(checkout))
	found = cerb.run_in('cerb', 'dss.cerb', 'cerberus_found', first_path=cerb.prod_code())
	assert found['version'] == '1.0.4'
	assert found['file'] == str(tmp_path / 'prod_code' / '1.0.4' / 'cerberus' / '__init__.py')


@pytest.fixture
def bot_clone(released, tmp_path, monkeypatch):
	"""The bot's own clone (DSS_CERB_GIT) of *released*, standing in for GitHub."""
	git(released, 'update-ref', 'refs/heads/master', 'origin/master')  # GitHub's master
	monkeypatch.setattr(code, 'REPO_URL', str(released))
	clone = tmp_path / 'bot' / 'DataDHPython'
	monkeypatch.setenv('DSS_CERB_GIT', str(clone))
	monkeypatch.delenv('DSS_CERB_PROD_CODE', raising=False)
	assert code.clone() is True
	return clone


def test_the_bots_clone_is_read_and_fetched_before_each_triage(released, bot_clone):
	assert code.clone() is False  # there already
	assert code.repo_dir() == str(bot_clone)
	assert 'y = data.round(2)' in code.read('cerberus/plots.py')  # the release, from the clone
	assert 'Stop rounding' in code.log('cerberus', all_branches=True)
	git(released, 'branch', 'DTN-9-work-new-fix', 'origin/master')  # pushed to GitHub
	assert 'nothing found' in code.branches('DTN-9')
	assert code.refresh() == []
	assert 'origin/DTN-9-work-new-fix' in code.branches('DTN-9')
	git(bot_clone, 'remote', 'set-url', 'origin', str(released.parent / 'gone'))
	(note,) = code.refresh()  # GitHub out of reach: the last fetch still serves
	assert note.startswith('Cerberus clone not updated, so the newest commits may be missing')
	assert 'origin/DTN-9-work-new-fix' in code.branches('DTN-9')


def test_a_developer_checkout_is_never_fetched(released, monkeypatch):
	monkeypatch.delenv('DSS_CERB_GIT', raising=False)
	assert code.refresh() == []  # no clone of its own: nothing to fetch
	with pytest.raises(RuntimeError, match="only the bot's own clone is fetched"):
		code.fetch()


def test_setup_says_what_each_tool_reads(released, bot_clone, tmp_path, monkeypatch):
	(tmp_path / 'cerberus-1.0.4-py3-none-any.whl').unlink()
	fake_wheel(tmp_path, '1.0.4')  # the prod folder of the released fixture
	monkeypatch.setenv('DSS_CERB_PROD_CODE', str(tmp_path / 'prod_code'))
	monkeypatch.setenv('DSS_CERB_PY', sys.executable)
	lines = code.setup()
	assert lines[0].startswith(f'code tools: {bot_clone}, prod 1.0.4 = ')
	assert 'release: v1.0.4' in lines[0]
	assert lines[1].startswith('last fetch: ')
	assert lines[2] == (
		'cerb_client_info runs Cerberus 1.0.4 from '
		+ str(tmp_path / 'prod_code' / '1.0.4' / 'cerberus' / '__init__.py')
	)
	assert len(lines) == 3  # no warning


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
