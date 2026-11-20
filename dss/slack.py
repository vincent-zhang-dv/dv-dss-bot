"""DSS triage bot in Slack #data-dev-support: each new ticket is analysed for the developers.

When a support-request workflow posts "created DSS-…" in the channel, Claude triages the ticket as
a checked record (:func:`dss.record.trial`). The decision is posted to DSS_SLACK_POST_CHANNEL (the
developers' channel), else to the reviewer's DM (DSS_SLACK_REVIEWER), with the proof in a thread
under it. Nothing is ever posted in the watched channel. A post with a draft reply has an "Edit &
copy draft" button: a pop-up with the draft to edit and copy into the DSS thread, where the
developer posts it themselves. The triage starts TRIAGE_DELAY_S after the post, so the thread it
reads (`slack_thread`) holds the workflow's screenshot. The bot runs on the reviewer's PC under
their `claude` login (Socket Mode: no public URL). On start it also triages the tickets of the
last CATCH_UP_S that have no saved answer, because Slack does not resend events missed while the
bot was down.

	set DSS_SLACK_BOT_TOKEN=xoxb-...  DSS_SLACK_APP_TOKEN=xapp-...  DSS_SLACK_POST_CHANNEL=C0...
	python -m dss slack

Slack hands each event to one of an app's open connections, so only one bot may run per app.

Copied from dv-auto-setup-agent `assistant/slack.py` (commit b3f182e), triage part only.
"""

import json
import logging
import os
import re
import threading
import time
from datetime import datetime

from dss import record
from dss.store import AnswerStore
from dss.triage import TIME_FORMAT, explain
from dss.worker import ROOT

CHANNEL = 'C0793LJ1KDM'  # #data-dev-support
TICKET = re.compile(r'created (DSS-\d+)')  # what the support-request workflows post
CREATOR = re.compile(r'<@([A-Z0-9]+)(\|[^>]*)?>\s*created DSS-')  # the analyst who asked
CATCH_UP_S = 24 * 3600  # on start, triage tickets posted this recently that were missed
# The workflow posts the analyst's screenshot within a second of the ticket; waiting longer would
# let the developers' own first answers into the thread the triage reads.
TRIAGE_DELAY_S = 15
SEEN_S = 600  # an event seen this recently is a redelivery; Slack resends unacknowledged events
MARKDOWN_CHARS = 12_000  # Slack's limit for the Markdown block of one message
ENV = ('DSS_SLACK_BOT_TOKEN', 'DSS_SLACK_APP_TOKEN')
LABEL = 'Draft by Claude'  # starts the post's footer, before what the triage cost
REVIEW_PAGE = 'https://claude.ai/artifact/E9iuWzrKfHo6iAxXLhvdwt'  # DSS Bot Review; #<key> opens it
REVIEW_TEXT = "Please review the bot's report"  # a button: a link would get its own preview
BUTTON_ACTION = 'open_link'  # a link button still sends its click to the app
COPY_TEXT = 'Edit & copy draft'
COPY_ACTION = 'edit_copy'
COPY_VIEW = 'copy_draft'  # the pop-up's callback id
MARKDOWN_LINK = re.compile(r'\[([^\]\n]*)\]\([^)\s]*\)')

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
	"""Workflow posts in, analyses to *destination*: a channel or a member's DM, never the watched
	channel. *client* is a slack_sdk WebClient, *ask* is Claude (None: the record's own, see
	:func:`dss.record.trial`) and *sleep* is :func:`time.sleep` (all faked in tests); Bolt wiring is
	in :func:`build_app`.
	"""

	def __init__(
		self,
		client,
		destination: str,
		store: AnswerStore,
		channel: str = CHANNEL,
		ask=None,
		sleep=time.sleep,
	) -> None:
		if destination in (channel, CHANNEL):
			raise ValueError('analyses never go to #data-dev-support or the watched channel')
		self.client = client
		self.destination = destination
		self.store = store
		self.channel = channel
		self.ask = ask
		self.sleep = sleep
		self.names = Names(client)
		self._seen: dict[str, float] = {}  # event ts -> when it was first seen
		self._lock = threading.Lock()

	def on_message(self, event: dict) -> None:
		"""A new DSS ticket from a support-request workflow: triage it as a checked record and post
		the decision, with the proof in its thread, to the destination."""
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
		link = self._link(channel, ts)  # the headline's key; Slack shows the DSS post below
		post = self.names.plain(event['text'])
		analyst = ''  # the Jira reporter stands in
		creator = CREATOR.search(event['text'])
		if creator and self.names.name(creator[1]) != creator[1]:
			analyst = self.names.name(creator[1])  # a name, not a mention, which would notify them
		try:
			result = record.trial(
				key,
				posted,
				post,
				self.ask,
				thread_ts=ts,
				label=LABEL,
				ticket_link=link,
				analyst=analyst,
			)
		except Exception as error:  # noqa: BLE001 - the developers must hear about every failure
			self._post(f':warning: Could not triage [{key}]({link}): {explain(error)}')
			return
		saved = {'ticket': key, 'posted': posted, 'trial': result['file']}
		for name in ('problems', 'tokens', 'cost_usd', 'seconds', 'turns'):
			saved[name] = result[name]
		self.store.put(channel, ts, saved)
		review = (REVIEW_TEXT, f'{REVIEW_PAGE}#{key}')
		decision = self._post(result['dm'], button=review, copy=(key, draft(result)))
		self._post(result['thread'], thread_ts=decision)

	def open_copy(self, body: dict) -> None:
		"""The "Edit & copy draft" click: the draft in a pop-up, to edit and copy; Slack buttons
		cannot write to the clipboard."""
		draft_reply = json.loads(body['actions'][0]['value'])
		how = 'Edit it here, then Ctrl+A and Ctrl+C, and paste it in the DSS thread yourself.'
		view = {
			'type': 'modal',
			'callback_id': COPY_VIEW,
			'title': {'type': 'plain_text', 'text': f'Draft reply · {draft_reply["ticket"]}'},
			'submit': {'type': 'plain_text', 'text': 'Done'},
			'close': {'type': 'plain_text', 'text': 'Cancel'},
			'blocks': [
				{
					'type': 'input',
					'block_id': 'reply',
					'label': {'type': 'plain_text', 'text': 'Reply to the analyst'},
					'element': {
						'type': 'plain_text_input',
						'action_id': 'text',
						'multiline': True,
						'initial_value': draft_reply['reply'],
					},
				},
				{'type': 'context', 'elements': [{'type': 'mrkdwn', 'text': how}]},
			],
		}
		self.client.views_open(trigger_id=body['trigger_id'], view=view)

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

	def _post(
		self,
		text: str,
		thread_ts: str = '',
		button: tuple[str, str] | None = None,
		copy: tuple[str, str] | None = None,
	) -> str:
		"""*text* (Markdown) to the destination, in the thread *thread_ts* when given; its ts."""
		if self.destination in (self.channel, CHANNEL):  # checked at start too; never post there
			raise RuntimeError('analyses never go to #data-dev-support or the watched channel')
		return post(self.client, self.destination, text, thread_ts, button, copy)


def post(
	client,
	to: str,
	text: str,
	thread_ts: str = '',
	button: tuple[str, str] | None = None,
	copy: tuple[str, str] | None = None,
) -> str:
	"""*text* (Markdown) to the channel or DM *to*, in the thread *thread_ts* when given; its ts.

	Never to #data-dev-support or the watched channel. Slack previews the link to the DSS post and
	no Jira or GitHub link. unfurl_links stays at Slack's default: False hides the DSS post's
	preview too. *button* (label, url) adds a link button under the text; it gets no preview.
	*copy* (ticket, draft reply) adds the "Edit & copy draft" button when the draft is not empty;
	the button carries both, so its pop-up needs no saved file.
	"""
	if to in (watched_channel(), CHANNEL):
		raise RuntimeError('analyses never go to #data-dev-support or the watched channel')
	first = MARKDOWN_LINK.sub(r'\1', text.splitlines()[0]).replace('**', '')
	message = {
		'channel': to,
		'text': first[:300],  # for notifications
		'blocks': [{'type': 'markdown', 'text': text[:MARKDOWN_CHARS]}],
		'unfurl_media': False,
	}
	elements = []
	if button:
		label, url = button
		element = {'type': 'button', 'action_id': BUTTON_ACTION, 'url': url}
		element['text'] = {'type': 'plain_text', 'text': label}
		elements.append(element)
	if copy and copy[1]:
		ticket, reply = copy
		value = json.dumps(
			{'ticket': ticket, 'reply': reply}, ensure_ascii=False
		)  # under 2000 chars
		element = {'type': 'button', 'action_id': COPY_ACTION, 'value': value}
		element['text'] = {'type': 'plain_text', 'text': COPY_TEXT}
		elements.append(element)
	if elements:
		message['blocks'].append({'type': 'actions', 'elements': elements})
	if thread_ts:
		message['thread_ts'] = thread_ts
	return client.chat_postMessage(**message)['ts']


def draft(result: dict) -> str:
	"""The reply to the analyst in a trial *result*; '' when the record failed its checks or the
	replay stopped."""
	if result.get('problems') or not result.get('record'):
		return ''
	return result['record']['triage'].get('reply') or ''


def watched_channel() -> str:
	"""The channel to watch: DSS_SLACK_CHANNEL (a test channel), else #data-dev-support."""
	return os.environ.get('DSS_SLACK_CHANNEL') or CHANNEL


def destination() -> str:
	"""Where analyses go: DSS_SLACK_POST_CHANNEL (the developers' channel), else the reviewer's
	DM (DSS_SLACK_REVIEWER); empty when neither is set."""
	return os.environ.get('DSS_SLACK_POST_CHANNEL') or os.environ.get('DSS_SLACK_REVIEWER', '')


def build_app(store: AnswerStore, to: str):
	from slack_bolt import App

	app = App(token=os.environ['DSS_SLACK_BOT_TOKEN'])
	desk = SlackDesk(app.client, to, store, watched_channel())
	app.event('message')(lambda event: desk.on_message(event))
	app.action(BUTTON_ACTION)(lambda ack: ack())  # the button only opens its link

	@app.action(COPY_ACTION)
	def open_copy(ack, body):
		ack()
		desk.open_copy(body)

	app.view(COPY_VIEW)(lambda ack: ack())  # Done only closes the pop-up
	return app, desk


def main() -> None:
	from slack_bolt.adapter.socket_mode import SocketModeHandler

	logging.basicConfig(level=logging.INFO)
	missing = []
	for name in ENV:
		if not os.environ.get(name):
			missing.append(name)
	if not destination():
		missing.append('DSS_SLACK_POST_CHANNEL or DSS_SLACK_REVIEWER')
	if missing:
		raise SystemExit(
			f'Set {", ".join(missing)} first: the bot and app tokens of the Slack app, and where '
			'analyses go (a channel id, or your member id: profile > Copy member ID).'
		)
	if destination() in (watched_channel(), CHANNEL):
		raise SystemExit('DSS_SLACK_POST_CHANNEL is the watched channel; analyses never go there')
	log.info('triaging new tickets in %s, analyses to %s', watched_channel(), destination())
	app, desk = build_app(AnswerStore(ROOT / 'out' / 'answers'), destination())
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
