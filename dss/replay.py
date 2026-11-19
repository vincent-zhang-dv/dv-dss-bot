"""Replay mode: existing DSS tickets triaged as they stood when they were posted.

`python -m dss replay DSS-1229 ...` finds each ticket's support-request workflow post in the
watched channel (#data-dev-support) and runs a trial (:func:`dss.record.trial`) with every tool
answering as of that post (:mod:`dss.cutoff`):

- Slack: the ticket's thread as raised: the request, the workflow's "Screenshot of error:" reply
  with the analyst's screenshot, and whatever the analyst added, up to the first reply from
  anyone else (:func:`dss.thread._as_raised`).
- Cerberus code: the analyst's build, from their last session before the post, else the newest
  release by then; git history is what GitHub held then (its push history), else what was
  committed by then. `cerb_client_info` runs that build from git.
- Jira: each ticket as it stood then (from its changelog), with none of the replayed ticket's
  comments; later tickets are not found, and a search hit is kept only if its text then matches.
- The project share: later sessions and log lines are left out; a file changed since is read as
  it is now and says so, and the answer notes a fix it shows (`replay.fixed_later`).
- Confluence: pages as they were then, searched by their text then.
- The prompt: known causes learned only from this ticket or later ones, and lines that name it or
  a later ticket, are left out.

The tools stop answering after MAX_CALLS calls, and claude stops at MAX_COST_USD. Code looks in
every tool answer for the ticket's key, later tickets and later dates, and the post names each
possible leak Claude saw, with the call it came through, and how many code flagged. Results are saved in REPLAY_DIR/<date>/; with *post_test*, the post and
its thread also go to #data-dev-support-assist-test, never to #data-dev-support. Without *go*
nothing runs: each ticket's post, cutoff and Cerberus commit are shown.
"""

import contextlib
import json
import os
import subprocess
from datetime import datetime, timedelta
from pathlib import Path

from dss import code, cutoff, jira, record, slack, thread, triage
from dss.worker import ROOT

REPLAY_DIR = ROOT / 'out' / 'replay'  # a folder per day; never out/answers, which the bot reads
MAX_CALLS = 40  # tool calls per replay, then the tools tell Claude to answer with what it has
MAX_COST_USD = 1.50  # claude stops a replay here: over 5 times a median triage
TEST_CHANNEL = 'C0CF1J345EY'  # #data-dev-support-assist-test
POST_WINDOW = timedelta(hours=1)  # the workflow post is looked for this long around the ticket
SETTINGS = (*cutoff.SETTINGS, 'DSS_THREAD_UNTIL', 'DV_CERB_REF')
POST_CHARS = 100  # of the workflow post, in the plan


def main(
	keys: list[str], go: bool = False, post_test: bool = False, client=None, ask=None, say=print
) -> list[dict]:
	"""Replay each ticket in *keys*, or with *go* False only show the plan; what was saved.

	*client* is a slack_sdk WebClient with the bot's token and *ask* is Claude (both faked in
	tests); *say* gets each line of progress.
	"""
	if client is None:
		from slack_sdk import WebClient

		client = WebClient(token=os.environ.get('DSS_SLACK_BOT_TOKEN', ''))
	to = ''
	if post_test:
		to = test_channel()
		if to in (slack.watched_channel(), slack.CHANNEL):  # checked before anything runs
			raise ValueError('replays never go to #data-dev-support or the watched channel')
	names = slack.Names(client)
	for note in code.refresh():
		say(f'note: {note}')
	try:
		code.push_history()  # brought up to date once; each replay's tools read it from disk
	except (OSError, RuntimeError, subprocess.TimeoutExpired) as error:
		say(f'note: no GitHub push history, so commit dates stand in for push times: {error}')
	folder = REPLAY_DIR / f'{datetime.now():%Y-%m-%d}'
	results = []
	for key in dict.fromkeys(keys):  # each once, in order
		try:
			plan = prepare(key, client, names)
		except Exception as error:  # noqa: BLE001 - one ticket that can't be found skips only itself
			say(f'{key}: cannot replay: {error}')
			continue
		say(planned(plan))
		if not go:
			continue
		result = replay(plan, folder, ask)
		try:
			result = with_developer_time(result, client, plan)
		except Exception as error:  # noqa: BLE001 - the replay itself is saved already
			say(f'{key}: developer time not read: {error}')
		results.append(result)
		say(outcome(result))
		if to and 'dm' in result:
			posted = slack.post(client, to, result['dm'])
			slack.post(client, to, result['thread'], thread_ts=posted)
	if not go:
		say('dry run: add --go to run them; each is a real triage under your Claude login')
	return results


def golden_keys(path: Path) -> list[str]:
	"""The ticket keys of a golden set (`out/eval/golden.jsonl`, one JSON row per line)."""
	keys = []
	for line in path.read_text(encoding='utf-8').splitlines():
		if line.strip():
			keys.append(json.loads(line)['key'])
	return keys


def test_channel() -> str:
	"""Where --post-test posts: DSS_SLACK_TEST_CHANNEL, else #data-dev-support-assist-test."""
	return os.environ.get('DSS_SLACK_TEST_CHANNEL') or TEST_CHANNEL


def prepare(key: str, client, names: slack.Names) -> dict:
	"""What a replay of *key* needs before Claude runs: the workflow post, the cutoff, the analyst
	and the Cerberus build their last session ran, with the commit the code tools will read."""
	if not jira.ISSUE_KEY.fullmatch(key):
		raise ValueError(f'{key!r} is not an issue key like DSS-1234')
	created = datetime.strptime(
		jira.get_issue(key, {'created': 'created'})['created'], jira.JIRA_TIME
	)
	channel = slack.watched_channel()
	post = find_post(client, channel, key, created)
	if post is None:
		raise LookupError(f'no workflow post "created {key}" in {channel} within an hour of it')
	ts = post['ts']
	posted = datetime.fromtimestamp(float(ts)).strftime(triage.TIME_FORMAT)
	note = ''
	with replaying(key, ts):
		ticket, _ = record.build_ticket(key, posted)
		build = ticket['cerberus_version']
		ref = code.release_then()[1]  # prod then, when the analyst's build is not known
		if not build:
			note = 'no Cerberus session of the reporter before the ticket: the newest release then'
		else:
			try:
				ref = code.version_ref(build)
			except (LookupError, ValueError) as error:  # a build no commit names
				note = f'the code tools read the newest release by then, {ref}: {error}'
	analyst, raiser = '', ''
	creator = slack.CREATOR.search(post.get('text', ''))
	if creator:
		raiser = creator[1]
		if names.name(raiser) != raiser:
			analyst = names.name(raiser)  # a name, not a mention, which would notify them
	return {
		'key': key,
		'ts': ts,
		'posted': posted,
		'post': names.plain(post.get('text', '')),
		'link': permalink(client, channel, ts),
		'analyst': analyst,
		'raiser': raiser,
		'build': build,
		'ref': ref,
		'note': note,
	}


def developer_time(messages: list[dict], raiser: str) -> dict:
	"""How developers answered a ticket's whole thread *messages*: their first and last reply (local
	time), the minutes from the post to their first reply (`response_minutes`) and from their first
	reply to their last (`solving_minutes`, the time they spent on it). Developers are the people
	who replied other than the analyst who raised it (*raiser*); the workflow's messages don't
	count. The times are None when nobody answered."""
	replies = []
	for message in messages[1:]:
		person = message.get('user') and not message.get('bot_id')
		if person and message['user'] != raiser:
			replies.append(float(message['ts']))
	found: dict = {
		'replies': len(replies),
		'first_reply': None,
		'last_reply': None,
		'response_minutes': None,
		'solving_minutes': None,
	}
	if not messages or not replies:
		return found
	posted, first, last = float(messages[0]['ts']), min(replies), max(replies)
	found['first_reply'] = datetime.fromtimestamp(first).astimezone().isoformat(timespec='seconds')
	found['last_reply'] = datetime.fromtimestamp(last).astimezone().isoformat(timespec='seconds')
	found['response_minutes'] = round((first - posted) / 60, 1)
	found['solving_minutes'] = round((last - first) / 60, 1)
	return found


def with_developer_time(result: dict, client, plan: dict) -> dict:
	"""*result* with `developers` (:func:`developer_time`) from the ticket's whole Slack thread,
	read only now that Claude has answered, and saved to its file again."""
	messages = thread._replies(client, slack.watched_channel(), plan['ts'])
	result['developers'] = developer_time(messages, plan.get('raiser', ''))
	Path(result['file']).write_text(json.dumps(result, indent=1), encoding='utf-8')
	return result


def find_post(client, channel: str, key: str, created: datetime) -> dict | None:
	"""The support-request workflow's "created <key>" post in *channel*, looked for within
	POST_WINDOW of the Jira ticket's *created* time; None when there is none."""
	history = client.conversations_history(
		channel=channel,
		oldest=f'{(created - POST_WINDOW).timestamp():.6f}',
		latest=f'{(created + POST_WINDOW).timestamp():.6f}',
		limit=200,
	)
	for message in history['messages']:
		ticket = slack.TICKET.search(message.get('text', ''))
		in_channel = message.get('thread_ts', message['ts']) == message['ts']  # not a reply
		if message.get('bot_id') and ticket and ticket[1] == key and in_channel:
			return message
	return None


def permalink(client, channel: str, ts: str) -> str:
	"""The Slack link of the post *ts*, which the replay's headline links to."""
	try:
		return client.chat_getPermalink(channel=channel, message_ts=ts)['permalink']
	except Exception:  # noqa: BLE001 - a link is a convenience
		return f'https://slack.com/archives/{channel}/p{ts.replace(".", "")}'


@contextlib.contextmanager
def replaying(key: str, ts: str, ref: str = ''):
	"""The settings that make the tools answer as of the post *ts* (:mod:`dss.cutoff`), with the
	code tools at *ref* when given; the previous settings come back after."""
	saved = {}
	for name in SETTINGS:  # DSS_THREAD_UNTIL is cleared too: a replay's thread is cut as raised
		saved[name] = os.environ.pop(name, None)
	os.environ.update(
		DSS_REPLAY_AT=ts,
		DSS_REPLAY_KEY=key,
		DSS_REPLAY_MAX_CALLS=str(MAX_CALLS),
	)
	if ref:
		os.environ['DV_CERB_REF'] = ref
	try:
		yield
	finally:
		for name, value in saved.items():
			os.environ.pop(name, None)
			if value is not None:
				os.environ[name] = value


def replay(plan: dict, folder: Path, ask=None) -> dict:
	"""One replay of a *plan* (:func:`prepare`), saved in *folder*. A run claude stopped (over
	MAX_COST_USD) or that failed is saved too, with `stopped` saying why."""
	key, ts = plan['key'], plan['ts']
	run_at = datetime.now().strftime(triage.TIME_FORMAT)
	trace = folder / f'{key}_{run_at}.jsonl'
	when = datetime.fromtimestamp(float(ts)).astimezone()
	label = f'Replay of {key} as posted {cutoff.shown(when)}'
	label += f' · Cerberus {plan["build"] or "unknown"} at {plan["ref"]}'
	facts = {
		'cutoff': when.isoformat(timespec='seconds'),
		'build': plan['build'],
		'code_ref': plan['ref'],
	}
	with replaying(key, ts, plan['ref']):
		try:
			return record.trial(
				key,
				plan['posted'],
				plan['post'],
				ask,
				thread_ts=ts,
				label=label,
				ticket_link=plan['link'],
				analyst=plan['analyst'],
				replay=facts,
				folder=folder,
				trace=trace,
				budget=MAX_COST_USD,
			)
		except Exception as error:  # noqa: BLE001 - a stopped replay is a result too
			why = triage.explain(error)
	folder.mkdir(parents=True, exist_ok=True)
	path = folder / f'{key}_{run_at}.json'
	stopped = {
		'key': key,
		'posted': plan['posted'],
		'stopped': why,
		**facts,
		'leaks_found': record.traced_leaks(trace),
		'trace': str(trace),
		'file': str(path),
	}
	path.write_text(json.dumps(stopped, indent=1), encoding='utf-8')
	return stopped


def planned(plan: dict) -> str:
	"""One ticket's plan in a line: post time, build and commit, and the post's start."""
	when = cutoff.shown(datetime.fromtimestamp(float(plan['ts'])).astimezone())
	text = ' '.join(plan['post'].split())[:POST_CHARS]
	line = f'{plan["key"]}: posted {when}, Cerberus {plan["build"] or "unknown"} at {plan["ref"]}'
	line += f' | {text}'
	if plan['note']:
		line += f'\n  note: {plan["note"]}'
	return line


def outcome(result: dict) -> str:
	"""How one replay ended, in a line: its status, the bot's time and cost, tool calls, possible
	leaks, and the developers' time on the ticket (:func:`developer_time`)."""
	key = result['key']
	people = _developers_line(result.get('developers'))
	if 'stopped' in result:
		leaks = len(result['leaks_found'])
		stopped = f'{key}: stopped: {result["stopped"]} · {calls(result)} calls · {leaks} leaks'
		return stopped + people
	found = result['record']
	if found is None or result['problems']:
		ended = f'failed the record check ({len(result["problems"])} problems)'
		leaks = 0
	else:
		ended = f'{found["triage"]["status"]}, settled at {found["triage"]["settled_at"]}'
		if found['replay']['fixed_later']:
			ended += f', fixed later in {len(found["replay"]["fixed_later"])} file(s)'
		leaks = len(found['replay']['leaks']) + len(found['replay']['leaks_found'])
	bot = f'bot {_duration(result["seconds"] / 60)}, ${result["cost_usd"]:.2f}'
	spent = f'{bot} · {calls(result)} calls · {leaks} leaks'
	return f'{key}: {ended} · {spent}{people} · {result["file"]}'


def _developers_line(developers: dict | None) -> str:
	"""The developers' time on a ticket for :func:`outcome`; empty when it wasn't read."""
	if not developers:
		return ''
	if developers['solving_minutes'] is None:
		return ' · no developer replied'
	solving = _duration(developers['solving_minutes'])
	return f' · developers {solving} (first reply {_duration(developers["response_minutes"])} in)'


def _duration(minutes: float) -> str:
	"""*minutes* as people say it: 40 s, 12 min, 2 h 5 min, 3 d 4 h."""
	if minutes < 1:
		return f'{round(minutes * 60)} s'
	if minutes < 60:
		return f'{round(minutes)} min'
	if minutes < 24 * 60:
		hours, rest = divmod(round(minutes), 60)
		return f'{hours} h {rest} min'
	days, rest = divmod(round(minutes / 60), 24)
	return f'{days} d {rest} h'


def calls(result: dict) -> int:
	"""The tool calls Claude made in one replay, from its trace, without those held back until
	past issues were searched: they read nothing, and don't count toward MAX_CALLS."""
	return len(record.traced_tools(Path(result['trace'])))
