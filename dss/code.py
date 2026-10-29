"""`code_*` tools: Cerberus source at the prod release, read-only through git.

Every call reads one commit, never the working tree, so answers match the Cerberus analysts run
and not whatever branch the checkout has open. The commit is DV_CERB_REF, else the
`release: v<prod version>` commit on origin/master. Fetch the checkout to see new releases.
"""

import os
import re
import subprocess
from pathlib import Path

from dss import cerb
from dss.worker import app_dir

MAX_LINES = 200
MAX_COMMITS = 20
GIT_TIMEOUT_S = 60


def prod_ref() -> str:
	"""The commit analysts run: DV_CERB_REF, else the release commit of the prod wheel."""
	if os.environ.get('DV_CERB_REF'):
		return os.environ['DV_CERB_REF']
	version = cerb.prod_version()
	subject = f'--grep=^release: v?{re.escape(version)} '
	found = _git('log', 'origin/master', '-1', '--format=%h', '-E', subject).strip()
	if not found:
		raise LookupError(
			f'no "release: v{version}" commit on origin/master; fetch the Cerberus checkout '
			'or set DV_CERB_REF'
		)
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


def files(ref: str) -> list[str]:
	"""Every file path in the Cerberus repo at *ref*."""
	return _git('ls-tree', '-r', '--name-only', ref).splitlines()


def line_count(path: str, ref: str) -> int:
	"""Lines in one file at *ref*; RuntimeError when the file is not there."""
	return len(_git('show', f'{ref}:{_checked(path)}').splitlines())


def _git(*args: str) -> str:
	"""Output of one read-only git command in the Cerberus checkout."""
	done = subprocess.run(
		['git', *args],
		cwd=app_dir('cerb'),
		capture_output=True,
		encoding='utf-8',
		errors='replace',
		timeout=GIT_TIMEOUT_S,
		check=False,
	)
	if done.returncode > 1:  # git grep exits with 1 when nothing matches
		raise RuntimeError(f'git {args[0]} failed: {done.stderr.strip()[-500:]}')
	return done.stdout


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
