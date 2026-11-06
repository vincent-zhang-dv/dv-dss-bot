"""Replay mode: existing DSS tickets triaged as they stood when they were posted.

`python -m dss replay DSS-1229 ...` finds each ticket's support-request workflow post in the
watched channel (#data-dev-support) and runs a trial (:func:`dss.record.trial`) with every tool
answering as of that post (:mod:`dss.cutoff`):

- Slack: the workflow's post only, none of the replies.
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
import functools
import json
import os
import subprocess
from datetime import datetime, timedelta
from pathlib import Path

from dss import code, cutoff, jira, record, runner, slack, triage
from dss.worker import ROOT

REPLAY_DIR = ROOT / 'out' / 'replay'  # a folder per day; never out/answers, which the bot reads
MAX_CALLS = 30  # tool calls per replay, about the 90th percentile of a triage's turns
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
	analyst = ''
	creator = slack.CREATOR.search(post.get('text', ''))
	if creator and names.name(creator[1]) != creator[1]:
		analyst = names.name(creator[1])  # a name, not a mention, which would notify them
	return {
		'key': key,
		'ts': ts,
		'posted': posted,
		'post': names.plain(post.get('text', '')),
		'link': permalink(client, channel, ts),
		'analyst': analyst,
		'build': build,
		'ref': ref,
		'note': note,
	}


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
	for name in SETTINGS:
		saved[name] = os.environ.pop(name, None)
	os.environ.update(
		DSS_REPLAY_AT=ts,
		DSS_REPLAY_KEY=key,
		DSS_REPLAY_MAX_CALLS=str(MAX_CALLS),
		DSS_THREAD_UNTIL=ts,  # slack_thread shows the post and none of the replies
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
		if ask is None:
			ask = functools.partial(
				runner.run_reply, system=record.system_prompt(), budget=MAX_COST_USD
			)
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
	"""How one replay ended, in a line: its status, cost, tool calls and possible leaks."""
	key = result['key']
	if 'stopped' in result:
		leaks = len(result['leaks_found'])
		return f'{key}: stopped: {result["stopped"]} · {calls(result)} calls · {leaks} leaks'
	found = result['record']
	if found is None or result['problems']:
		ended = f'failed the record check ({len(result["problems"])} problems)'
		leaks = 0
	else:
		ended = f'{found["triage"]["status"]}, settled at {found["triage"]["settled_at"]}'
		if found['replay']['fixed_later']:
			ended += f', fixed later in {len(found["replay"]["fixed_later"])} file(s)'
		leaks = len(found['replay']['leaks']) + len(found['replay']['leaks_found'])
	spent = f'${result["cost_usd"]:.2f} · {calls(result)} calls · {leaks} leaks'
	return f'{key}: {ended} · {spent} · {result["file"]}'


def calls(result: dict) -> int:
	"""The tool calls one replay made, from its trace, without those held back until past issues
	were searched: they read nothing, and don't count toward MAX_CALLS."""
	trace = Path(result['trace'])
	if not trace.exists():
		return 0
	count = 0
	for line in trace.read_text(encoding='utf-8').splitlines():
		if line.strip() and not json.loads(line).get('blocked'):
			count += 1
	return count
