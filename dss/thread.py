"""`slack_thread` tool: a DSS ticket's Slack thread with its screenshots, read-only.

Reads only threads in the watched channel (:func:`dss.slack.watched_channel`) that start with a
support-request workflow's "created DSS-…" post, with the bot's token (DSS_SLACK_BOT_TOKEN).
Every request is a GET: `conversations.replies`, `users.info`, and each file's private URL on
files.slack.com, which needs the app's `files:read` scope. Images go to Claude as images and text
files as text. DSS_THREAD_UNTIL (a Slack ts, set by the CLI) leaves out later replies, so a
triage of an older ticket sees the thread as the bot would have, without the developers' answers.

The workflow (Data Support Request, After Hours Support Request) posts in three parts, within
seconds: "@<analyst> created DSS-…", its first reply "Screenshot of error:" with the analyst's
screenshot, and a reminder. The analyst often adds more (screenshots, details) before anyone else
replies. In a replay (:mod:`dss.cutoff`) the replayed ticket's thread shows those and stops at the
first reply from anyone else (:func:`_as_raised`).
"""

import os
import re
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime

from mcp.server.mcpserver import Image
from slack_sdk import WebClient

from dss import cutoff, slack

TS = re.compile(r'\d+\.\d+')
MAX_MESSAGES = 200
MAX_IMAGES = 20  # each costs Claude about 1.5-3k tokens
MAX_FILE_BYTES = 20 * 2**20  # one file is held in memory
MAX_TEXT_CHARS = 20_000  # of one text file
TIMEOUT_S = 60
FILE_HOST = 'files.slack.com'  # the only host that gets the bot token
IMAGE_FORMATS = {'image/png': 'png', 'image/jpeg': 'jpeg', 'image/gif': 'gif', 'image/webp': 'webp'}
SHOWN_TIME = '%Y-%m-%d %H:%M:%S'
NOT_SENT = 'Slack sent a sign-in page instead of the file (the app needs the files:read scope)'
AS_RAISED_S = 30 * 60  # a replay reads the analyst's own follow-ups this long after the post


class _NoRedirect(urllib.request.HTTPRedirectHandler):
	"""Refuses redirects, so the token never follows one; Slack redirects when it won't send."""

	def redirect_request(self, req, fp, code, msg, headers, newurl):
		return None


OPENER = urllib.request.build_opener(_NoRedirect)


def thread(ts: str) -> list[str | Image]:
	"""The post *ts* and every reply in its thread, oldest first, with their images and text files.

	Replies after DSS_THREAD_UNTIL are left out; a replay shows the thread as raised instead
	(:func:`_as_raised`). A file that can't be read is named with the reason, and the rest of the
	thread still comes back.
	"""
	if not TS.fullmatch(ts):
		raise ValueError(f'{ts!r} is not a Slack message ts like 1793475636.080799')
	client = _client()
	messages = _replies(client, slack.watched_channel(), ts)
	post: dict = {}
	if messages:
		post = messages[0]
	from_workflow = post.get('bot_id') and slack.TICKET.search(post.get('text', ''))
	if post.get('ts') != ts or not from_workflow:
		raise ValueError(f'{ts} is not a workflow\'s "created DSS-…" post in the watched channel')
	until = os.environ.get('DSS_THREAD_UNTIL', '')
	then = cutoff.when()
	if then is not None:
		messages = _as_raised(messages, then)
		until = f'{then.timestamp():.6f}'  # what is marked as edited after the ticket
	elif until:
		shown = []
		for message in messages:
			if float(message['ts']) <= float(until):
				shown.append(message)
		messages = shown
	names = slack.Names(client)
	found: list[str | Image] = [
		f'Thread {ts}: the post and {len(messages) - 1} replies, oldest first, in local time.'
	]
	images = 0
	for number, message in enumerate(messages):
		label = f'Reply {number}' if number else 'Post'
		when = datetime.fromtimestamp(float(message['ts'])).strftime(SHOWN_TIME)
		lines = [f'--- {label} · {_author(message, names)} · {when}']
		edited = (message.get('edited') or {}).get('ts', '')
		if until and edited and float(edited) > float(until):  # Slack keeps only the new text
			lines.append('(edited after the ticket time: the text below is the edited one)')
		if message.get('text'):
			lines.append(names.plain(message['text']))
		attached: list[str | Image] = []
		for file in message.get('files') or []:
			line, content = _attachment(file, images < MAX_IMAGES)
			lines.append(line)
			if content is not None:
				attached.append(content)
				if isinstance(content, Image):
					images += 1
		found.append('\n'.join(lines))
		found.extend(attached)
	return found


def _as_raised(messages: list[dict], then: datetime) -> list[dict]:
	"""A thread's *messages* as a replay at the cutoff *then* may read them.

	The replayed ticket's own thread: its post, then what the workflow posted ("Screenshot of
	error:" with the screenshot, the reminder) and what the analyst who raised it added (the
	@mention in "@<analyst> created DSS-…"), up to the first reply from anyone else, however soon,
	and for at most AS_RAISED_S: a later note by the analyst may say how it was solved.
	Another ticket's thread: what was posted by *then*; ValueError for one posted after it.
	"""
	post = messages[0]
	ticket = slack.TICKET.search(post.get('text', ''))
	if not ticket or ticket[1] != cutoff.key():
		shown = []
		for message in messages:
			if float(message['ts']) <= then.timestamp():
				shown.append(message)
		if not shown:  # a ticket posted after the replayed one
			raise ValueError(
				f'thread {post["ts"]} was posted after the ticket time; it cannot be read'
			)
		return shown
	raiser = ''
	creator = slack.CREATOR.search(post.get('text', ''))
	if creator:
		raiser = creator[1]
	shown = [post]
	for message in messages[1:]:
		from_raiser = bool(raiser) and message.get('user') == raiser
		if not message.get('bot_id') and not from_raiser:
			break
		if float(message['ts']) - float(post['ts']) > AS_RAISED_S:
			break
		shown.append(message)
	return shown


def _client() -> WebClient:
	return WebClient(token=_token())


def _token() -> str:
	token = os.environ.get('DSS_SLACK_BOT_TOKEN', '')
	if not token:
		raise RuntimeError(
			'Slack is not set up on this machine: set DSS_SLACK_BOT_TOKEN (the bot token of the '
			'app made from slack_manifest.yaml)'
		)
	return token


def _replies(client, channel: str, ts: str) -> list[dict]:
	"""Up to MAX_MESSAGES messages of thread *ts*: the post, then its replies."""
	messages: list[dict] = []
	options = {'channel': channel, 'ts': ts, 'limit': MAX_MESSAGES}
	while len(messages) < MAX_MESSAGES:
		page = client.conversations_replies(**options)
		messages.extend(page['messages'])
		cursor = (page.get('response_metadata') or {}).get('next_cursor')
		if not cursor:
			break
		options['cursor'] = cursor
	return messages[:MAX_MESSAGES]


def _author(message: dict, names: slack.Names) -> str:
	if message.get('user'):
		return names.name(message['user'])
	profile = message.get('bot_profile') or {}
	return message.get('username') or profile.get('name') or 'workflow'


def _attachment(file: dict, image_allowed: bool) -> tuple[str, str | Image | None]:
	"""A line naming *file*, and its image or text when Claude can read it here."""
	name = file.get('name') or file.get('title') or file.get('id', 'file')
	kind = file.get('mimetype', '')
	line = f'File {name} ({kind or "type unknown"}, {_size(file.get("size"))})'
	url = file.get('url_private_download') or file.get('url_private')
	if not url:  # deleted, or a link to a file kept elsewhere
		return f'{line}: no Slack copy to read', None
	try:
		if kind in IMAGE_FORMATS:
			if not image_allowed:
				return f'{line}: not shown, the thread has over {MAX_IMAGES} images', None
			image = Image(data=_download(url, kind), format=IMAGE_FORMATS[kind])
			return f'{line}: the image follows', image
		if kind.startswith('text/') or kind == 'application/json':
			text = _download(url, kind).decode('utf-8', errors='replace')
			cut = ''
			if len(text) > MAX_TEXT_CHARS:
				cut = f' (the first {MAX_TEXT_CHARS:,} of {len(text):,} characters)'
			return f'{line}: its text follows{cut}', f'{name}:\n{text[:MAX_TEXT_CHARS]}'
	except (OSError, RuntimeError, ValueError) as error:
		return f'{line}: could not be read: {error}', None
	return f'{line}: this type cannot be read here', None


def _download(url: str, kind: str) -> bytes:
	"""File *url* from Slack with the bot's token; refused for any host but FILE_HOST."""
	parts = urllib.parse.urlsplit(url)
	if parts.scheme != 'https' or parts.hostname != FILE_HOST:
		raise ValueError(f'{url} is not a file on {FILE_HOST}')
	request = urllib.request.Request(url, headers={'Authorization': f'Bearer {_token()}'})
	try:
		with OPENER.open(request, timeout=TIMEOUT_S) as response:
			sent = response.headers.get_content_type()
			data = response.read(MAX_FILE_BYTES + 1)
	except urllib.error.HTTPError as error:
		if 300 <= error.code < 400:
			raise RuntimeError(NOT_SENT) from error
		raise RuntimeError(f'Slack answered {error.code} {error.reason}') from error
	except (urllib.error.URLError, TimeoutError) as error:
		raise RuntimeError(f'cannot reach Slack: {getattr(error, "reason", error)}') from error
	if sent == 'text/html' and kind != 'text/html':
		raise RuntimeError(NOT_SENT)
	if len(data) > MAX_FILE_BYTES:
		raise RuntimeError(f'the file is over {MAX_FILE_BYTES // 2**20} MB')
	return data


def _size(size) -> str:
	if not isinstance(size, int):
		return 'size unknown'
	if size >= 2**20:
		return f'{size / 2**20:.1f} MB'
	return f'{size / 1024:.0f} KB'


def register(server) -> None:
	@server.tool()
	def slack_thread(ts: str) -> list[str | Image]:
		"""The DSS ticket's Slack thread *ts*: the workflow's post and every reply, oldest first,
		with each screenshot as an image and each text file as text."""
		return thread(ts)
