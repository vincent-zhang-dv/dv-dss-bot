"""DSS tickets the developers answered in Slack but nobody reviewed: `python -m dss selflearn`.

The weekly learning run (the dss-learn skill) reviews these from the developers' own words: the
replies in the ticket's thread in #data-dev-support and under the bot's post in
DSS_SLACK_POST_CHANNEL. *gather* takes each ticket the bot answered in the last LOOKBACK_DAYS (its
saved answers in out/answers), reads both threads with the bot token (GETs only), and writes
`<export>/self/DSS-<n>.json` when the ticket is due: someone other than the analyst who raised it
replied in the DSS thread, neither thread has had a reply for QUIET_DAYS, and no person rated it on
the review page. A self-learning review is written again when replies came after it. *check*
validates the reviews the session writes from those files (`<export>/self/DSS-<n>.review.json`)
before they go to the page. Writes local files only.
"""

import json
import os
import re
import time
from datetime import datetime
from pathlib import Path

from slack_sdk import WebClient

from dss import slack, thread
from dss.learn import ACTS, SECTIONS, SELF, TAGS
from dss.worker import ROOT

ANSWERS = ROOT / 'out' / 'answers'
LOOKBACK_DAYS = 28
QUIET_DAYS = 2
POST_KEY = re.compile(r'(DSS-\d+) · ')  # how the bot's post in the developers' channel starts
RATINGS = ('correct', 'partial', 'incorrect')
LINK = 'https://darkvision.slack.com/archives/{channel}/p{digits}'


def gather(export: Path, now: float | None = None, client=None) -> list[str]:
	"""Write a file for each due ticket to `<export>/self/`; one line per ticket on what was done."""
	now = time.time() if now is None else now
	client = client or WebClient(token=thread._token())
	channel = os.environ.get('DSS_SLACK_POST_CHANNEL', '')
	if not channel:
		raise RuntimeError('set DSS_SLACK_POST_CHANNEL: the channel the bot posts its analyses in')
	since = now - LOOKBACK_DAYS * 86400
	reviews = read_reviews(export)
	posts = bot_posts(client, channel, since)
	names = slack.Names(client)
	out = export / 'self'
	out.mkdir(parents=True, exist_ok=True)
	lines = []
	for record in answered(since):
		key = record['key']
		review = reviews.get(key)
		if by_people(review):
			lines.append(f'{key}: skipped, a person reviewed it')
			continue
		if key not in posts:
			lines.append(f'{key}: skipped, no bot post in the developers channel')
			continue
		dss = said(client, names, slack.CHANNEL, record['thread_ts'], raised_by=True)
		bot = [line for line in said(client, names, channel, posts[key]) if line['role'] != 'bot']
		replies = [line for line in dss + bot if line['role'] in ('analyst', 'helper')]
		latest = max((float(line['ts']) for line in replies), default=0.0)
		if not any(line['role'] == 'helper' for line in dss):
			lines.append(f'{key}: skipped, nobody but the analyst replied in the DSS thread')
		elif now - latest < QUIET_DAYS * 86400:
			lines.append(f'{key}: skipped, a reply came in the last {QUIET_DAYS} days')
		elif review and review.get('source') == SELF and latest <= iso_ts(review.get('updatedAt')):
			lines.append(f'{key}: skipped, learned already and no reply since')
		else:
			bundle = {
				'key': key,
				'run': posts[key],
				'links': {
					'dss': link(slack.CHANNEL, record['thread_ts']),
					'bot': link(channel, posts[key]),
				},
				'bot_saw_until': record['posted'],
				'bot_post': record.get('dm', ''),
				'bot_thread': record.get('thread', ''),
				'dss_thread': dss,
				'developers_channel_thread': bot,
				'review_before': review,
			}
			path = out / f'{key}.json'
			path.write_text(json.dumps(bundle, ensure_ascii=False, indent=1), encoding='utf-8')
			lines.append(f'{key}: due, {path}')
	return lines


def read_reviews(export: Path) -> dict[str, dict]:
	"""The page's shared reviews in *export*, by ticket key; none when it has no reviews folder."""
	found = {}
	for path in sorted((export / 'reviews').glob('DSS-*.json')):
		body = json.loads(path.read_text(encoding='utf-8'))
		if isinstance(body, dict) and body.get('key') == path.stem:
			found[path.stem] = body
	return found


def answered(since: float) -> list[dict]:
	"""The bot's trial records of the DSS tickets posted after *since*, oldest first. Answers
	saved before the bot posted checked records have no trial file and are left out."""
	records = []
	for path in sorted(ANSWERS.glob(f'{slack.CHANNEL}_*.json')):
		if float(path.stem.split('_', 1)[1]) < since:
			continue
		trial = Path(json.loads(path.read_text(encoding='utf-8')).get('trial') or '')
		if trial.is_file():
			record = json.loads(trial.read_text(encoding='utf-8'))
			if record.get('thread_ts'):
				records.append(record)
	return records


def bot_posts(client, channel: str, since: float) -> dict[str, str]:
	"""The ts of the bot's newest post of each DSS ticket in *channel* since *since*, by key."""
	bot = client.api_call('auth.test', http_verb='GET')['user_id']
	posts: dict[str, str] = {}
	# Slack's history call missed messages for a fractional oldest; whole seconds are reliable
	options = {'channel': channel, 'oldest': str(int(since)), 'limit': 200}
	while True:
		page = client.conversations_history(**options)
		for message in page['messages']:  # newest first
			key = POST_KEY.match(message.get('text', ''))
			if key and message.get('user') == bot:
				posts.setdefault(key[1], message['ts'])
		cursor = (page.get('response_metadata') or {}).get('next_cursor')
		if not cursor:
			return posts
		options['cursor'] = cursor


def said(client, names, channel: str, ts: str, raised_by: bool = False) -> list[dict]:
	"""Thread *ts* of *channel* as {ts, time, who, role, text}. The role is 'bot' for apps and
	workflows, 'analyst' for who raised the ticket (when *raised_by*: the @mention in the
	workflow's "created DSS-…" post), else 'helper', usually a data developer."""
	messages = thread._replies(client, channel, ts)
	raiser = ''
	if raised_by and messages:
		creator = slack.CREATOR.search(messages[0].get('text', ''))
		raiser = creator[1] if creator else ''
	lines = []
	for message in messages:
		role = 'helper'
		if message.get('bot_id') or not message.get('user'):
			role = 'bot'
		elif message['user'] == raiser:
			role = 'analyst'
		lines.append(
			{
				'ts': message['ts'],
				'time': datetime.fromtimestamp(float(message['ts'])).strftime(thread.SHOWN_TIME),
				'who': thread._author(message, names),
				'role': role,
				'text': names.plain(message.get('text', '')),
			}
		)
	return lines


def by_people(review: dict | None) -> bool:
	"""Whether a person, not the weekly run, rated, answered or submitted *review*."""
	if not review:
		return False
	marks = [
		rating.get('by') for rating in review.get('sections', {}).values() if rating.get('rating')
	]
	overall = review.get('overall') or {}
	if overall.get('act') or overall.get('harmful'):
		marks.append(overall.get('by'))
	if review.get('submittedAt'):
		marks.append(review.get('submittedBy'))
	return any(mark != SELF for mark in marks)


def iso_ts(value: str | None) -> float:
	"""An ISO time from the page as a Slack-style epoch; 0 when missing."""
	return datetime.fromisoformat(value).timestamp() if value else 0.0


def link(channel: str, ts: str) -> str:
	return LINK.format(channel=channel, digits=ts.replace('.', ''))


def check(export: Path) -> list[str]:
	"""What is wrong with each `<export>/self/DSS-<n>.review.json`; one line per problem."""
	found = []
	for path in sorted((export / 'self').glob('DSS-*.review.json')):
		key = path.name.split('.')[0]
		bundle = export / 'self' / f'{key}.json'
		run = json.loads(bundle.read_text(encoding='utf-8'))['run'] if bundle.exists() else None
		review = json.loads(path.read_text(encoding='utf-8'))
		found += [f'{key}: {problem}' for problem in problems(review, key, run)]
	return found


def problems(review: dict, key: str, run: str | None) -> list[str]:
	"""Why *review* may not go to the page as the weekly run's review of *key*; [] when it may.
	The page asks the same of a person's rating (review/dss-bot-review.html sectionGap)."""
	found = []
	if review.get('key') != key or run is None or review.get('run') != run:
		found.append(f'key and run must be {key} and its bundle run {run}')
	if review.get('source') != SELF or review.get('by') != SELF:
		found.append(f'source and by must be {SELF!r}')
	if review.get('submittedAt') or review.get('submittedBy'):
		found.append('a self-learning review is never submitted: a person submits')
	if not (review.get('basis') or '').strip():
		found.append('basis must say which replies the review rests on')
	for section, rating in (review.get('sections') or {}).items():
		if section not in SECTIONS:
			found.append(f'{section}: not a section of the page')
		if rating.get('rating') not in RATINGS:
			found.append(f'{section}: rating must be one of {", ".join(RATINGS)}')
		if rating.get('by') != SELF:
			found.append(f'{section}: by must be {SELF!r}')
		unknown = sorted(set(rating.get('tags') or []) - set(TAGS))
		if unknown:
			found.append(f'{section}: unknown reasons {", ".join(unknown)}')
		if rating.get('rating') in ('partial', 'incorrect'):
			if not (rating.get('tags') or (rating.get('why') or '').strip()):
				found.append(f'{section}: a {rating["rating"]} rating needs a reason')
			if not (rating.get('should') or '').strip():
				found.append(f'{section}: a {rating["rating"]} rating needs what it should say')
	overall = review.get('overall') or {}
	if overall:
		if overall.get('act') not in (*ACTS, None):
			found.append(f'overall: act must be one of {", ".join(ACTS)}')
		if overall.get('by') != SELF:
			found.append(f'overall: by must be {SELF!r}')
		if overall.get('harmful') and not (overall.get('note') or '').strip():
			found.append('overall: say what harm it would do')
	return found
