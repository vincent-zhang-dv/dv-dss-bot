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
import contextlib
import importlib.metadata
import io
import json
import ntpath
import os
import re
import shutil
import struct
import tempfile
import time
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
MAX_FETCH_BYTES = 50 * 2**20  # a file read as text is held in memory
DEFAULT_MAX_COPY_MB = 1024  # a copy streams to disk (DV_PROJECT_INDEX_MAX_MB)
CHUNK_BYTES = 2**20
COPY_FRESH_S = 15 * 60  # a copy this new is used again: paging through a file downloads it once
COPY_KEEP_S = 24 * 3600  # older copies are deleted
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
DOCUMENTS = ('.pdf', '.docx')  # what pypdf and python-docx open; not the old binary .doc
DOC_PAGES = 20  # pages per answer
DOC_CHARS = 30_000  # per answer too
WORD_PART_CHARS = 3000  # a Word file has no pages, so its text is cut into parts about this long
IMAGES = ('.png', '.jpg', '.jpeg', '.gif', '.bmp', '.tif', '.tiff', '.webp')  # what Pillow opens
MAX_IMAGE_SIDE = 1568  # pixels on the long side; Claude scales larger images down anyway
MAX_IMAGE_BYTES = 3_500_000  # a bigger PNG is sent as JPEG
MIN_PICTURE_PIXELS = 200 * 200  # smaller pictures in a PDF are logos and icons
MAX_PAGE_PICTURES = 6  # each costs Claude about 1.5-3k tokens
TABLES = ('.parquet', '.db', '.sqlite', '.sqlite3')
CALIPERS = ('.dvcaliper', '.npy')  # what Cerberus's DHCaliper loads
PICKLES = ('.pkl', '.pickle')  # loading one runs code stored in it: never read
MAX_TABLE_ROWS = 100_000  # rows handed over from the Cerberus venv; the pages come from these
MAX_CALIPER_ROWS = 60  # slices listed for a depth window, evenly spread
MAX_PEAK_ROWS = 10  # and the slices with the highest values, which an even spread can miss
METRES_PER_FOOT = 0.3048
# A .dvcaliper is two int64s (elements, slices), then slices x (1 + elements) float32s, depth
# first in each slice. A depth window is read by HTTP ranges, so a 4 GB file is never copied.
CALIPER_HEADER = struct.Struct('<2q')
CALIPER_VALUE = struct.Struct('<f')
MAX_WINDOW_SLICES = 20_000
PARTIAL_CONTENT = 206
WHEEL = re.compile(r'cerberus-(\d+(?:\.\d+)+)-py3-none-any\.whl')
# <login>_telemetry_<local start time>_<pid>.log, see cerberus/modules/telemetry/config.py
TELEMETRY_LOG = re.compile(r'(.+)_telemetry_(\d{4}-\d\d-\d\d_\d\d-\d\d-\d\d)_\d+\.log')
START_TIME = re.compile(r'\d{4}-\d\d-\d\d_\d\d-\d\d-\d\d')
LOGFMT_PAIR = re.compile(r'(\w+)=("(?:[^"\\]|\\.)*"|\S*)')
# From Cerberus 1.1 on, <project>\cerberus\terminal_log holds loguru's DEBUG output of each
# session, named like its telemetry log (cerberus/modules/utility/terminal_log.py)
TERMINAL_LOG_SINCE = (1, 1)
# loguru's line: 2026-11-04 22:48:19.480 | ERROR    | cerberus.x.y:function:52 - message
LOG_RECORD = re.compile(r'(\d{4}-\d\d-\d\d \d\d:\d\d:\d\d)\.\d{3} \| (\w+)\s*\| (\S+) - (.*)')
LOG_VALUES = re.compile(r'\s*[│└]')  # the variable values loguru prints under a traceback line
MAX_LOG_ERRORS = 8  # distinct errors of a terminal log, the newest
MAX_LOG_WARNINGS = 10  # warning call sites, the first
MAX_ERROR_DETAIL = 6  # lines kept under an error: the end of a traceback, the deepest frames
LOG_LINE_CHARS = 300


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


def document(path: str) -> dict:
	"""The text of the PDF or Word file at *path*: one entry per PDF page, or per part of about
	WORD_PART_CHARS of a Word file (paragraphs and table rows in order).

	Runs in the Cerberus venv (pypdf, python-docx), through `cerb_read_document`.
	"""
	if path.lower().endswith('.pdf'):
		from pypdf import PdfReader

		pages = []
		for page in PdfReader(path).pages:
			pages.append(page.extract_text() or '')
		return {'unit': 'page', 'parts': pages}
	from docx import Document
	from docx.table import Table

	lines = []
	for block in Document(path).iter_inner_content():
		if isinstance(block, Table):
			for row in block.rows:
				cells = []
				for cell in row.cells:
					cells.append(cell.text.strip())
				lines.append(' | '.join(cells))
		elif block.text.strip():
			lines.append(block.text)
	parts = ['']
	for line in lines:
		if parts[-1] and len(parts[-1]) + len(line) > WORD_PART_CHARS:
			parts.append('')
		parts[-1] += line + '\n'
	return {'unit': 'part', 'parts': parts}


def pictures(path: str, page: int = 0) -> dict:
	"""The image at *path*, or the pictures in page *page* of the PDF at *path*, each scaled to
	at most MAX_IMAGE_SIDE pixels and base64-encoded. Pictures under MIN_PICTURE_PIXELS (logos)
	are left out, and at most MAX_PAGE_PICTURES are kept.

	Runs in the Cerberus venv (Pillow, pypdf), through `cerb_read_image`.
	"""
	from PIL import Image

	pages = 0
	found = []
	if path.lower().endswith('.pdf'):
		from pypdf import PdfReader

		reader = PdfReader(path)
		pages = len(reader.pages)
		if not 1 <= page <= pages:
			raise ValueError(f'the PDF has {pages} pages; page {page} is not one of them')
		for embedded in reader.pages[page - 1].images:
			width, height = embedded.image.size
			if width * height >= MIN_PICTURE_PIXELS:
				found.append(embedded.image)
	else:
		found.append(Image.open(path))
	encoded = []
	for picture in found[:MAX_PAGE_PICTURES]:
		encoded.append(_encoded(picture))
	return {'pages': pages, 'found': len(found), 'images': encoded}


def _encoded(picture) -> dict:
	"""A Pillow image as base64 PNG, or JPEG when the PNG is over MAX_IMAGE_BYTES, scaled down
	to MAX_IMAGE_SIDE on the long side."""
	original = picture.size
	picture.thumbnail((MAX_IMAGE_SIDE, MAX_IMAGE_SIDE))  # only shrinks, keeps the shape
	if picture.mode not in ('RGB', 'RGBA', 'L'):
		picture = picture.convert('RGB')
	buffer = io.BytesIO()
	picture.save(buffer, 'PNG')
	kind = 'png'
	if buffer.tell() > MAX_IMAGE_BYTES:
		buffer = io.BytesIO()
		picture.convert('RGB').save(buffer, 'JPEG', quality=85)
		kind = 'jpeg'
	data = base64.b64encode(buffer.getvalue()).decode()
	return {'format': kind, 'size': list(original), 'shown': list(picture.size), 'data': data}


def table(path: str, name: str = '') -> dict:
	"""The rows of the Parquet file at *path*, or of table *name* of the SQLite file at *path*,
	as cells: the column names, then up to MAX_TABLE_ROWS rows. A SQLite file without *name*
	gives its tables and their row counts.

	Runs in the Cerberus venv (pandas and pyarrow; sqlite3, read-only), through `cerb_read_table`.
	"""
	if path.lower().endswith('.parquet'):
		import pandas

		frame = pandas.read_parquet(path)
		cells = [[str(column) for column in frame.columns]]
		for values in frame.head(MAX_TABLE_ROWS).itertuples(index=False):
			cells.append(_json_row(values))
		return {'rows': len(frame), 'cells': cells}
	import sqlite3

	connection = sqlite3.connect(Path(path).as_uri() + '?mode=ro', uri=True)
	try:
		tables = []
		try:
			listed = connection.execute("SELECT name FROM sqlite_master WHERE type = 'table'")
		except sqlite3.DatabaseError as error:
			raise ValueError(f'{Path(path).name} is not a SQLite file: {error}') from error
		for (found,) in listed:
			tables.append(found)
		if not name:
			counts = []
			for found in sorted(tables):
				counts.append([found, _row_count(connection, found)])
			return {'tables': counts}
		if name not in tables:
			raise ValueError(f'no table {name!r}; the tables are: {", ".join(sorted(tables))}')
		quoted = name.replace('"', '""')  # a table name from the file itself, checked above
		cursor = connection.execute(f'SELECT * FROM "{quoted}"')
		cells = [[column[0] for column in cursor.description]]
		for values in cursor.fetchmany(MAX_TABLE_ROWS):
			cells.append(_json_row(values))
		return {'rows': _row_count(connection, name), 'cells': cells}
	finally:
		connection.close()


def _row_count(connection, name: str) -> int:
	quoted = name.replace('"', '""')  # a table name from the file itself
	return connection.execute(f'SELECT COUNT(*) FROM "{quoted}"').fetchone()[0]


def _json_row(values) -> list:
	row = []
	for value in values:
		row.append(_json_cell(value))
	return row


def caliper(path: str, depth_from: float = 0, depth_to: float = 0) -> dict:
	"""A caliper file (.dvcaliper, or a caliper .npy) as Cerberus reads it: its type, units, size
	and depth range. With *depth_to* above *depth_from* (metres, as Cerberus stores depths), also
	slices of that window, MAX_CALIPER_ROWS evenly spread and the MAX_PEAK_ROWS with the highest
	values, each as its depth and the min, mean and max over its elements, and a picture of the
	whole window.

	Runs in the Cerberus venv with the prod Cerberus first (:func:`prod_code`), through
	`cerb_read_caliper`.
	"""
	import warnings

	import numpy
	from cerberus.modules.integrity.caliper import DHCaliper

	found = DHCaliper(filepath=path)
	found.load_data(memmap=True)
	depths = numpy.asarray(found.depths, dtype=float)
	result = {
		'type': str(found.caliper_type),
		'units': found.units,
		'slices': found.slices,
		'elements': found.elems,
		'depths': [float(numpy.nanmin(depths)), float(numpy.nanmax(depths))],
	}
	if depth_to <= depth_from:
		return result
	inside = numpy.flatnonzero((depths >= depth_from) & (depths <= depth_to))
	result['window_slices'] = len(inside)
	if not len(inside):
		return result
	window = slice(int(inside[0]), int(inside[-1]) + 1)  # depths ascend, so the window is one run
	values = numpy.asarray(found.values[window], dtype=float)
	spread = numpy.linspace(0, len(values) - 1, min(len(values), MAX_CALIPER_ROWS)).round()
	picks = set(spread.astype(int).tolist())
	rows = []
	with warnings.catch_warnings():  # an all-empty slice gives NaN, which is the answer
		warnings.simplefilter('ignore', RuntimeWarning)
		peaks = numpy.nan_to_num(numpy.nanmax(values, axis=1), nan=-numpy.inf)
		picks.update(numpy.argsort(-peaks)[:MAX_PEAK_ROWS].tolist())
		for pick in sorted(picks):
			row = values[pick]
			nans = int(numpy.isnan(row).sum())
			low, mean, high = numpy.nanmin(row), numpy.nanmean(row), numpy.nanmax(row)
			rows.append([float(depths[window][pick]), float(low), float(mean), float(high), nans])
	result['rows'] = rows
	result['image'] = _window_picture(values, depths[window], found.units)
	return result


def _window_picture(values, depths, units) -> dict:
	"""A caliper window as a picture: depth across, elements down, the value as colour."""
	import matplotlib

	matplotlib.use('Agg')
	from matplotlib import pyplot
	from PIL import Image

	figure, axes = pyplot.subplots(figsize=(12, 4), dpi=100)
	shown = axes.imshow(
		values.T,
		aspect='auto',
		origin='lower',
		extent=(float(depths[0]), float(depths[-1]), 0, values.shape[1]),
		interpolation='nearest',
	)
	axes.ticklabel_format(axis='x', style='plain', useOffset=False)  # 4580.1, not 0.1 + 4.58e3
	axes.set_xlabel('Depth (m)')
	axes.set_ylabel('Element')
	figure.colorbar(shown, ax=axes, label=str((units or {}).get('values', 'value')))
	buffer = io.BytesIO()
	figure.savefig(buffer, format='png', bbox_inches='tight')
	pyplot.close(figure)
	buffer.seek(0)
	return _encoded(Image.open(buffer))


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
	facts = session_facts(text)
	log = terminal_log(project, name, facts['cerb'], before)
	return {'found': True, 'file': name, 'started': started, **facts, 'terminal_log': log}


def terminal_log(project: str, telemetry_file: str, version: str, before: str = '') -> dict:
	"""The errors and warnings (:func:`log_problems`) of the terminal log paired with
	*telemetry_file* in *project*, or why there is none: Cerberus writes one from 1.1 on."""
	name = telemetry_file.replace('_telemetry_', '_terminal_log_')
	path = ntpath.join(_inside_index(project), 'cerberus', 'terminal_log', name)
	try:
		text = _fetch(path).decode('utf-8', errors='replace')
	except RuntimeError as error:
		if version and not writes_terminal_log(version):
			why = f'Cerberus {version} writes no terminal log; Cerberus 1.1 and later do'
		else:
			why = f'terminal log not read: {error}'
		return {'found': False, 'why': why}
	return {'found': True, 'path': path, **log_problems(text, before)}


def writes_terminal_log(version: str) -> bool:
	"""Whether Cerberus *version*, such as 1.1.0.68 or 1.0.4+uat20261031, writes a terminal log."""
	numbers = re.match(r'(\d+)\.(\d+)', version)
	if not numbers:
		return False
	return (int(numbers[1]), int(numbers[2])) >= TERMINAL_LOG_SINCE


def log_problems(text: str, before: str = '') -> dict:
	"""The errors and warnings of a terminal log, logged before *before* (as for
	:func:`last_session`), each with its line number for `cerb_read_text`.

	Each distinct error is listed once, at its newest line, with the end of its traceback (the
	deepest frames and the exception) or the lines of its message, without the variable values
	loguru prints. Each warning call site is listed once, at its first line, with how often it
	fired.
	"""
	cutoff = ''
	if before:
		day, clock = before.split('_')
		cutoff = f'{day} {clock.replace("-", ":")}'
	records: list[dict] = []
	for number, line in enumerate(text.splitlines(), 1):
		match = LOG_RECORD.match(line)
		if match:
			if cutoff and match[1] >= cutoff:
				break  # the log is in time order
			records.append(
				{'line': number, 'level': match[2], 'where': match[3], 'message': match[4]}
			)
			records[-1]['detail'] = []
		elif records and _is_detail(line):
			records[-1]['detail'].append(line.rstrip()[:LOG_LINE_CHARS])
	errors: dict = {}
	warnings: dict = {}
	for record in records:
		if record['level'] in ('ERROR', 'CRITICAL'):
			key = (record['where'], record['message'])
			error = {**record, 'count': 1}
			previous = errors.pop(key, None)  # added again last, so the newest errors end the list
			if previous:
				error['count'] = previous['count'] + 1
				if not error['detail']:
					error['detail'] = previous['detail']  # the newest traceback it has
			errors[key] = error
		elif record['level'] == 'WARNING':
			if record['where'] not in warnings:
				warnings[record['where']] = {**record, 'count': 0}
			warnings[record['where']]['count'] += 1
	found: dict = {'errors': [], 'warnings': []}
	for error in list(errors.values())[-MAX_LOG_ERRORS:]:
		detail = error['detail']
		if detail and detail[0].startswith('Traceback'):
			detail = detail[-MAX_ERROR_DETAIL:]
			while len(detail) > 1 and not detail[0].lstrip('> ').startswith('File '):
				detail = detail[1:]  # start at a frame, not in the middle of one
		else:
			detail = detail[:MAX_ERROR_DETAIL]
		found['errors'].append('\n'.join([_log_line(error), *detail]))
	for warning in list(warnings.values())[:MAX_LOG_WARNINGS]:
		found['warnings'].append(_log_line(warning))
	if len(warnings) > MAX_LOG_WARNINGS:
		found['warnings'].append(f'and {len(warnings) - MAX_LOG_WARNINGS} more warning call sites')
	return found


def _is_detail(line: str) -> bool:
	"""Whether a line under a log record is kept: not blank, not a variable value loguru prints
	under a traceback line, and not a row of a banner's !s."""
	stripped = line.strip()
	return bool(stripped) and not LOG_VALUES.match(line) and set(stripped) != {'!'}


def _log_line(record: dict) -> str:
	"""One terminal log record as "line 141: ERROR where - message (3 times)"."""
	text = f'line {record["line"]}: {record["level"]} {record["where"]}'
	if record['message'].strip():  # a banner's message is in the lines under it
		text += f' - {record["message"].strip()}'
	text = text[:LOG_LINE_CHARS]
	if record['count'] > 1:
		text += f' ({record["count"]} times)'
	return text


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


def read_document(path: str, first: int = 1, contains: str = '') -> str:
	"""Pages of a PDF, or parts of a Word file, on the share, from page *first* on.

	The file is copied through the index and read in the Cerberus venv (:func:`document`). See
	:func:`document_page` for what an answer holds.
	"""
	if not path.lower().endswith(DOCUMENTS):
		raise ValueError(f'{path} is not a .pdf or .docx file')
	if ntpath.basename(path).startswith('~$'):
		raise ValueError(
			f'{path} is the lock file Word keeps while the file is open; read the file'
		)
	found = run_in('cerb', __name__, 'document', path=str(local_copy(path)))
	return (
		f'{ntpath.basename(path)}: {document_page(found["parts"], found["unit"], first, contains)}'
	)


def read_image(path: str, page: int = 0) -> list:
	"""An image on the share, or the pictures in one page of a PDF on the share, for Claude to
	look at: a line saying what follows, then the images (:func:`pictures`)."""
	from mcp.server.mcpserver import Image  # not in the Cerberus venv, which imports this module

	name = ntpath.basename(path)
	lower = path.lower()
	if not lower.endswith((*IMAGES, '.pdf')):
		raise ValueError(f'{path} is not an image ({", ".join(IMAGES)}) or a PDF')
	if lower.endswith('.pdf') and page < 1:
		raise ValueError('give the PDF page whose pictures to show, from 1')
	found = run_in('cerb', __name__, 'pictures', path=str(local_copy(path)), page=page)
	sizes = []
	for image in found['images']:
		sizes.append(f'{image["size"][0]}x{image["size"][1]}')
	if found['pages']:
		line = f'{name}, page {page} of {found["pages"]}: {found["found"]} pictures'
		if not found['images']:
			line += ' (only logos or icons, or none); cerb_read_document reads its text'
		elif found['found'] > len(found['images']):
			line += f', the first {len(found["images"])} shown'
	else:
		line = f'{name}'
	if sizes:
		line += f' ({", ".join(sizes)} pixels)'
	parts: list = [line]
	for image in found['images']:
		parts.append(Image(data=base64.b64decode(image['data']), format=image['format']))
	return parts


def read_table(path: str, table_name: str = '', first_row: int = 1, contains: str = '') -> str:
	"""A Parquet file, or a table of a SQLite file, on the share: pages of rows as for sheets
	(:func:`sheet_page`), row 1 being the column names. A SQLite file without *table_name* gives
	its tables."""
	lower = path.lower()
	if lower.endswith(PICKLES):
		raise ValueError(
			f'{path} is a pickle; loading one runs code stored in it, so it is never read'
		)
	if not lower.endswith(TABLES):
		raise ValueError(f'{path} is not a .parquet or SQLite (.db, .sqlite) file')
	name = ntpath.basename(path)
	if name.lower() == 'thumbs.db':
		raise ValueError(f"{path} is Windows' cache of picture thumbnails, not a SQLite file")
	found = run_in('cerb', __name__, 'table', path=str(local_copy(path)), name=table_name)
	if 'tables' in found:
		parts = []
		for found_name, rows in found['tables']:
			parts.append(f'"{found_name}" {rows:,} rows')
		return f'{name}: {len(found["tables"])} tables: {"; ".join(parts)}.'
	label = name
	if table_name:
		label += f' [{table_name}]'
	text = (
		f'{label}: row 1 holds the column names. {sheet_page(found["cells"], first_row, contains)}'
	)
	if found['rows'] > MAX_TABLE_ROWS:
		text += f'\nOnly the first {MAX_TABLE_ROWS:,} of {found["rows"]:,} rows can be paged.'
	return text


def caliper_piece(path: str, depth_from: float = 0, depth_to: float = 0) -> tuple[dict, Path, bool]:
	"""A .dvcaliper on the share, read by HTTP ranges: its size and depth range, and a small
	.dvcaliper of its own holding only the slices from *depth_from* to *depth_to* (m), or only
	its first slice when no window is asked for or the window is empty. The last value says
	whether the piece is the window.

	The window is found by a binary search on depth, four bytes per step, in either depth order.
	"""
	inside = _inside_index(path)
	url_path = _url_path(inside)
	elements, slices = CALIPER_HEADER.unpack(_get(url_path, part=(0, CALIPER_HEADER.size - 1)))
	row = (elements + 1) * CALIPER_VALUE.size
	read = {}

	def depth_of(index: int) -> float:
		if index not in read:
			start = CALIPER_HEADER.size + index * row
			found = _get(url_path, part=(start, start + CALIPER_VALUE.size - 1))
			(read[index],) = CALIPER_VALUE.unpack(found)
		return read[index]

	first, last = depth_of(0), depth_of(slices - 1)
	facts = {'slices': slices, 'elements': elements, 'depths': [min(first, last), max(first, last)]}
	start, count, window = 0, 1, False
	if depth_to > depth_from:
		if first <= last:
			start = _first_index(depth_of, slices, lambda depth: depth >= depth_from)
			stop = _first_index(depth_of, slices, lambda depth: depth > depth_to)
		else:
			start = _first_index(depth_of, slices, lambda depth: depth <= depth_to)
			stop = _first_index(depth_of, slices, lambda depth: depth < depth_from)
		facts['window_slices'] = stop - start
		most = min(MAX_WINDOW_SLICES, MAX_FETCH_BYTES // row)
		if stop - start > most:
			raise ValueError(
				f'{depth_from}-{depth_to} m holds {stop - start:,} slices; ask for at most '
				f'{most:,} (a narrower window)'
			)
		if stop > start:
			count, window = stop - start, True
		else:
			start = 0
	offset = CALIPER_HEADER.size + start * row
	data = _get(url_path, part=(offset, offset + count * row - 1))
	name = (
		f'{start}_{count}_{ntpath.basename(inside)}'  # the name keeps what Cerberus reads from it
	)
	piece = Path(tempfile.gettempdir(), 'dv_project_index', 'caliper pieces', name)
	piece.parent.mkdir(parents=True, exist_ok=True)
	piece.write_bytes(CALIPER_HEADER.pack(elements, count) + data)
	return facts, piece, window


def _first_index(depth_of, slices: int, passed) -> int:
	"""The first slice whose depth has *passed*, by binary search; *slices* when none has.
	*passed* turns true once along the file's depth order and stays true."""
	low, high = 0, slices
	while low < high:
		middle = (low + high) // 2
		if passed(depth_of(middle)):
			high = middle
		else:
			low = middle + 1
	return low


def read_caliper(path: str, depth_from: float = 0, depth_to: float = 0) -> list:
	"""A caliper file on the share as Cerberus reads it (:func:`caliper`): a summary, and for a
	depth window (metres) its slices as text and a picture."""
	from mcp.server.mcpserver import Image  # not in the Cerberus venv, which imports this module

	lower = path.lower()
	if lower.endswith(PICKLES):
		raise ValueError(
			f'{path} is a pickle; loading one runs code stored in it, so it is never read'
		)
	if not lower.endswith(CALIPERS):
		raise ValueError(f'{path} is not a .dvcaliper or .npy caliper file')
	if lower.endswith('.dvcaliper'):  # read by ranges: only the window is fetched
		facts, piece, window = caliper_piece(path, depth_from, depth_to)
		asked = (0, 0)
		if window:
			asked = (depth_from, depth_to)
		found = run_in(
			'cerb',
			__name__,
			'caliper',
			first_path=prod_code(),
			path=str(piece),
			depth_from=asked[0],
			depth_to=asked[1],
		)
		found.update(facts)  # the whole file's size and depths, not the piece's
	else:
		found = run_in(
			'cerb',
			__name__,
			'caliper',
			first_path=prod_code(),
			path=str(local_copy(path)),
			depth_from=depth_from,
			depth_to=depth_to,
		)
	low, high = found['depths']
	feet = f'{low / METRES_PER_FOOT:.1f}-{high / METRES_PER_FOOT:.1f} ft'
	size = f'{found["slices"]:,} slices x {found["elements"]} elements'
	depths = f'depth {low:.2f}-{high:.2f} m ({feet}), units {found["units"]}'
	lines = [f'{ntpath.basename(path)}: {found["type"]}, {size}, {depths}']
	if depth_to <= depth_from:
		lines.append(
			'Give depth_from and depth_to (m) to see the slices and a picture of a window.'
		)
		return ['\n'.join(lines)]
	lines.append(f'{found.get("window_slices", 0):,} slices from {depth_from} to {depth_to} m.')
	rows = found.get('rows', [])
	if rows:
		lines.append(
			f'{len(rows)} of them, evenly spread plus the {MAX_PEAK_ROWS} highest, by depth: '
			'depth, min, mean, max, empty elements'
		)
		for depth, smallest, mean, largest, nans in rows:
			feet = depth / METRES_PER_FOOT
			values = f'{smallest:.4g}\t{mean:.4g}\t{largest:.4g}\t{nans}'
			lines.append(f'{depth:.3f} m ({feet:.2f} ft)\t{values}')
	parts: list = ['\n'.join(lines)]
	if found.get('image'):
		parts.append(Image(data=base64.b64decode(found['image']['data']), format='png'))
	return parts


def document_page(parts: list[str], unit: str, first: int = 1, contains: str = '') -> str:
	"""A line saying what the answer holds, then each page (or part) from *first* on, up to
	DOC_PAGES and DOC_CHARS, under a `--- page n` line. With *contains*, only pages holding that
	text (any case) count. A page with no text is said to be one, likely an image."""
	numbers = []
	for number in range(max(first, 1), len(parts) + 1):
		if not contains or contains.lower() in parts[number - 1].lower():
			numbers.append(number)
	shown, size = [], 0
	for number in numbers:
		text = parts[number - 1].strip()
		if shown and (len(shown) >= DOC_PAGES or size + len(text) > DOC_CHARS):
			break
		shown.append(number)
		size += len(text)
	head = f'{len(parts)} {unit}s'
	if contains:
		head += f', {len(numbers)} from {unit} {max(first, 1)} on contain "{contains}"'
	lines = [head + '.']
	for number in shown:
		text = parts[number - 1].strip()
		if not text:
			text = f'(no text on this {unit}: likely an image, which this tool cannot show)'
		lines.append(f'--- {unit} {number}\n{text[:DOC_CHARS]}')
	if len(shown) < len(numbers):
		lines.append(f'More from first={numbers[len(shown)]}.')
	else:
		lines.append('End of document.')
	return '\n'.join(lines)


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
	"""*path* from the share, copied into the temp folder for the tools that need a file on disk.

	The copy streams to disk, so files up to DV_PROJECT_INDEX_MAX_MB (default 1 GB) can be read.
	A copy made in the last COPY_FRESH_S is used again, so paging through a big file downloads it
	once (a change to the file on the share in that time is not seen); copies older than
	COPY_KEEP_S are deleted.
	"""
	inside = _inside_index(path)
	relative = inside[len(_index_root()) + 1 :]
	copies = Path(tempfile.gettempdir(), 'dv_project_index')
	_prune(copies)
	target = copies / relative
	if target.exists() and time.time() - target.stat().st_mtime < COPY_FRESH_S:
		return target
	target.parent.mkdir(parents=True, exist_ok=True)
	_get(_url_path(inside), into=target)
	return target


def _prune(copies: Path) -> None:
	"""Delete copies older than COPY_KEEP_S, and the folders they leave empty."""
	if not copies.exists():
		return
	cutoff = time.time() - COPY_KEEP_S
	for path in copies.rglob('*'):
		if path.is_file() and path.stat().st_mtime < cutoff:
			path.unlink(missing_ok=True)
	for path in sorted(copies.rglob('*'), reverse=True):  # a folder after everything in it
		if path.is_dir() and not any(path.iterdir()):
			with contextlib.suppress(OSError):  # another triage is copying into it right now
				path.rmdir()


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
	return _get(_url_path(_inside_index(path)))


def _url_path(inside: str) -> str:
	"""The index's URL path of a file on the share."""
	parts = inside.lstrip('\\').split('\\')
	return '/%5C%5C' + '/'.join(urllib.parse.quote(part, safe='') for part in parts)


def _max_copy_bytes() -> int:
	text = _setting('DV_PROJECT_INDEX_MAX_MB', str(DEFAULT_MAX_COPY_MB))
	try:
		return int(text) * 2**20
	except ValueError as error:
		raise ValueError(f'DV_PROJECT_INDEX_MAX_MB must be a number of MB, not {text!r}') from error


def _stream(response, target: Path, limit: int) -> None:
	"""*response* into *target*, CHUNK_BYTES at a time, at most *limit* bytes; a copy that is
	too big or breaks off leaves nothing behind."""
	partial = target.with_name(target.name + '.partial')
	size = 0
	try:
		with partial.open('wb') as file:
			while True:
				chunk = response.read(CHUNK_BYTES)
				if not chunk:
					break
				size += len(chunk)
				if size > limit:
					raise RuntimeError(
						f'the file is over {limit // 2**20} MB, the most this bot copies '
						'(DV_PROJECT_INDEX_MAX_MB)'
					)
				file.write(chunk)
		partial.replace(target)
	finally:
		partial.unlink(missing_ok=True)


def _get(url_path: str, into: Path | None = None, part: tuple[int, int] | None = None) -> bytes:
	"""GET from the project index, the only request this module sends. The answer is returned,
	up to MAX_FETCH_BYTES, or with *into* streamed to that file (:func:`_stream`). *part*, the
	first and last byte, asks for only those bytes of the file (an HTTP range)."""
	missing = []
	for name in INDEX_ENV:
		if not os.environ.get(name):
			missing.append(name)
	if missing:
		raise RuntimeError(f'the project index is not set up here: set {", ".join(missing)}')
	url = _index_url()
	login = f'{os.environ[INDEX_ENV[0]]}:{os.environ[INDEX_ENV[1]]}'
	headers = {'Authorization': 'Basic ' + base64.b64encode(login.encode()).decode()}
	if part is not None:
		headers['Range'] = f'bytes={part[0]}-{part[1]}'
	request = urllib.request.Request(url + url_path, headers=headers)
	try:
		with urllib.request.urlopen(request, timeout=TIMEOUT_S) as response:
			if part is not None and response.status != PARTIAL_CONTENT:
				raise RuntimeError('the project index does not serve parts of files')
			if into is None:
				data = response.read(MAX_FETCH_BYTES + 1)
			else:
				_stream(response, into, _max_copy_bytes())
				data = b''
	except urllib.error.HTTPError as error:
		reason = {
			401: 'the project index refused the login',
			404: 'no such file in the project index',
		}.get(error.code, f'the project index answered {error.code} {error.reason}')
		raise RuntimeError(reason) from error
	except (urllib.error.URLError, TimeoutError) as error:
		raise RuntimeError(_no_answer(url, error)) from error
	if len(data) > MAX_FETCH_BYTES:
		raise RuntimeError(
			f'the file is over {MAX_FETCH_BYTES // 2**20} MB, too big to read as text here'
		)
	return data


def _no_answer(url: str, error: Exception) -> str:
	"""Why a request to the index ended without an HTTP answer: a timeout or no connection."""
	reason = getattr(error, 'reason', error)
	if isinstance(reason, TimeoutError):
		return f'the project index did not answer within {TIMEOUT_S} s'
	return f'cannot reach the project index at {url}: {reason}'


def register(server) -> None:
	from mcp.server.mcpserver import Image  # not at the top: the Cerberus venv imports this module

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
	def cerb_read_document(path: str, first: int = 1, contains: str = '') -> str:
		"""The text of a .pdf or .docx on the project share, such as a deliverable report or the
		2D log intro pages: up to 20 pages from page *first* (a Word file comes in parts of about
		3,000 characters), and where the next answer starts. *contains* keeps only the pages
		with that text. Pages that are only an image show no text."""
		return read_document(path, first, contains)

	@server.tool()
	def cerb_read_image(path: str, page: int = 0) -> list[str | Image]:
		"""Look at an image on the project share (.png, .jpg and other bitmaps, such as a plot in
		Deliverables), or at the pictures in page *page* of a PDF on the share (plots that
		cerb_read_document cannot read, like the 2D log overview plots). Large images are scaled
		to 1568 pixels; logos are left out."""
		return read_image(path, page)

	@server.tool()
	def cerb_read_table(path: str, table: str = '', first_row: int = 1, contains: str = '') -> str:
		"""A .parquet file (such as calibs_dataframe.parquet), or a table of a SQLite .db file,
		on the project share: up to 100 rows from *first_row*, row 1 being the column names, and
		where the next page starts. A .db file without *table* lists its tables. *contains*
		keeps only the rows with a cell containing that text. Pickle files are never read."""
		return read_table(path, table, first_row, contains)

	@server.tool()
	def cerb_read_caliper(
		path: str, depth_from: float = 0, depth_to: float = 0
	) -> list[str | Image]:
		"""A .dvcaliper (or caliper .npy) file on the project share, read by the prod Cerberus:
		its type, units, slices, elements and depth range. With *depth_from* and *depth_to* in
		metres (ft x 0.3048), also up to 60 slices of that window evenly spread plus the 10 with
		the highest values, each with the min, mean and max over its elements, and a picture of
		the window (depth across, elements down). Big files are read in parts: only the window is
		fetched, at most 20,000 slices."""
		return read_caliper(path, depth_from, depth_to)

	@server.tool()
	def cerb_last_session(project: str, user: str, before: str = '') -> dict:
		"""The reporter's newest Cerberus session in *project* before the ticket time
		(`YYYY-MM-DD_HH-MM-SS`): Cerberus version, failed script runs, last saved config, and from
		Cerberus 1.1 on its terminal log's errors (with the end of each traceback) and warnings,
		each with its line number for `cerb_read_text`. Before 1.1 there is no terminal log."""
		return last_session(project, user, before)

	@server.tool()
	def cerb_prod_version() -> str:
		"""Version of the Cerberus wheel in the prod folder, to compare with a session's."""
		return prod_version()
