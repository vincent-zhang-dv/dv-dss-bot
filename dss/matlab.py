"""`matlab_*` tools: the MATLAB CAT code (DataAnalysis) that Cerberus was ported from, read-only
through git.

CAT shows what a step did before the port, so it is a reference for intended behaviour beside the
Confluence SOPs. The two can disagree, and either can be wrong: CAT's code may do more or less
than its SOP says. Every call reads origin/master of the bot's own clone (DSS_MATLAB_GIT, made by
`python -m dss cerberus` without files over 1 MB), which :func:`refresh` fetches before each
triage. Searches cover MATLAB files (`*.m`) only.
"""

import os
from pathlib import Path

from dss import code

REPO_URL = 'https://github.com/darkvisiontech/DataAnalysis.git'
REF = 'origin/master'
CLONE_TIMEOUT_S = 1800  # about 500 MB on GitHub, most of it in files the clone leaves out


def grep(pattern: str, path: str = 'CAT', regex: bool = False) -> str:
	"""Lines of the MATLAB files under *path* containing *pattern*, as `file:line:text`."""
	kind = '-F'
	if regex:
		kind = '-E'
	found = _git('grep', '-n', '-I', kind, '-e', pattern, REF, '--', _matlab_files(path))
	lines = [line.removeprefix(f'{REF}:') for line in found.splitlines()]
	return code._capped(f'MATLAB CAT at {REF}, matches of {pattern!r} in {path}', lines)


def read(path: str, start: int = 1, end: int = 0) -> str:
	"""Lines *start* to *end* of one file; *end* 0 means the last line."""
	lines = _git('show', f'{REF}:{code._checked(path)}').splitlines()
	start = max(start, 1)
	if not end:
		end = len(lines)
	end = min(end, start + code.MAX_LINES - 1, len(lines))
	numbered = [f'{number}\t{lines[number - 1]}' for number in range(start, end + 1)]
	return f'{path} at {REF}, lines {start}-{end} of {len(lines)}:\n' + '\n'.join(numbered)


def log(path: str = 'CAT', text: str = '') -> str:
	"""The newest commits that change *path*; with *text*, only those that add or remove it."""
	search = []
	title = f'Newest commits that change {path}'
	if text:
		search.append(f'-S{text}')
		title = f'Commits that add or remove {text!r} in {path}'
	found = _git(
		'log',
		'--no-textconv',
		*search,
		f'-{code.MAX_COMMITS}',
		'--format=%h %ad %an | %s',
		'--date=short',
		REF,
		'--',
		code._checked(path),
	)
	return code._capped(title, found.splitlines())


def repo_dir() -> str:
	"""The bot's MATLAB CAT clone (DSS_MATLAB_GIT)."""
	folder = os.environ.get('DSS_MATLAB_GIT')
	if not folder:
		raise RuntimeError(
			'MATLAB CAT is not set up here: set DSS_MATLAB_GIT and run `python -m dss cerberus`'
		)
	return folder


def clone() -> bool:
	"""Make the bot's clone at DSS_MATLAB_GIT, with no working files and no file over 1 MB (data,
	not code); False when it is there."""
	folder = Path(repo_dir())
	if (folder / '.git').exists():
		return False
	folder.parent.mkdir(parents=True, exist_ok=True)
	code._run(
		'clone',
		'--no-checkout',
		'--quiet',
		'--filter=blob:limit=1m',
		REPO_URL,
		str(folder),
		cwd=str(folder.parent),
		timeout=CLONE_TIMEOUT_S,
	)
	return True


def refresh() -> list[str]:
	"""Fetch the bot's clone before a triage, when DSS_MATLAB_GIT is set; what failed, as notes.
	A failure never stops the triage: the tools read the last fetch."""
	if not os.environ.get('DSS_MATLAB_GIT'):
		return []
	try:
		done = code._run('fetch', '--prune', '--quiet', 'origin', cwd=repo_dir())
		if done.returncode:  # _run lets exit code 1 through; for a fetch it is a failure
			raise RuntimeError(f'git fetch failed: {done.stderr.strip()[-500:]}')
	except Exception as error:  # noqa: BLE001 - the last fetch still serves
		return [f'MATLAB CAT clone not updated, so its newest commits may be missing: {error}']
	return []


def setup() -> list[str]:
	"""Make or update the bot's clone (`python -m dss cerberus`), then say what the tools read."""
	if not os.environ.get('DSS_MATLAB_GIT'):
		return ['DSS_MATLAB_GIT is not set: the matlab tools are off']
	lines = []
	if clone():
		lines.append(f'cloned MATLAB CAT into {repo_dir()}')
	lines.extend(refresh())
	newest = _git('log', '-1', '--format=%h %ad | %s', '--date=short', REF).strip()
	lines.append(f'matlab tools: {repo_dir()}, {REF} = {newest}')
	return lines


def _matlab_files(path: str) -> str:
	"""The pathspec of the MATLAB files under *path* (a `*` in it spans folders)."""
	path = code._checked(path).rstrip('/')
	if path.endswith('.m'):
		return path
	if not path:
		return '*.m'
	return f'{path}/*.m'


def _git(*args: str) -> str:
	"""Output of one read-only git command in the bot's MATLAB CAT clone."""
	return code._run(*args, cwd=repo_dir()).stdout


def register(server) -> None:
	@server.tool()
	def matlab_grep(pattern: str, path: str = 'CAT', regex: bool = False) -> str:
		"""Lines of the MATLAB CAT code (DataAnalysis, which Cerberus was ported from) containing
		*pattern*, in `.m` files under *path* (file:line:text); with *regex*, an extended regex."""
		return grep(pattern, path, regex)

	@server.tool()
	def matlab_read(path: str, start: int = 1, end: int = 0) -> str:
		"""Lines *start*-*end* of one MATLAB CAT file (at most 200 lines)."""
		return read(path, start, end)

	@server.tool()
	def matlab_log(path: str = 'CAT', text: str = '') -> str:
		"""The newest commits that change a MATLAB CAT file or folder; with *text*, only those that
		add or remove it, to date when a behaviour came in."""
		return log(path, text)
