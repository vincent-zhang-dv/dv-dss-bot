"""The replay's cutoff: the `dss` tools answer as of the time a DSS ticket was posted.

`python -m dss replay` (:mod:`dss.replay`) sets DSS_REPLAY_AT (the workflow post's Slack ts) and
DSS_REPLAY_KEY (the ticket) for each triage it runs, and the tools, which inherit claude's
environment, leave out whatever came later. Without them every tool answers as of now. Standard
library only: the Cerberus venv imports :mod:`dss.cerb`, which uses this module.

What a replay hides it hides without saying when it happened: a refusal or a flag never gives the
later time, which would tell Claude when a fix came.
"""

import os
import re
from datetime import datetime

NAME_TIME = '%Y-%m-%d_%H-%M-%S'  # local, as Cerberus names its logs (triage.TIME_FORMAT)
SHOWN_TIME = '%Y-%m-%d %H:%M'
DATE = re.compile(r'\b20[0-9]{2}-[01][0-9]-[0-3][0-9]\b')
NAMED_DATE = re.compile(r'(?<![0-9])(20[0-9]{2})[-_]?([01][0-9])(?:[-_]?([0-3][0-9]))?(?![0-9])')
DSS_KEY = re.compile(r'\bDSS-([0-9]+)\b')
MAX_LEAKS = 5  # per tool call
WORD = re.compile(r'[a-z0-9]+')
# Words a Jira or Confluence text search ignores, or reads as operators
STOP_WORDS = {'and', 'or', 'not', 'the', 'an', 'of', 'in', 'on', 'to', 'is', 'for', 'with', 'by'}
STEM_CHARS = 4  # a search word matches the words that start as it does: tallies, tally
# Tools whose answer is the replayed ticket itself, so its key there is no leak
OWN_TICKET_TOOLS = {'slack_thread', 'jira_get_issue'}
# Set only by a replay, for the tools it starts; every other command drops them (dss.__main__)
SETTINGS = ('DSS_REPLAY_AT', 'DSS_REPLAY_KEY', 'DSS_REPLAY_MAX_CALLS')


def when() -> datetime | None:
	"""The cutoff in local time; None outside a replay."""
	ts = os.environ.get('DSS_REPLAY_AT', '')
	if not ts:
		return None
	return datetime.fromtimestamp(float(ts)).astimezone()


def key() -> str:
	"""The replayed ticket's key; empty outside a replay."""
	return os.environ.get('DSS_REPLAY_KEY', '')


def after(moment: datetime) -> bool:
	"""Whether *moment* (with its time zone) came after the cutoff; False outside a replay."""
	limit = when()
	return limit is not None and moment > limit


def name_time() -> str:
	"""The cutoff as Cerberus names a log's start time (local); empty outside a replay."""
	limit = when()
	if limit is None:
		return ''
	return limit.strftime(NAME_TIME)


def shown(moment: datetime) -> str:
	"""*moment* in local time, as the tools show it."""
	return moment.astimezone().strftime(SHOWN_TIME)


def refusal(what: str) -> str:
	"""Why a replay does not show *what*: it came after the ticket (never when)."""
	text = f'{what} came after the ticket'
	limit = when()
	if limit is not None:
		text += f'; this replay sees only what existed at {shown(limit)}'
	return text


def names_key(text: str) -> bool:
	"""Whether *text* (a file name or a path) names the replayed ticket: nothing older can."""
	return bool(key()) and re.search(rf'\b{re.escape(key())}\b', text, re.IGNORECASE) is not None


def named_later(name: str) -> bool:
	"""Whether the file or folder *name* says it was made after the cutoff: it names the replayed
	ticket, or a date (2026-11-05, 20261105 or 2026-11) after the cutoff's day or month. False
	outside a replay."""
	limit = when()
	if limit is None:
		return False
	if names_key(name):
		return True
	for year, month, day in NAMED_DATE.findall(name):
		if day and f'{year}-{month}-{day}' > limit.strftime('%Y-%m-%d'):
			return True
		if not day and f'{year}-{month}' > limit.strftime('%Y-%m'):
			return True
	return False


def search_terms(phrase: str) -> list[str]:
	"""The words of a text search *phrase* as stems (lower case, at most STEM_CHARS long), without
	the words a search ignores: what :func:`matches` looks for."""
	terms = []
	for word in WORD.findall(phrase.lower()):
		if len(word) > 1 and word not in STOP_WORDS:
			terms.append(word[:STEM_CHARS])
	return terms


def matches(text: str, phrases: list[list[str]]) -> bool:
	"""Whether *text* has every term of at least one of *phrases* (:func:`search_terms`), each as
	the start of a word: a loose stand-in for Jira's and Confluence's own matching, used on text as
	it was at the cutoff. True when there is no phrase to match."""
	starts = set()  # every word's first 1 to STEM_CHARS characters, so a term is one lookup
	for word in WORD.findall(text.lower()):
		for length in range(1, STEM_CHARS + 1):
			starts.add(word[:length])
	for terms in phrases:
		found = True
		for term in terms:
			if term not in starts:
				found = False
		if found:
			return True
	return not phrases


def leaks(tool: str, arguments: dict, text: str) -> list[str]:
	"""What in one tool answer *text* may come from after the cutoff: the replayed ticket's key,
	later DSS tickets (keys are numbered in order, so nothing older names them) and dates after the
	cutoff's day. Empty outside a replay, and for the ticket itself: its post and fields name its
	key and the dates the analyst asks for."""
	limit = when()
	if limit is None:
		return []
	if tool in OWN_TICKET_TOOLS and arguments.get('key', key()) == key():
		return []
	found = []
	if names_key(text):
		found.append(f'names {key()}')
	replayed = DSS_KEY.fullmatch(key())
	if replayed:
		later_keys = set()
		for number in DSS_KEY.findall(text):
			if int(number) > int(replayed[1]):
				later_keys.add(int(number))
		if later_keys:
			shown_keys = ', '.join(f'DSS-{number}' for number in sorted(later_keys)[:MAX_LEAKS])
			found.append(f'names later tickets: {shown_keys}')
	day = limit.strftime('%Y-%m-%d')
	later = sorted({date for date in DATE.findall(text) if date > day})
	if later:
		found.append(f'dates after the ticket: {", ".join(later[:MAX_LEAKS])}')
	return found
