"""`vizapp_*` tools: VizApp's code and release notes (DVCode), read-only through GitHub.

VizApp writes most of what Cerberus reads (dvcalipers, bookmarks, exported tables), so a problem in
that data can start in VizApp. Its code is under Hades/Src/dv/apps/desktop/visualizer and its
release notes, by version, in Hades/CHANGELOG.md. DVCode is over 4 GB, too big for the bot to
clone, so the tools ask GitHub's API through the gh CLI, GET only. In a replay, reads are at
master's newest commit before the cutoff (:func:`ref`); GitHub's code search sees today's master
only.
"""

import subprocess
from urllib.parse import quote

from dss import code, cutoff

REPO = 'darkvisiontech/DVCode'
REF = 'master'
MAX_RESULTS = 30  # files per search answer
TIMEOUT_S = 60
SEARCH_ROWS = (  # one line per file: path, tab, its matching fragments
	r'.items[] | .path + "\t" + '
	r'([.text_matches[]?.fragment] | join(" ... ") | gsub("\\s+"; " "))'
)
# Sent with each VizApp search and read, where VizApp may turn out to be the cause
SUPPORT = (
	'If VizApp is the cause, the analyst raises it in #dh-ask-support (Mon-Fri 9:00-17:00), '
	'otherwise in the after-hours channel from '
	'https://darkvision.slack.com/archives/C0793LJ1KDM/p1748290762872579.'
)


def search(text: str) -> str:
	"""DVCode files on master containing *text*, each with its matching fragments."""
	query = quote(f'{text} repo:{REPO}')
	url = f'search/code?q={query}&per_page={MAX_RESULTS}'
	found = _api(url, accept='text-match+json', jq=SEARCH_ROWS)
	title = f"DVCode files containing {text!r} (GitHub code search of today's {REF})"
	if cutoff.when() is not None:
		title += '; read a match with vizapp_read to see it as it was at the post'
	return code._capped(title, found.splitlines())


def read(path: str, start: int = 1, end: int = 0, contains: str = '') -> str:
	"""Lines *start* to *end* of one DVCode file (*end* 0 means the last line); with *contains*,
	only the lines containing it."""
	at = ref()
	url = f'repos/{REPO}/contents/{quote(code._checked(path))}?ref={at}'
	lines = _api(url, accept='raw').splitlines()
	if contains:
		found = [f'{number}\t{line}' for number, line in enumerate(lines, 1) if contains in line]
		return code._capped(f'{path} at {at}, lines containing {contains!r}', found)
	start = max(start, 1)
	if not end:
		end = len(lines)
	end = min(end, start + code.MAX_LINES - 1, len(lines))
	numbered = [f'{number}\t{lines[number - 1]}' for number in range(start, end + 1)]
	return f'{path} at {at}, lines {start}-{end} of {len(lines)}:\n' + '\n'.join(numbered)


def ref() -> str:
	"""The commit the tools read: REF, or in a replay master's newest commit before the cutoff."""
	then = cutoff.when()
	if then is None:
		return REF
	until = quote(then.isoformat(timespec='seconds'))
	url = f'repos/{REPO}/commits?sha={REF}&until={until}&per_page=1'
	found = _api(url, jq='.[0].sha // ""').strip()
	if not found:
		raise LookupError(cutoff.refusal('every DVCode commit'))
	return found[:10]


def _api(url: str, accept: str = '', jq: str = '') -> str:
	"""Output of one GET of GitHub's API through gh; *accept* names a media type such as `raw`."""
	command = [code._gh(), 'api', '--method', 'GET', url]
	if accept:
		command += ['-H', f'Accept: application/vnd.github.{accept}']
	if jq:
		command += ['--jq', jq]
	done = subprocess.run(
		command,
		capture_output=True,
		encoding='utf-8',
		errors='replace',
		timeout=TIMEOUT_S,
		check=False,
	)
	if done.returncode:
		raise RuntimeError(f'gh api failed: {done.stderr.strip()[-300:]}')
	return done.stdout


def register(server) -> None:
	@server.tool()
	def vizapp_search(text: str) -> str:
		"""DVCode files (VizApp, which writes the dvcalipers and bookmarks Cerberus reads, and its
		release notes) containing *text*, a word or a "quoted phrase", with the matching lines.
		GitHub code search of master."""
		return f'{search(text)}\n\n{SUPPORT}'

	@server.tool()
	def vizapp_read(path: str, start: int = 1, end: int = 0, contains: str = '') -> str:
		"""Lines *start*-*end* of one DVCode file (at most 200), or with *contains* only the lines
		containing it. VizApp's code is under Hades/Src/dv/apps/desktop/visualizer; its release
		notes by version are Hades/CHANGELOG.md."""
		return f'{read(path, start, end, contains)}\n\n{SUPPORT}'
