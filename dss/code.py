"""`code_*` tools: Cerberus source at the prod release, read-only through git.

Every call reads one commit, never the working tree, so answers match the Cerberus analysts run
and not whatever branch the checkout has open. The commit is DV_CERB_REF, else the
`release: v<prod version>` commit on origin/master. History tools (`code_log_s`, `code_log`,
`code_branches`, `code_contains`) also look at remote branches and the analyst's build, to find
fixes that exist but are not released.

The repo is the bot's own clone (DSS_CERB_GIT, made by `python -m dss cerberus`), which
:func:`refresh` fetches before each triage, so new releases and branches are seen. Without it the
tools read the developer's checkout (DSS_CERB_CWD), as fresh as its last fetch.
"""

import os
import re
import subprocess
from datetime import datetime
from pathlib import Path

from dss import cerb
from dss.worker import app_dir, run_in

MAX_LINES = 200
MAX_COMMITS = 20
GIT_TIMEOUT_S = 60
CLONE_TIMEOUT_S = 900  # the repo is about 200 MB
REPO_URL = 'https://github.com/darkvisiontech/DataDHPython.git'
COMMIT = re.compile(r'[0-9a-f]{7,40}')
VERSION = re.compile(r'[0-9]+(\.[0-9]+)*([+.][0-9A-Za-z.]+)?')  # 1.0.4, 1.0.4+uat20261031
BRANCH = re.compile(r'[A-Za-z0-9][A-Za-z0-9._/-]*')  # develop-v1.1-release, origin/DTN-3726-x
DATE = re.compile(r'[0-9]{4}-[0-9]{2}-[0-9]{2}')


def prod_ref() -> str:
	"""The commit analysts run: DV_CERB_REF, else the release commit of the prod wheel."""
	if os.environ.get('DV_CERB_REF'):
		return os.environ['DV_CERB_REF']
	return release_ref(cerb.prod_version())


def release_ref(version: str) -> str:
	"""The `release: v<version>` commit on origin/master."""
	subject = f'--grep=^release: v?{re.escape(version)} '
	found = _git('log', 'origin/master', '-1', '--format=%h', '-E', subject).strip()
	if not found:
		raise LookupError(
			f'no "release: v{version}" commit on origin/master; fetch the Cerberus checkout '
			'or set DV_CERB_REF'
		)
	return found


def version_ref(version: str) -> str:
	"""The commit of Cerberus *version*: `prod`, a release such as 1.0.4, a local build such as
	1.0.4+uat20261031 (found by the commit that names it), or a remote branch."""
	if version == 'prod':
		return prod_ref()
	if not VERSION.fullmatch(version):
		branch = branch_ref(version)
		if branch is None:
			raise ValueError(
				f'{version!r} is not a Cerberus version like 1.0.4 or 1.0.4+uat20261031, '
				'or a remote branch'
			)
		return branch
	if '+' not in version:
		return release_ref(version)
	found = _git('log', '--all', '-1', '--format=%h', '-F', f'--grep={version}').strip()
	if not found:
		raise LookupError(f'no commit names the build {version}; the checkout may not have it')
	return found


def grep(pattern: str, path: str = 'cerberus', regex: bool = False) -> str:
	"""Lines containing *pattern* under *path* at the prod release, as `file:line:text`."""
	ref = prod_ref()
	if regex:
		kind = '-E'
	else:
		kind = '-F'  # error texts contain ( [ and . characters, so they are matched literally
	found = _git('grep', '-n', '-I', kind, '-e', pattern, ref, '--', _checked(path))
	lines = [line.removeprefix(f'{ref}:') for line in found.splitlines()]
	return _capped(f'Cerberus at {ref}, matches of {pattern!r} in {path}', lines)


def read(path: str, start: int = 1, end: int = 0) -> str:
	"""Lines *start* to *end* of one file at the prod release; *end* 0 means the last line."""
	ref = prod_ref()
	lines = _git('show', f'{ref}:{_checked(path)}').splitlines()
	start = max(start, 1)
	if not end:
		end = len(lines)
	end = min(end, start + MAX_LINES - 1, len(lines))
	numbered = [f'{number}\t{lines[number - 1]}' for number in range(start, end + 1)]
	return f'{path} at {ref}, lines {start}-{end} of {len(lines)}:\n' + '\n'.join(numbered)


def log_s(text: str, path: str = 'cerberus', all_branches: bool = False) -> str:
	"""Commits that add or remove *text*, up to the prod release or on any remote branch."""
	if all_branches:
		where = '--remotes'
		scope = 'any remote branch'
	else:
		where = prod_ref()
		scope = f'the release {where}'
	found = _git(
		'log',
		# Git for Windows converts .docx and .pdf blobs with a text filter, which makes -S slow
		# and makes it fail on Office lock files; code search needs only the text files.
		'--no-textconv',
		f'-S{text}',
		f'-{MAX_COMMITS}',
		'--format=%h %ad %an | %s',
		'--date=short',
		where,
		'--',
		_checked(path),
	)
	return _capped(f'Commits up to {scope} that add or remove {text!r}', found.splitlines())


def log(path: str = 'cerberus', since: str = '', all_branches: bool = False) -> str:
	"""The newest commits that change *path*, up to the prod release or on any remote branch,
	from *since* (YYYY-MM-DD) on when given."""
	if all_branches:
		where = '--remotes'
		title = f'Newest commits that change {path} on any remote branch'
	else:
		where = prod_ref()
		title = f'Newest commits that change {path} up to the release {where}'
	dates = []
	if since:
		if not DATE.fullmatch(since):
			raise ValueError(f'{since!r} is not a date like 2026-10-01')
		dates.append(f'--since={since}')
		title += f', since {since}'
	found = _git(
		'log',
		'--no-textconv',
		f'-{MAX_COMMITS}',
		'--format=%h %ad %an | %s',
		'--date=short',
		*dates,
		where,
		'--',
		_checked(path),
	)
	return _capped(title, found.splitlines())


def branches(text: str) -> str:
	"""Remote branches whose name contains *text* (any case), newest first, with their last
	commit."""
	found = _git(
		'for-each-ref',
		'--sort=-committerdate',
		'--format=%(refname:short) %(committerdate:short) %(authorname) | %(subject)',
		'refs/remotes',
	)
	lines = []
	for line in found.splitlines():
		name = line.split(' ', 1)[0]
		if text.lower() in name.lower():
			lines.append(line)
	return _capped(f'Remote branches named with {text!r}', lines)


def branch_ref(name: str) -> str | None:
	"""The commit at the tip of remote branch *name* (with or without `origin/`); None when the
	checkout has no such branch."""
	if not BRANCH.fullmatch(name):
		return None
	if not name.startswith('origin/'):
		name = f'origin/{name}'
	found = _run('rev-parse', '--verify', '--quiet', '--short', f'refs/remotes/{name}^{{commit}}')
	return found.stdout.strip() or None


def contains(commit: str, version: str) -> str:
	"""Whether the Cerberus *version* (see :func:`version_ref`) includes *commit*, a commit hash
	or the tip of a remote branch."""
	if not COMMIT.fullmatch(commit):
		tip = branch_ref(commit)
		if tip is None:
			raise ValueError(f'{commit!r} is not a commit hash or a remote branch')
		commit = tip
	ref = version_ref(version)
	included = _run('merge-base', '--is-ancestor', commit, ref).returncode == 0
	build = _git('log', '-1', '--format=%h %ad | %s', '--date=short', ref).strip()
	change = _git('log', '-1', '--format=%h %ad %an | %s', '--date=short', commit).strip()
	if included:
		return f'Cerberus {version} ({build}) includes {change}'
	return f'Cerberus {version} ({build}) does not include {change}'


def repo_dir() -> str:
	"""The Cerberus repo the tools read: the bot's clone (DSS_CERB_GIT), else the developer's
	checkout (DSS_CERB_CWD)."""
	return os.environ.get('DSS_CERB_GIT') or app_dir('cerb')


def clone() -> bool:
	"""Make the bot's clone at DSS_CERB_GIT, with no working files; False when it is there."""
	folder = Path(os.environ['DSS_CERB_GIT'])
	if (folder / '.git').exists():
		return False
	folder.parent.mkdir(parents=True, exist_ok=True)
	_run(
		'clone',
		'--no-checkout',
		'--quiet',
		REPO_URL,
		str(folder),
		cwd=str(folder.parent),
		timeout=CLONE_TIMEOUT_S,
	)
	return True


def fetch() -> None:
	"""Bring the bot's clone up to date: every branch, with the deleted ones dropped. Only the
	bot's own clone is fetched, never a developer's checkout."""
	if not os.environ.get('DSS_CERB_GIT'):
		raise RuntimeError("only the bot's own clone is fetched; set DSS_CERB_GIT")
	done = _run('fetch', '--prune', '--quiet', 'origin')
	if done.returncode:  # _run lets exit code 1 through; for a fetch it is a failure
		raise RuntimeError(f'git fetch failed: {done.stderr.strip()[-500:]}')


def refresh() -> list[str]:
	"""Bring the bot's Cerberus copies up to date before a triage; what failed, as notes.

	Fetches the clone when DSS_CERB_GIT is set, and unpacks a new prod wheel when
	DSS_CERB_PROD_CODE is set (:func:`dss.cerb.prod_code`). A failure never stops the triage:
	the tools read what is there.
	"""
	notes = []
	if os.environ.get('DSS_CERB_GIT'):
		try:
			fetch()
		except Exception as error:  # noqa: BLE001 - the last fetch still serves
			notes.append(
				f'Cerberus clone not updated, so the newest commits may be missing: {error}'
			)
	try:
		cerb.prod_code()
	except Exception as error:  # noqa: BLE001 - only cerb_client_info needs it
		notes.append(f'prod Cerberus not unpacked, so cerb_client_info may fail: {error}')
	return notes


def setup() -> list[str]:
	"""Make or update the bot's Cerberus copies (`python -m dss cerberus`), then say what each
	tool reads, one line each."""
	lines = []
	if os.environ.get('DSS_CERB_GIT'):
		if clone():
			lines.append(f'cloned Cerberus into {os.environ["DSS_CERB_GIT"]}')
	else:
		lines.append('DSS_CERB_GIT is not set: the code tools read the developer checkout')
	lines.extend(refresh())
	version = cerb.prod_version()
	release = _git('log', '-1', '--format=%h %ad | %s', '--date=short', prod_ref()).strip()
	lines.append(f'code tools: {repo_dir()}, prod {version} = {release}')
	fetched = Path(repo_dir(), '.git', 'FETCH_HEAD')
	if fetched.exists():
		when = datetime.fromtimestamp(fetched.stat().st_mtime).strftime('%Y-%m-%d %H:%M')
		lines.append(f'last fetch: {when}')
	unpacked = cerb.prod_code()
	found = run_in('cerb', 'dss.cerb', 'cerberus_found', first_path=unpacked)
	lines.append(f'cerb_client_info runs Cerberus {found["version"]} from {found["file"]}')
	if not unpacked:
		lines.append(f'warning: that is the checkout, not prod {version}; set DSS_CERB_PROD_CODE')
	elif not found['file'].startswith(unpacked) or found['version'] != version:
		lines.append(f'warning: that is not the prod {version} unpacked in {unpacked}')
	return lines


def files(ref: str) -> list[str]:
	"""Every file path in the Cerberus repo at *ref*."""
	return _git('ls-tree', '-r', '--name-only', ref).splitlines()


def line_count(path: str, ref: str) -> int:
	"""Lines in one file at *ref*; RuntimeError when the file is not there."""
	return len(_git('show', f'{ref}:{_checked(path)}').splitlines())


def _git(*args: str) -> str:
	"""Output of one read-only git command in the Cerberus checkout."""
	return _run(*args).stdout


def _run(*args: str, cwd: str = '', timeout: float = GIT_TIMEOUT_S) -> subprocess.CompletedProcess:
	"""One git command in the Cerberus repo (:func:`repo_dir`) or *cwd*. Only reads, apart from
	the bot's own clone taking in what it fetches. Exit code 1 means no or false."""
	if not cwd:
		cwd = repo_dir()
	done = subprocess.run(
		['git', *args],
		cwd=cwd,
		capture_output=True,
		encoding='utf-8',
		errors='replace',
		timeout=timeout,
		check=False,
	)
	if done.returncode > 1:  # git grep exits with 1 when nothing matches
		raise RuntimeError(f'git {args[0]} failed: {done.stderr.strip()[-500:]}')
	return done


def _checked(path: str) -> str:
	if path.startswith('-') or '..' in Path(path).parts:
		raise ValueError(f'{path!r} is not a path inside the Cerberus repo')
	return path


def _capped(title: str, lines: list[str]) -> str:
	if not lines:
		return f'{title}: nothing found'
	text = f'{title}:\n' + '\n'.join(lines[:MAX_LINES])
	more = len(lines) - MAX_LINES
	if more > 0:
		text += f'\n... {more} more lines; narrow the pattern or the path'
	return text


def register(server) -> None:
	@server.tool()
	def code_grep(pattern: str, path: str = 'cerberus', regex: bool = False) -> str:
		"""Lines of Cerberus at the prod release containing *pattern* (file:line:text); with
		*regex*, *pattern* is an extended regex (no \\d, use [0-9])."""
		return grep(pattern, path, regex)

	@server.tool()
	def code_read(path: str, start: int = 1, end: int = 0) -> str:
		"""Lines *start*-*end* of one Cerberus file at the prod release (at most 200 lines)."""
		return read(path, start, end)

	@server.tool()
	def code_log_s(text: str, path: str = 'cerberus', all_branches: bool = False) -> str:
		"""Commits that add or remove *text*, up to the prod release, or on any remote branch
		when *all_branches* (to find fixes not released yet)."""
		return log_s(text, path, all_branches)

	@server.tool()
	def code_log(path: str = 'cerberus', since: str = '', all_branches: bool = False) -> str:
		"""The newest commits that change a Cerberus file or folder, up to the prod release, or on
		any remote branch when *all_branches*; *since* (YYYY-MM-DD) keeps the recent ones."""
		return log(path, since, all_branches)

	@server.tool()
	def code_branches(text: str) -> str:
		"""Remote Cerberus branches whose name contains *text*, such as a DTN key or a feature
		word, newest first: work that may not be merged or released yet."""
		return branches(text)

	@server.tool()
	def code_contains(commit: str, version: str) -> str:
		"""Whether Cerberus *version* includes *commit* (a hash or a remote branch). *version* is
		`prod`, a release such as 1.0.4, the analyst's build such as 1.0.4+uat20261031, or a
		remote branch such as develop-v1.1-release."""
		return contains(commit, version)
