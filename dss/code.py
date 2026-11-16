"""`code_*` tools: Cerberus source at the prod release, read-only through git.

Every call reads one commit, never the working tree, so answers match the Cerberus analysts run
and not whatever branch the checkout has open. The commit is DV_CERB_REF, else the
`release: v<prod version>` commit on origin/master. History tools (`code_log_s`, `code_log`,
`code_branches`, `code_contains`) also look at remote branches and the analyst's build, to find
fixes that exist but are not released.

The repo is the bot's own clone (DSS_CERB_GIT, made by `python -m dss cerberus`), which
:func:`refresh` fetches before each triage, so new releases and branches are seen. Without it the
tools read the developer's checkout (DSS_CERB_CWD), as fresh as its last fetch.

In a replay (:mod:`dss.cutoff`), DV_CERB_REF is the analyst's build, and history is what GitHub
held at the cutoff: each branch at the commit it pointed to then, from GitHub's push history
(:func:`branches_then`), so a commit made before but pushed after is not seen. Later releases and
branches are not seen either. Without the push history, git's commit dates stand in.
"""

import json
import os
import re
import shutil
import subprocess
import tarfile
from datetime import UTC, datetime, timedelta
from pathlib import Path

from dss import cerb, cutoff, jira
from dss.worker import ROOT, app_dir, run_in

MAX_LINES = 200
MAX_COMMITS = 20
GIT_TIMEOUT_S = 60
CLONE_TIMEOUT_S = 900  # the repo is about 200 MB
REPO_URL = 'https://github.com/darkvisiontech/DataDHPython.git'
GITHUB_URL = REPO_URL.removesuffix('.git')
COMMIT = re.compile(r'[0-9a-f]{7,40}')
VERSION = re.compile(r'[0-9]+(\.[0-9]+)*([+.][0-9A-Za-z.]+)?')  # 1.0.4, 1.0.4+uat20261031
BRANCH = re.compile(r'[A-Za-z0-9][A-Za-z0-9._/-]*')  # develop-v1.1-release, origin/DTN-3726-x
DATE = re.compile(r'[0-9]{4}-[0-9]{2}-[0-9]{2}')
# What web_links looks for in a post's text
NAMED_COMMIT = re.compile(r'\b(?=[0-9a-f]*[a-f])(?=[0-9a-f]*[0-9])[0-9a-f]{7,40}\b')  # not 2026110
NAMED_RELEASE = re.compile(r'\bCerberus v?([0-9]+(?:\.[0-9]+){1,3})(?![+\w]|\.\w)', re.IGNORECASE)
NAMED_PULL = re.compile(r'(?:\bPR |\()#([0-9]+)\b')  # PR #1018, 'release: 1.1 (#978)'
WORD = re.compile(r'[\w./-]+')
BRANCH_MARK = re.compile(r'[-/._0-9]')  # in a branch name such as DTN-3726-fix, not in a word
PULL_SUBJECT = re.compile(r'\(#([0-9]+)\)$')  # a squash merge's subject ends with its PR
RELEASE_SUBJECT = re.compile(r'release: v?([0-9][0-9A-Za-z.+]*)')
EXPORT_DIR = ROOT / 'out' / 'cerberus_at'  # a replay's Cerberus source, one folder per commit
MAX_RELEASES = 30  # release commits looked through for prod at a replay's cutoff
RELEASES_PROJECT = 'DTN'  # whose Jira versions (CERBERUS v1.0.4) carry release dates
JIRA_RELEASE = re.compile(r'CERBERUS v?([0-9][0-9.]*)', re.IGNORECASE)
GITHUB_REPO = 'darkvisiontech/DataDHPython'
ACTIVITY_FILE = ROOT / 'out' / 'archive' / 'github_activity.json'  # GitHub's push history, kept
GH_PATH = Path(r'C:\Program Files\GitHub CLI\gh.exe')  # where the gh installer puts it
GH_TIMEOUT_S = 300  # the whole push history takes about 35 s
ZERO_SHA = '0' * 40  # the commit "after" a branch is deleted
# Asking GitHub only for what came since the kept history: the shortest period that covers it
ACTIVITY_PERIODS = (('day', 1), ('week', 7), ('month', 28), ('quarter', 90), ('year', 365))
_BRANCHES_THEN: dict[str, dict[str, str] | None] = {}  # by cutoff, for this process
_KNOWN_THEN: dict[str, set[str]] = {}


def prod_ref() -> str:
	"""The commit analysts run: DV_CERB_REF, else the release commit of the prod wheel."""
	if os.environ.get('DV_CERB_REF'):
		return os.environ['DV_CERB_REF']
	return release_ref(cerb.prod_version())


def release_ref(version: str) -> str:
	"""The `release: v<version>` commit on origin/master, the version as the wheel names it or as
	the release commit does (:func:`_release_names`). In a replay, only a release made by then."""
	names = _release_names(version)
	for name in names:
		subject = f'--grep=^release: v?{re.escape(name)} '
		found = _git('log', _master_then(), *_until(), '-1', '--format=%h', '-E', subject)
		if found.strip():
			return found.strip()
	looked_for = f'"release: v{version}"'
	if len(names) > 1:
		looked_for += f' (or {", ".join(names[1:])})'
	if cutoff.when() is not None:
		raise LookupError(f'no {looked_for} commit by then: {cutoff.refusal("its release")}')
	raise LookupError(
		f'no {looked_for} commit on origin/master; fetch the Cerberus checkout or set DV_CERB_REF'
	)


def release_then() -> tuple[str, str]:
	"""Prod as it was at the replay's cutoff: the newest release on origin/master that was out by
	then, as its commit names it (1.0.4, 1.1), and that commit. The prod folder keeps only
	today's wheel, so this is how a replay knows the prod of its time.

	A release counts from the latest of what dates it (:func:`_released`), as release commits
	come days before the release, so a replay never calls a release prod before it was out.
	"""
	found = _git(
		'log',
		_master_then(),
		'--first-parent',
		*_until(),
		f'-{MAX_RELEASES}',
		'--format=%h %cI %s',
		'-E',
		f'--grep=^{RELEASE_SUBJECT.pattern}',
	)
	dates = _release_dates()
	try:
		wheel: tuple[str, datetime] | None = cerb.prod_wheel()
	except OSError:  # no prod folder here: the commits and Jira still date the releases
		wheel = None
	for line in found.splitlines():
		ref, made, subject = line.split(' ', 2)
		release = RELEASE_SUBJECT.match(subject)
		if not release:
			continue
		if not cutoff.after(_released(release[1], datetime.fromisoformat(made), dates, wheel)):
			return release[1], ref
	raise LookupError('no release on origin/master was out by the ticket time')


def _released(
	name: str, made: datetime, dates: dict[str, datetime], wheel: tuple[str, datetime] | None
) -> datetime:
	"""When release *name* was out, at the latest: its commit time *made*, the day after its
	Jira release date (*dates*), and, for the prod folder's *wheel*, when the wheel was written."""
	latest = made
	for alias in _release_names(name):
		if alias in dates:
			latest = max(latest, dates[alias])
	if wheel is not None and name in _release_names(wheel[0]):
		latest = max(latest, wheel[1])
	return latest


def _release_dates() -> dict[str, datetime]:
	"""The released Cerberus versions in Jira (as 1.0.4), each with the start of the day after
	its release date: a release on the ticket's day may have come after it. Empty when Jira
	cannot say."""
	try:
		versions = jira.versions(RELEASES_PROJECT)
	except Exception:  # noqa: BLE001 - the commits and the wheel still date the releases
		return {}
	dates = {}
	for version in versions:
		named = JIRA_RELEASE.fullmatch(str(version.get('name', '')))
		if named and version.get('released') and version.get('releaseDate'):
			day = datetime.strptime(version['releaseDate'], '%Y-%m-%d')
			dates[named[1]] = (day + timedelta(days=1)).astimezone()
	return dates


def _release_names(version: str) -> list[str]:
	"""How a release commit may name *version*: as the wheel does (1.1.0.68), without the build
	number (1.1.0), and without a zero patch (1.1). A real patch (1.1.1) is never dropped."""
	parts = version.split('.')
	names = [version]
	if len(parts) == 4:
		parts = parts[:3]
		names.append('.'.join(parts))
	if len(parts) == 3 and parts[2] == '0':
		names.append('.'.join(parts[:2]))
	return names


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
	everywhere, stdin = ['--all'], None
	if branches_then():
		everywhere, stdin = _branch_revisions()
	grep = f'--grep={version}'
	found = _git('log', *everywhere, *_until(), '-1', '--format=%h', '-F', grep, stdin=stdin)
	if not found.strip():
		raise LookupError(f'no commit names the build {version}; the checkout may not have it')
	return found.strip()


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
	"""Commits that add or remove *text*, up to the prod release or on any remote branch (in a
	replay, the branches as GitHub held them then, :func:`_branch_revisions`)."""
	stdin = None
	if all_branches:
		where, stdin = _branch_revisions()
		scope = 'any remote branch'
	else:
		where = [prod_ref()]
		scope = f'the release {where[0]}'
	found = _git(
		'log',
		# Git for Windows converts .docx and .pdf blobs with a text filter, which makes -S slow
		# and makes it fail on Office lock files; code search needs only the text files.
		'--no-textconv',
		f'-S{text}',
		f'-{MAX_COMMITS}',
		'--format=%h %ad %an | %s',
		'--date=short',
		*_until(),
		*where,
		'--',
		_checked(path),
		stdin=stdin,
	)
	return _capped(f'Commits up to {scope} that add or remove {text!r}', found.splitlines())


def log(path: str = 'cerberus', since: str = '', all_branches: bool = False) -> str:
	"""The newest commits that change *path*, up to the prod release or on any remote branch (as
	for :func:`log_s`), from *since* (YYYY-MM-DD) on when given."""
	stdin = None
	if all_branches:
		where, stdin = _branch_revisions()
		title = f'Newest commits that change {path} on any remote branch'
	else:
		where = [prod_ref()]
		title = f'Newest commits that change {path} up to the release {where[0]}'
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
		*_until(),
		*where,
		'--',
		_checked(path),
		stdin=stdin,
	)
	return _capped(title, found.splitlines())


def branches(text: str) -> str:
	"""Remote branches whose name contains *text* (any case), newest first, with their last
	commit. In a replay, the branches as GitHub held them then (:func:`branches_then`), else those
	with a commit of their own by then, shown as they were."""
	tips = branches_then()
	if tips is not None:
		lines = []
		for name, commit in tips.items():
			if text.lower() in name.lower():
				lines.append(
					f'{name} ' + _git('log', '-1', '--format=%cs %an | %s', commit).strip()
				)
		lines.sort(key=lambda line: line.split(' ', 2)[1], reverse=True)
		return _capped(f'Remote branches named with {text!r}', lines)
	found = _git(
		'for-each-ref',
		'--sort=-committerdate',
		'--format=%(refname:short) %(committerdate:short) %(authorname) | %(subject)',
		'refs/remotes',
	)
	lines = []
	for line in found.splitlines():
		name = line.split(' ', 1)[0]
		if text.lower() not in name.lower():
			continue
		if cutoff.when() is not None:
			tip = branch_ref(name)
			if tip is None:  # no commit of its own yet
				continue
			line = f'{name} ' + _git('log', '-1', '--format=%cs %an | %s', tip).strip()
		lines.append(line)
	if cutoff.when() is not None:  # newest first by their tips then
		lines.sort(key=lambda line: line.split(' ', 2)[1], reverse=True)
	return _capped(f'Remote branches named with {text!r}', lines)


def branch_ref(name: str) -> str | None:
	"""The commit at the tip of remote branch *name* (with or without `origin/`); None when the
	checkout has no such branch.

	In a replay, the tip as GitHub held it then (:func:`branches_then`); without the push history,
	the newest commit of the branch's own (not on origin/master then) made by the cutoff. None when
	the branch had no such tip then.
	"""
	if not BRANCH.fullmatch(name):
		return None
	if not name.startswith('origin/'):
		name = f'origin/{name}'
	tips = branches_then()
	if tips is not None:
		return tips.get(name)
	found = _run('rev-parse', '--verify', '--quiet', '--short', f'refs/remotes/{name}^{{commit}}')
	tip = found.stdout.strip()
	if not tip or cutoff.when() is None:
		return tip or None
	master = _git('log', '-1', '--first-parent', *_until(), '--format=%h', 'origin/master').strip()
	if name == 'origin/master':
		return master or None
	own = [f'refs/remotes/{name}']
	if master:
		own.append(f'^{master}')
	return _git('log', '-1', *_until(), '--format=%h', *own).strip() or None


def contains(commit: str, version: str) -> str:
	"""Whether the Cerberus *version* (see :func:`version_ref`) includes *commit*, a commit hash
	or the tip of a remote branch. In a replay, a commit GitHub didn't hold then is refused (made
	later, by its date, without the push history)."""
	if not COMMIT.fullmatch(commit):
		tip = branch_ref(commit)
		if tip is None:
			raise ValueError(f'{commit!r} is not a commit hash or a remote branch')
		commit = tip
	known = _known_then()
	if known is not None:
		full = _git('rev-parse', '--verify', '--quiet', f'{commit}^{{commit}}').strip()
		if full not in known:
			raise LookupError(cutoff.refusal(f'commit {commit}'))
	elif cutoff.when() is not None:
		made = datetime.fromisoformat(_git('log', '-1', '--format=%cI', commit).strip())
		if cutoff.after(made):
			raise LookupError(cutoff.refusal(f'commit {commit}'))
	ref = version_ref(version)
	included = _run('merge-base', '--is-ancestor', commit, ref).returncode == 0
	build = _git('log', '-1', '--format=%h %ad | %s', '--date=short', ref).strip()
	change = _git('log', '-1', '--format=%h %ad %an | %s', '--date=short', commit).strip()
	if included:
		return f'Cerberus {version} ({build}) includes {change}'
	return f'Cerberus {version} ({build}) does not include {change}'


def web_links(text: str) -> dict[str, str]:
	"""GitHub links for the commits, branches, PRs and releases *text* names, by the name as
	written. Commits, branches and releases are linked only when the checkout has them.

	A release (Cerberus 1.1) and a branch link to their PR when there is one, else to the commit
	or the branch.
	"""
	links = {}
	for name in set(NAMED_COMMIT.findall(text)):
		found = _run('rev-parse', '--verify', '--quiet', f'{name}^{{commit}}').stdout.strip()
		if found:
			links[name] = f'{GITHUB_URL}/commit/{found}'
	releases = {}
	for match in NAMED_RELEASE.finditer(text):
		releases[match[0]] = match[1]
	for name, version in releases.items():
		try:
			ref = release_ref(version)
		except LookupError:
			continue  # not released yet, or not fetched
		links[name] = _change_url(ref)
	for number in NAMED_PULL.findall(text):
		links[f'#{number}'] = f'{GITHUB_URL}/pull/{number}'
	links.update(_branch_links(text))
	return links


def _change_url(ref: str) -> str:
	"""The PR that *ref* merged, from its subject, else the commit."""
	subject = _git('log', '-1', '--format=%s', ref).strip()
	merged = PULL_SUBJECT.search(subject)
	if merged:
		return f'{GITHUB_URL}/pull/{merged[1]}'
	return f'{GITHUB_URL}/commit/{_git("rev-parse", ref).strip()}'


def _branch_links(text: str) -> dict[str, str]:
	"""Links for the remote branches *text* names: the PR whose head is the branch's tip, else the
	branch. A plain word counts only with origin/ before it. master is not linked: it is named in
	most answers, and its page says nothing about them."""
	tips = {}
	refs = _git('for-each-ref', '--format=%(objectname) %(refname:short)', 'refs/remotes/origin')
	for line in refs.splitlines():
		commit, name = line.split(' ', 1)
		if name.startswith('origin/') and name not in ('origin/HEAD', 'origin/master'):
			tips[name.removeprefix('origin/')] = commit
	named = {}
	for word in WORD.findall(text):
		word = word.rstrip('./')
		name = word.removeprefix('origin/')
		if name == word and not BRANCH_MARK.search(word):
			continue
		if name in tips:
			named[word] = name
	if not named:
		return {}
	heads = _pull_heads()
	links = {}
	for word, name in named.items():
		if tips[name] in heads:
			links[word] = f'{GITHUB_URL}/pull/{heads[tips[name]]}'
		else:
			links[word] = f'{GITHUB_URL}/tree/{name}'
	return links


def _pull_heads() -> dict[str, int]:
	"""The newest PR whose head is each commit, from GitHub's pull refs; empty when GitHub is out
	of reach, and the branches link to themselves."""
	try:
		found = _git('ls-remote', 'origin', 'refs/pull/*/head')
	except (RuntimeError, subprocess.TimeoutExpired):
		return {}
	heads: dict[str, int] = {}
	for line in found.splitlines():
		commit, ref = line.split('\t')
		number = int(ref.split('/')[2])
		heads[commit] = max(number, heads.get(commit, 0))
	return heads


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


def export(ref: str) -> str:
	"""The folder holding the Cerberus package at commit *ref*, written into EXPORT_DIR once by
	`git archive` (no wheel is built): the analyst's build that `cerb_client_info` runs in a
	replay. The folder appears whole or not at all."""
	folder = EXPORT_DIR / ref
	if (folder / 'cerberus' / '__init__.py').exists():
		return str(folder)
	partial = folder.with_name(folder.name + '.partial')
	shutil.rmtree(partial, ignore_errors=True)
	partial.mkdir(parents=True)
	archive = partial / 'cerberus.tar'
	_git('archive', '--format=tar', '-o', str(archive), ref, 'cerberus')
	with tarfile.open(archive) as tar:
		tar.extractall(partial, filter='data')
	archive.unlink()
	shutil.rmtree(folder, ignore_errors=True)
	partial.rename(folder)
	return str(folder)


def push_history(until: datetime | None = None) -> list[list[str]]:
	"""Every change of a branch of the Cerberus repo on GitHub (a push, force push, merge, branch
	made or deleted), oldest first, as [time (ISO, UTC), ref, commit after]: GitHub's activity API
	through the gh CLI, GET only.

	Kept in ACTIVITY_FILE. What happened by a time never changes, so the kept history is enough
	when it reaches past *until*; else only what came since it is asked for. Raises when gh cannot
	answer and nothing is kept.
	"""
	kept: list[list[str]] = []
	if ACTIVITY_FILE.exists():
		kept = json.loads(ACTIVITY_FILE.read_text(encoding='utf-8'))
	newest = None
	if kept:
		newest = datetime.fromisoformat(kept[-1][0])
	if newest is not None and until is not None and newest > until:
		return kept
	period = ''
	if newest is not None:
		age = datetime.now(UTC) - newest
		for name, days in ACTIVITY_PERIODS:
			if age < timedelta(days=days):
				period = name
				break
	try:
		fresh = _activity(period)
	except (OSError, RuntimeError, subprocess.TimeoutExpired):
		if kept:
			return kept
		raise
	rows = set()
	for row in kept + fresh:
		rows.add(tuple(row))
	merged = sorted(list(row) for row in rows)  # ISO times in UTC sort as text
	ACTIVITY_FILE.parent.mkdir(parents=True, exist_ok=True)
	partial = ACTIVITY_FILE.with_suffix('.tmp')
	partial.write_text(json.dumps(merged), encoding='utf-8')
	os.replace(partial, ACTIVITY_FILE)  # a crash never leaves a half-written history
	return merged


def _activity(period: str) -> list[list[str]]:
	"""GitHub's activity rows for the Cerberus repo (:func:`push_history`): all of them, or those
	of the last *period* (day, week, month, quarter or year)."""
	url = f'/repos/{GITHUB_REPO}/activity?per_page=100'
	if period:
		url += f'&time_period={period}'
	rows = '.[] | [.timestamp, .ref, .after] | @tsv'
	command = [_gh(), 'api', '--method', 'GET', '--paginate', url, '--jq', rows]
	done = subprocess.run(
		command,
		capture_output=True,
		encoding='utf-8',
		errors='replace',
		timeout=GH_TIMEOUT_S,
		check=False,
	)
	if done.returncode:
		raise RuntimeError(f'gh api failed: {done.stderr.strip()[-300:]}')
	found = []
	for line in done.stdout.splitlines():
		parts = line.split('\t')
		if len(parts) == 3:
			found.append(parts)
	return found


def _gh() -> str:
	"""The gh CLI: DSS_GH, else gh on the PATH, else where its installer puts it."""
	return os.environ.get('DSS_GH') or shutil.which('gh') or str(GH_PATH)


def branches_then() -> dict[str, str] | None:
	"""The remote branches as GitHub held them at the replay's cutoff, {origin/<branch>: commit},
	from its push history (:func:`push_history`), each kept only when this clone has its commit (a
	branch deleted since may be gone). None outside a replay, or when the push history can't be had
	or doesn't reach back that far: git's commit dates then stand in (:func:`_until`)."""
	then = cutoff.when()
	if then is None:
		return None
	key = then.isoformat()
	if key not in _BRANCHES_THEN:
		_BRANCHES_THEN[key] = _branches_at(then)
	return _BRANCHES_THEN[key]


def _branches_at(then: datetime) -> dict[str, str] | None:
	""":func:`branches_then` for the time *then*."""
	try:
		history = push_history(then)
	except (OSError, RuntimeError, ValueError, subprocess.TimeoutExpired):
		return None
	if not history or datetime.fromisoformat(history[0][0]) > then:
		return None
	tips: dict[str, str] = {}
	for moment, ref, after in history:
		if datetime.fromisoformat(moment) > then:
			break
		if not ref.startswith('refs/heads/'):
			continue
		name = 'origin/' + ref.removeprefix('refs/heads/')
		if after == ZERO_SHA:
			tips.pop(name, None)
		else:
			tips[name] = after
	asked = '\n'.join(sorted(set(tips.values()))) + '\n'
	have = set()
	for line in _git('cat-file', '--batch-check', stdin=asked).splitlines():
		parts = line.split()
		if len(parts) > 1 and parts[1] == 'commit':
			have.add(parts[0])
	kept = {}
	for name, commit in tips.items():
		if commit in have:
			kept[name] = commit
	return kept


def _known_then() -> set[str] | None:
	"""Every commit GitHub held at the replay's cutoff (reachable from :func:`branches_then`);
	None when that is not known."""
	tips = branches_then()
	then = cutoff.when()
	if tips is None or then is None:
		return None
	key = then.isoformat()
	if key not in _KNOWN_THEN:
		_KNOWN_THEN[key] = set()
		if tips:
			asked = '\n'.join(sorted(set(tips.values()))) + '\n'
			_KNOWN_THEN[key] = set(_git('rev-list', '--stdin', stdin=asked).split())
	return _KNOWN_THEN[key]


def _branch_revisions() -> tuple[list[str], str | None]:
	"""What `git log` walks for "any remote branch", and its stdin: in a replay the branches as
	GitHub held them then (hundreds, so on stdin), else every remote branch now."""
	tips = branches_then()
	if not tips:
		return ['--remotes'], None
	return ['--stdin'], '\n'.join(sorted(set(tips.values()))) + '\n'


def _master_then() -> str:
	"""origin/master as GitHub held it at the replay's cutoff; origin/master itself outside one,
	or without the push history (:func:`_until` then cuts it by commit dates)."""
	tips = branches_then()
	if tips and tips.get('origin/master'):
		return tips['origin/master']
	return 'origin/master'


def _until() -> list[str]:
	"""The git option that keeps history to the replay's cutoff; none outside a replay."""
	then = cutoff.when()
	if then is None:
		return []
	return [f'--before={then.isoformat(timespec="seconds")}']


def _git(*args: str, stdin: str | None = None) -> str:
	"""Output of one read-only git command in the Cerberus checkout, given *stdin*."""
	return _run(*args, stdin=stdin).stdout


def _run(
	*args: str, cwd: str = '', timeout: float = GIT_TIMEOUT_S, stdin: str | None = None
) -> subprocess.CompletedProcess:
	"""One git command in the Cerberus repo (:func:`repo_dir`) or *cwd*, given *stdin*.
	Only reads, apart from the bot's own clone taking in what it fetches. Exit code 1 means no or
	false."""
	if not cwd:
		cwd = repo_dir()
	done = subprocess.run(
		['git', *args],
		cwd=cwd,
		input=stdin,
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
		raise ValueError(f'{path!r} is not a path inside the repo')
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
