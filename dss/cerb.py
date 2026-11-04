"""`cerb_*` tools: what Cerberus reads and ran for a job.

`client_info` and `workbook` run in the DataDHPython venv. The project tools read DVT project
folders through the project index (the Everything HTTP server over the project share), approved
for read-only use on DSS tickets: they send only GET requests, and only for paths inside the
indexed share. The login comes from DV_PROJECT_INDEX_USER and DV_PROJECT_INDEX_PASSWORD.
`prod_version` reads DV_CERB_PROD_DIR (the folder analysts install Cerberus from). With
DSS_CERB_PROD_CODE, `client_info` runs the Cerberus of that prod wheel (:func:`prod_code`), not
the developer checkout the venv is installed from. Settings are read at each call, so a `.env`
file loaded after this module is imported still applies.
"""

import base64
import importlib.metadata
import json
import ntpath
import os
import re
import shutil
import tempfile
import urllib.error
import urllib.parse
import urllib.request
import zipfile
from pathlib import Path

from dss.worker import run_in

DEFAULT_INDEX_URL = 'http://192.168.25.120'
# The index names the share by its address, so its paths start with this, not with hades-vfs.
DEFAULT_INDEX_ROOT = r'\\192.168.24.252\projects'
DEFAULT_PROD_DIR = 'F:/DarkVision Dropbox/Analysis/CERBERUS/prod'
INDEX_ENV = ('DV_PROJECT_INDEX_USER', 'DV_PROJECT_INDEX_PASSWORD')
TIMEOUT_S = 60
MAX_FETCH_BYTES = 50 * 2**20  # one file is held in memory
MAX_LISTED = 500
# All of a project's telemetry logs are listed, then filtered by login here. Add login terms to
# the search if a project's telemetry folder grows past this.
MAX_TELEMETRY_LOGS = 5000
MAX_TEXT_LINES = 400
MAX_FAILED_RUNS = 10
WORKBOOKS = ('.xlsx', '.xlsm')  # what openpyxl opens; not the old binary .xls
SHEET_ROWS = 100  # rows per page of a sheet
SHEET_CHARS = 30_000  # per page too: a statistics row runs to about 400 characters
CELL_CHARS = 200
TOP_ROWS = 3  # shown before a later page or the rows a filter found, for the column names
WHEEL = re.compile(r'cerberus-(\d+(?:\.\d+)+)-py3-none-any\.whl')
# <login>_telemetry_<local start time>_<pid>.log, see cerberus/modules/telemetry/config.py
TELEMETRY_LOG = re.compile(r'(.+)_telemetry_(\d{4}-\d\d-\d\d_\d\d-\d\d-\d\d)_\d+\.log')
START_TIME = re.compile(r'\d{4}-\d\d-\d\d_\d\d-\d\d-\d\d')
LOGFMT_PAIR = re.compile(r'(\w+)=("(?:[^"\\]|\\.)*"|\S*)')


def client_info(path: str) -> dict:
	"""Well info, casing strings and joints of the client info at *path*, as Cerberus reads them.

	Runs in the Cerberus venv, through `cerb_client_info`.
	"""
	from cerberus.modules.common.clientinfo import DHClientInfo

	info = DHClientInfo(ci_path=path).initialize_client_info()
	found = {'version': info.version, 'wellinfo': info.get_wellinfo()}
	if 'casing_record' in info.sheetmaps:  # older client info versions have no Casing Record
		record, depth_unit, dia_unit, pressure_unit, strings = info.get_casingrecord()
		found['units'] = {'depth': depth_unit, 'diameter': dia_unit, 'pressure': pressure_unit}
		found['strings'] = strings.to_dict('records')
		found['joints'] = record.to_dict('records')
	return found


def cerberus_found() -> dict:
	"""Where this python's Cerberus comes from, and its version. Runs in the Cerberus venv."""
	import cerberus

	return {'file': cerberus.__file__, 'version': importlib.metadata.version('cerberus')}


def workbook(path: str, sheet: str = '') -> dict:
	"""The worksheets of the workbook at *path* with their sizes, or every row of *sheet*.

	Runs in the Cerberus venv (openpyxl), through `cerb_read_sheet`. Formulas give the value Excel
	saved. `cells[0]` is row 1 and empty rows are kept, so rows keep Excel's numbers; trailing
	empty cells and rows are dropped.
	"""
	from openpyxl import load_workbook

	book = load_workbook(path, read_only=True, data_only=True)
	try:
		names = []
		for worksheet in book.worksheets:  # chart sheets have no cells
			names.append(worksheet.title)
		if not sheet:
			sizes = []
			for worksheet in book.worksheets:
				rows = _sheet_rows(worksheet)
				widest = 0
				for row in rows:
					widest = max(widest, len(row))
				sizes.append({'name': worksheet.title, 'rows': len(rows), 'columns': widest})
			return {'sheets': sizes}
		if sheet not in names:
			raise ValueError(f'no sheet {sheet!r}; the sheets are: {", ".join(names)}')
		return {'cells': _sheet_rows(book[sheet])}
	finally:
		book.close()


def _sheet_rows(worksheet) -> list[list]:
	"""Every row of *worksheet* from row 1, as JSON values."""
	worksheet.reset_dimensions()  # a stale size record would cut rows off
	rows = []
	for values in worksheet.iter_rows(min_row=1, values_only=True):
		row = []
		for value in values:
			row.append(_json_cell(value))
		while row and row[-1] in (None, ''):
			row.pop()
		rows.append(row)
	while rows and not rows[-1]:
		rows.pop()
	return rows


def _json_cell(value):
	"""A cell's value as JSON: dates and times as ISO text, anything else unusual as text."""
	if value is None or isinstance(value, bool | int | float | str):
		return value
	if hasattr(value, 'isoformat'):
		return value.isoformat()
	return str(value)


def find_project(dvt: int) -> dict:
	"""Project folders of DVT *dvt* on the share: <client>\\DVT<dvt>_<date>_<type>_<well>.

	The index can lag behind a folder made minutes ago, so no match means not indexed yet.
	"""
	root = _index_root()
	prefix = f'DVT{int(dvt)}_'
	found = []
	for hit in _search(f'folder: {prefix} path:"{root}\\"'):
		in_client_folder = _key(ntpath.dirname(hit['path'])) == _key(root)
		if hit['type'] == 'folder' and hit['name'].startswith(prefix) and in_client_folder:
			found.append(ntpath.join(hit['path'], hit['name']))
	return {'projects': sorted(found)}


def last_session(project: str, user: str, before: str = '') -> dict:
	"""Facts from *user*'s newest Cerberus telemetry log in *project* that started before *before*.

	Logs are named after the Windows login (SurnameGiven), so a log matches when the first and
	last parts of *user* both appear in its login. *before* is the ticket time as
	`YYYY-MM-DD_HH-MM-SS` (local); empty means the newest log.
	"""
	if before and not START_TIME.fullmatch(before):
		raise ValueError(f'before must look like 2026-10-20_12-17-30, not {before!r}')
	folder = ntpath.join(_inside_index(project), 'cerberus', 'telemetry')
	names = re.findall(r'[a-z]+', user.lower())
	if not names:
		raise ValueError(f'{user!r} has no name to match')
	logs = []
	for hit in _search(f'file: _telemetry_ path:"{folder}\\"', MAX_TELEMETRY_LOGS):
		match = TELEMETRY_LOG.fullmatch(hit['name'])
		if hit['type'] != 'file' or not match or _key(hit['path']) != _key(folder):
			continue
		login = match[1].lower()
		started = match[2]
		if names[0] not in login or names[-1] not in login:
			continue
		if before and started >= before:
			continue
		logs.append((started, hit['name']))
	if not logs:
		return {'found': False, 'folder': folder, 'user': user, 'before': before}
	started, name = max(logs)
	text = _fetch(ntpath.join(folder, name)).decode('utf-8', errors='replace')
	return {'found': True, 'file': name, 'started': started, **session_facts(text)}


def session_facts(text: str) -> dict:
	"""Cerberus version, failed script runs and the last saved config of one telemetry log."""
	facts: dict = {'cerb': '', 'failed_runs': [], 'last_config': ''}
	for line in text.splitlines():
		record = {key: value.strip('"') for key, value in LOGFMT_PAIR.findall(line)}
		event = record.get('event')
		if event == 'session' and record.get('cerb'):
			facts['cerb'] = record['cerb']
		elif event == 'config_saved':
			facts['last_config'] = record.get('config', '')
		elif (
			event == 'script_run'
			and record.get('phase') == 'stop'
			and record.get('status', 'ok') != 'ok'
		):
			run = {key: record.get(key, '') for key in ('name', 'status', 'result_detail')}
			facts['failed_runs'].append(run)
	facts['failed_runs'] = facts['failed_runs'][-MAX_FAILED_RUNS:]
	return facts


def list_folder(folder: str) -> dict:
	"""Files and folders directly inside *folder*, folders first, with file sizes in bytes."""
	folder = _inside_index(folder)
	entries = []
	for hit in _search(f'parent:"{folder}"', MAX_LISTED + 1):
		if _key(hit['path']) == _key(folder):
			entries.append({'name': hit['name'], 'type': hit['type'], 'size': hit.get('size', '')})
	entries.sort(key=_folders_first)
	return {'folder': folder, 'entries': entries[:MAX_LISTED], 'cut': len(entries) > MAX_LISTED}


def read_text(path: str, first_line: int = 1) -> dict:
	"""Up to MAX_TEXT_LINES lines of a text file on the share, from *first_line* on."""
	data = _fetch(path)
	if b'\0' in data[:8192]:
		raise ValueError(f'{path} is not a text file')
	lines = data.decode('utf-8', errors='replace').splitlines()
	start = max(first_line, 1) - 1
	return {
		'path': path,
		'first_line': start + 1,
		'total_lines': len(lines),
		'text': '\n'.join(lines[start : start + MAX_TEXT_LINES]),
	}


def read_sheet(path: str, sheet: str = '', first_row: int = 1, contains: str = '') -> str:
	"""One page of *sheet* of a workbook on the share; without *sheet*, its sheets and sizes.

	The workbook is copied through the index and read in the Cerberus venv (:func:`workbook`).
	See :func:`sheet_page` for the page.
	"""
	if not path.lower().endswith(WORKBOOKS):
		raise ValueError(f'{path} is not an .xlsx or .xlsm workbook; cerb_read_text reads text')
	if ntpath.basename(path).startswith('~$'):
		raise ValueError(
			f'{path} is the lock file Excel keeps while the workbook is open; read the workbook'
		)
	local = str(local_copy(path))
	name = ntpath.basename(path)
	if not sheet:
		sheets = run_in('cerb', __name__, 'workbook', path=local)['sheets']
		parts = []
		for found in sheets:
			parts.append(f'"{found["name"]}" {found["rows"]:,} rows x {found["columns"]} columns')
		return f'{name}: {len(sheets)} sheets: {"; ".join(parts)}.'
	cells = run_in('cerb', __name__, 'workbook', path=local, sheet=sheet)['cells']
	return f'{name} [{sheet}]: {sheet_page(cells, first_row, contains)}'


def sheet_page(cells: list[list], first_row: int = 1, contains: str = '') -> str:
	"""A line saying what the page holds, then its rows: Excel's row number and the cells,
	tab-separated, one row per line.

	A page is up to SHEET_ROWS rows and SHEET_CHARS characters from *first_row* on. With
	*contains*, only rows with a cell holding that text (any case) count. A filtered page, or one
	that starts later, also shows the top TOP_ROWS rows, for the column names.
	"""
	needle = contains.lower()
	start = max(first_row, 1)
	lines: dict[int, str] = {}  # row number -> line
	size = 0
	matched = 0
	last = 0
	next_row = None
	for number in range(start, len(cells) + 1):
		texts = _cell_texts(cells[number - 1])
		if needle and not any(needle in text.lower() for text in texts):
			continue
		matched += 1
		if next_row is not None:
			continue  # only counting the matches from here on
		line = _row_line(number, texts)
		if len(lines) == SHEET_ROWS or (lines and size + len(line) > SHEET_CHARS):
			next_row = number
			if not needle:
				break
			continue
		lines[number] = line
		size += len(line) + 1
		last = number
	shown = len(lines)
	widest = 0
	for row in cells:
		widest = max(widest, len(row))
	summary = f'{len(cells):,} rows, {widest} columns.'
	if needle:
		summary += f' {matched:,} rows from row {start} on contain "{contains}"; {shown} below'
	elif lines:
		summary += f' Rows {start}-{last} below'
	else:
		summary += f' No rows from row {start} on'
	top = min(TOP_ROWS, len(cells))
	missing = any(number not in lines for number in range(1, top + 1))
	if (needle or start > 1) and missing:
		summary += ", with the sheet's top rows for the column names"
		for number in range(1, top + 1):
			lines.setdefault(number, _row_line(number, _cell_texts(cells[number - 1])))
	if next_row is None:
		summary += '. End of sheet.'
	else:
		summary += f'. More from first_row={next_row}.'
	return '\n'.join([summary, *(lines[number] for number in sorted(lines))])


def _cell_texts(row: list) -> list[str]:
	"""*row*'s cells as one-line text: whole floats without .0, long text cut at CELL_CHARS."""
	texts = []
	for value in row:
		text = ''
		if isinstance(value, float) and value.is_integer():
			text = str(int(value))
		elif value is not None:
			text = ' '.join(str(value).split())  # tabs and line breaks would split the row
		if len(text) > CELL_CHARS:
			text = text[:CELL_CHARS] + '…'
		texts.append(text)
	return texts


def _row_line(number: int, texts: list[str]) -> str:
	return '\t'.join([str(number), *texts])


def local_copy(path: str) -> Path:
	"""Download *path* from the share into the temp folder, for tools that need a file on disk."""
	inside = _inside_index(path)
	relative = inside[len(_index_root()) + 1 :]
	target = Path(tempfile.gettempdir(), 'dv_project_index', relative)
	target.parent.mkdir(parents=True, exist_ok=True)
	target.write_bytes(_fetch(inside))
	return target


def prod_version() -> str:
	"""Version of the Cerberus wheel analysts install from the prod folder (the highest one)."""
	prod_dir = _prod_dir()
	found = []
	for path in prod_dir.glob('cerberus-*.whl'):
		match = WHEEL.fullmatch(path.name)
		if match:
			found.append(match[1])
	if not found:
		raise FileNotFoundError(f'no Cerberus wheel in {prod_dir}')
	return max(found, key=_version_key)


def prod_code() -> str:
	"""The folder holding the prod wheel's Cerberus, unpacked on first use; empty when
	DSS_CERB_PROD_CODE is not set.

	Code run in the Cerberus venv imports Cerberus from here first (:func:`dss.worker.run_in`), so
	it is the Cerberus analysts installed. The venv's own packages still serve its dependencies.
	Each version gets its own folder, so a new prod wheel is unpacked once, next to the old one.
	"""
	root = os.environ.get('DSS_CERB_PROD_CODE', '')
	if not root:
		return ''
	version = prod_version()
	folder = Path(root, version)
	if not (folder / 'cerberus' / '__init__.py').exists():
		_unpack(_prod_dir() / f'cerberus-{version}-py3-none-any.whl', version, folder)
	return str(folder)


def _unpack(wheel: Path, version: str, folder: Path) -> None:
	"""The Cerberus package and its metadata from *wheel*, into *folder*, which appears whole."""
	partial = folder.with_name(folder.name + '.partial')
	shutil.rmtree(partial, ignore_errors=True)
	with zipfile.ZipFile(wheel) as archive:
		names = []
		for name in archive.namelist():
			if name.startswith(('cerberus/', f'cerberus-{version}.dist-info/')):
				names.append(name)
		archive.extractall(partial, names)
	shutil.rmtree(folder, ignore_errors=True)  # a folder left without its package
	partial.rename(folder)


def _index_url() -> str:
	return _setting('DV_PROJECT_INDEX_URL', DEFAULT_INDEX_URL).rstrip('/')


def _index_root() -> str:
	return _setting('DV_PROJECT_INDEX_ROOT', DEFAULT_INDEX_ROOT).rstrip('\\')


def _prod_dir() -> Path:
	return Path(_setting('DV_CERB_PROD_DIR', DEFAULT_PROD_DIR))


def _setting(name: str, default: str) -> str:
	"""The environment variable *name*, or *default* when it is unset or empty."""
	value = os.environ.get(name, '')
	if not value:
		return default
	return value


def _version_key(version: str) -> tuple[int, ...]:
	return tuple(int(part) for part in version.split('.'))


def _folders_first(entry: dict) -> tuple[bool, str]:
	return entry['type'] != 'folder', entry['name'].lower()


def _key(path: str) -> str:
	return ntpath.normcase(ntpath.normpath(path)).rstrip('\\')


def _inside_index(path: str) -> str:
	# normpath stops `..` at the share root, so a prefix check suffices
	normal = ntpath.normpath(path)
	root = _index_root()
	if not _key(normal).startswith(_key(root) + '\\'):
		raise ValueError(f'{path} is outside the project share {root}')
	return normal


def _search(query: str, count: int = MAX_LISTED) -> list[dict]:
	params = {'search': query, 'json': 1, 'path_column': 1, 'size_column': 1, 'count': count}
	return json.loads(_get('/?' + urllib.parse.urlencode(params))).get('results', [])


def _fetch(path: str) -> bytes:
	parts = _inside_index(path).lstrip('\\').split('\\')
	return _get('/%5C%5C' + '/'.join(urllib.parse.quote(part, safe='') for part in parts))


def _get(url_path: str) -> bytes:
	"""GET from the project index, the only request this module sends."""
	missing = []
	for name in INDEX_ENV:
		if not os.environ.get(name):
			missing.append(name)
	if missing:
		raise RuntimeError(f'the project index is not set up here: set {", ".join(missing)}')
	url = _index_url()
	login = f'{os.environ[INDEX_ENV[0]]}:{os.environ[INDEX_ENV[1]]}'
	request = urllib.request.Request(
		url + url_path,
		headers={'Authorization': 'Basic ' + base64.b64encode(login.encode()).decode()},
	)
	try:
		with urllib.request.urlopen(request, timeout=TIMEOUT_S) as response:
			data = response.read(MAX_FETCH_BYTES + 1)
	except urllib.error.HTTPError as error:
		reason = {
			401: 'the project index refused the login',
			404: 'no such file in the project index',
		}.get(error.code, f'the project index answered {error.code} {error.reason}')
		raise RuntimeError(reason) from error
	except (urllib.error.URLError, TimeoutError) as error:
		raise RuntimeError(_no_answer(url, error)) from error
	if len(data) > MAX_FETCH_BYTES:
		raise RuntimeError(f'the file is over {MAX_FETCH_BYTES // 2**20} MB, too big to read here')
	return data


def _no_answer(url: str, error: Exception) -> str:
	"""Why a request to the index ended without an HTTP answer: a timeout or no connection."""
	reason = getattr(error, 'reason', error)
	if isinstance(reason, TimeoutError):
		return f'the project index did not answer within {TIMEOUT_S} s'
	return f'cannot reach the project index at {url}: {reason}'


def register(server) -> None:
	@server.tool()
	def cerb_client_info(path: str) -> dict:
		"""Well info, casing strings and joints of a `client_info.xlsm`, as Cerberus reads them (the
		prod wheel's Cerberus, when the bot has it). *path* is a local file or one on the share."""
		if path.startswith('\\\\'):
			local = local_copy(path)
		else:
			local = Path(path).resolve()
		return run_in('cerb', __name__, 'client_info', first_path=prod_code(), path=str(local))

	@server.tool()
	def cerb_find_project(dvt: int) -> dict:
		"""Project folders of DVT *dvt* on the project share, from the project index."""
		return find_project(dvt)

	@server.tool()
	def cerb_list(folder: str) -> dict:
		"""Files and folders directly inside a folder on the project share, with sizes."""
		return list_folder(folder)

	@server.tool()
	def cerb_read_text(path: str, first_line: int = 1) -> dict:
		"""Up to 400 lines of a text file on the project share (config, log, csv) from
		*first_line* on, with the file's total line count."""
		return read_text(path, first_line)

	@server.tool()
	def cerb_read_sheet(path: str, sheet: str = '', first_row: int = 1, contains: str = '') -> str:
		"""An .xlsx or .xlsm workbook on the project share, such as a statistics spreadsheet or
		the client info. Without *sheet*: its sheets and their sizes. With *sheet*: up to 100 rows
		from *first_row* (Excel row numbers, cells tab-separated, formulas as last saved), the
		sheet's top 3 rows first for the column names, and where the next page starts.
		*contains* keeps only the rows with a cell containing that text."""
		return read_sheet(path, sheet, first_row, contains)

	@server.tool()
	def cerb_last_session(project: str, user: str, before: str = '') -> dict:
		"""The reporter's newest Cerberus session in *project* before the ticket time
		(`YYYY-MM-DD_HH-MM-SS`): Cerberus version, failed script runs, last saved config."""
		return last_session(project, user, before)

	@server.tool()
	def cerb_prod_version() -> str:
		"""Version of the Cerberus wheel in the prod folder, to compare with a session's."""
		return prod_version()
