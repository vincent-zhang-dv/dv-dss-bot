"""DSS triage bot in Slack #data-dev-support: each new ticket is analysed for the reviewer.

When a support-request workflow posts "created DSS-…" in the channel, Claude triages the ticket
(:mod:`dss.triage`) and the analysis goes to the reviewer's DM only; nothing is posted in the
channel. The triage starts TRIAGE_DELAY_S after the post, so the thread it reads
(`slack_thread`) holds the analyst's screenshots. The bot runs on the reviewer's PC under their
`claude` login (Socket Mode: no public URL). On start it also triages the tickets of the last
CATCH_UP_S that have no saved answer, because Slack does not resend events missed while the bot
was down.

	set DSS_SLACK_BOT_TOKEN=xoxb-...  DSS_SLACK_APP_TOKEN=xapp-...  DSS_SLACK_REVIEWER=U0...
	python -m dss slack

Create the Slack app from `slack_manifest.yaml`, as its own app: Slack hands each event to one
of an app's open connections, so two bots sharing an app would each miss events.

Copied from dv-auto-setup-agent `assistant/slack.py` (commit b3f182e), triage part only.
"""

import logging
import os
import re
import threading
import time
from datetime import datetime

from dss import runner
from dss.store import AnswerStore
from dss.triage import TIME_FORMAT, explain, triage
from dss.worker import ROOT

CHANNEL = 'C0793LJ1KDM'  # #data-dev-support
TICKET = re.compile(r'created (DSS-\d+)')  # what the support-request workflows post
CATCH_UP_S = 24 * 3600  # on start, triage tickets posted this recently that were missed
TRIAGE_DELAY_S = 300  # the analyst's screenshots and first replies follow the post within minutes
SEEN_S = 600  # an event seen this recently is a redelivery; Slack resends unacknowledged events
BLOCK_CHARS = 2900  # Slack's section text limit is 3000
MAX_BLOCKS = 50  # Slack's limit per message
MAX_SECTIONS = 45  # leaves room for the footer
ENV = ('DSS_SLACK_BOT_TOKEN', 'DSS_SLACK_APP_TOKEN', 'DSS_SLACK_REVIEWER')

log = logging.getLogger(__name__)


class Names:
	"""Slack markup as plain text, asking Slack for each user's display name once.

	*client* is a slack_sdk WebClient (faked in tests).
	"""

	def __init__(self, client) -> None:
		self.client = client
		self._names: dict[str, str] = {}

	def plain(self, text: str) -> str:
		"""Slack markup to plain text: user/channel mentions to names, links to their URLs."""
		text = re.sub(r'<@([A-Z0-9]+)>', lambda match: '@' + self.name(match[1]), text)
		text = re.sub(r'<#[A-Z0-9]+\|([^>]*)>', r'#\1', text)
		text = re.sub(r'<(https?://[^|>]+)\|([^>]+)>', r'\2 (\1)', text)
		text = re.sub(r'<(https?://[^>]+)>', r'\1', text)
		return text.replace('&lt;', '<').replace('&gt;', '>').replace('&amp;', '&')

	def name(self, user: str) -> str:
		"""The display name of Slack user *user*, or the id itself when Slack cannot say."""
		if user not in self._names:
			try:
				profile = self.client.users_info(user=user)['user']
				self._names[user] = (
					profile.get('profile', {}).get('display_name')
					or profile.get('real_name')
					or profile.get('name')
					or user
				)
			except Exception:  # noqa: BLE001 - a missing name must not stop the triage
				return user
		return self._names[user]


class SlackDesk:
	"""Workflow posts in, analyses to the reviewer. *client* is a slack_sdk WebClient, *ask*
	is :func:`dss.runner.run_reply` and *sleep* is :func:`time.sleep` (all faked in tests); Bolt
	wiring is in :func:`build_app`.
	"""

	def __init__(
		self,
		client,
		reviewer: str,
		store: AnswerStore,
		channel: str = CHANNEL,
		ask=runner.run_reply,
		sleep=time.sleep,
	) -> None:
		self.client = client
		self.reviewer = reviewer
		self.store = store
		self.channel = channel
		self.ask = ask
		self.sleep = sleep
		self.names = Names(client)
		self._seen: dict[str, float] = {}  # event ts -> when it was first seen
		self._lock = threading.Lock()

	def on_message(self, event: dict) -> None:
		"""A new DSS ticket from a support-request workflow: triage it and DM the reviewer."""
		ticket = TICKET.search(event.get('text', ''))
		if not (event.get('bot_id') and ticket and event.get('channel') == self.channel):
			return
		channel, ts, key = event['channel'], event['ts'], ticket[1]
		if event.get('thread_ts', ts) != ts or self._repeat(ts):
			return
		wait = float(ts) + TRIAGE_DELAY_S - time.time()
		if wait > 0:
			self.sleep(wait)
		# Cerberus names telemetry logs by local start time; the triage compares against this
		posted = datetime.fromtimestamp(float(ts)).strftime(TIME_FORMAT)
		link = self._link(channel, ts)
		try:
			answer = triage(key, posted, self.names.plain(event['text']), self.ask, thread_ts=ts)
		except Exception as error:  # noqa: BLE001 - the reviewer must hear about every failure
			detail = explain(error)
			warning = f':warning: Could not triage <{link}|{key}>: {detail}'
			self._dm(f'Could not triage {key}: {detail}', [_section(warning)])
			return
		self.store.put(channel, ts, {'ticket': key, 'posted': posted, **answer})
		usage = f'{answer["tokens"]:,} tokens · ≈ ${answer["cost_usd"]:.2f} · {answer["seconds"]} s'
		footer = f'Draft by Claude, for you only · <{link}|{key}> · {usage}'
		self._dm(f'Triage of {key}', [*_sections(mrkdwn(answer['text'])), _context(footer)])

	def catch_up(self) -> None:
		"""Triage tickets posted while the bot was down; Slack does not resend those events.

		Tickets older than CATCH_UP_S, and tickets already triaged, are left alone. A triage that
		failed saved no answer, so it is tried again.
		"""
		oldest = f'{time.time() - CATCH_UP_S:.6f}'
		history = self.client.conversations_history(channel=self.channel, oldest=oldest, limit=200)
		for event in reversed(history['messages']):  # Slack lists newest first
			if self.store.get(self.channel, event['ts']) is None:
				self.on_message({**event, 'channel': self.channel})

	def _repeat(self, ts: str) -> bool:
		"""Whether the event *ts* was already seen within SEEN_S, i.e. Slack sent it again."""
		now = time.monotonic()
		with self._lock:
			recent = {}
			for seen_ts, seen_at in self._seen.items():
				if now - seen_at < SEEN_S:
					recent[seen_ts] = seen_at
			self._seen = recent
			if ts in self._seen:
				return True
			self._seen[ts] = now
		return False

	def _link(self, channel: str, ts: str) -> str:
		try:
			return self.client.chat_getPermalink(channel=channel, message_ts=ts)['permalink']
		except Exception:  # noqa: BLE001 - a link is a convenience
			return f'https://slack.com/archives/{channel}/p{ts.replace(".", "")}'

	def _dm(self, text: str, blocks: list[dict]) -> None:
		self.client.chat_postMessage(channel=self.reviewer, text=text, blocks=blocks[:MAX_BLOCKS])


def mrkdwn(markdown: str) -> str:
	"""Claude's Markdown as Slack mrkdwn: bold, headings, links and bullets; code kept."""
	parts = re.split(r'(```.*?```)', markdown, flags=re.DOTALL)
	for index in range(0, len(parts), 2):  # the even parts lie outside code fences
		text = parts[index].replace('&', '&amp;').replace('<', '&lt;').replace('>', '&gt;')
		text = re.sub(r'\[([^\]]+)\]\((https?://[^)\s]+)\)', r'<\2|\1>', text)
		text = re.sub(r'\*\*([^*]+)\*\*', r'*\1*', text)
		text = re.sub(r'^#{1,6}\s+(.+)$', r'*\1*', text, flags=re.MULTILINE)
		parts[index] = re.sub(r'^(\s*)[-*]\s+', r'\1• ', text, flags=re.MULTILINE)
	return ''.join(parts)


def _sections(text: str) -> list[dict]:
	"""Section blocks of at most BLOCK_CHARS, split at line ends (a code fence may be split)."""
	chunks: list[str] = []
	chunk = ''
	for line in text.splitlines(keepends=True):
		while len(line) > BLOCK_CHARS:  # a line longer than a block is cut into block-sized pieces
			chunks.append(chunk)
			chunks.append(line[:BLOCK_CHARS])
			chunk = ''
			line = line[BLOCK_CHARS:]
		if len(chunk) + len(line) > BLOCK_CHARS:
			chunks.append(chunk)
			chunk = ''
		chunk += line
	chunks.append(chunk)
	sections = []
	for piece in chunks:
		if piece.strip():
			sections.append(_section(piece))
	if not sections:
		return [_section('(empty answer)')]
	return sections[:MAX_SECTIONS]


def _section(text: str) -> dict:
	return {'type': 'section', 'text': {'type': 'mrkdwn', 'text': text[:3000]}}


def _context(text: str) -> dict:
	return {'type': 'context', 'elements': [{'type': 'mrkdwn', 'text': text}]}


def watched_channel() -> str:
	"""The channel to watch: DSS_SLACK_CHANNEL (a test channel), else #data-dev-support."""
	return os.environ.get('DSS_SLACK_CHANNEL') or CHANNEL


def build_app(store: AnswerStore, reviewer: str):
	from slack_bolt import App

	app = App(token=os.environ['DSS_SLACK_BOT_TOKEN'])
	desk = SlackDesk(app.client, reviewer, store, watched_channel())
	app.event('message')(lambda event: desk.on_message(event))
	return app, desk


def main() -> None:
	from slack_bolt.adapter.socket_mode import SocketModeHandler

	logging.basicConfig(level=logging.INFO)
	missing = []
	for name in ENV:
		if not os.environ.get(name):
			missing.append(name)
	if missing:
		raise SystemExit(
			f'Set {", ".join(missing)} first: the bot and app tokens of the Slack app made from '
			'slack_manifest.yaml, and your Slack member id (profile > Copy member ID).'
		)
	log.info('triaging new tickets in %s', watched_channel())
	app, desk = build_app(AnswerStore(ROOT / 'out' / 'answers'), os.environ['DSS_SLACK_REVIEWER'])
	handler = SocketModeHandler(app, os.environ['DSS_SLACK_APP_TOKEN'])
	handler.connect()
	# live events are handled while the missed tickets are triaged
	threading.Thread(target=_catch_up_logged, args=(desk,), daemon=True).start()
	threading.Event().wait()


def _catch_up_logged(desk: SlackDesk) -> None:
	try:
		desk.catch_up()
	except Exception:  # a failed catch-up must not stop the live bot
		log.exception('catching up on missed DSS tickets failed')
