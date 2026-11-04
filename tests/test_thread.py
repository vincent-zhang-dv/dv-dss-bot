"""`slack_thread`: a DSS ticket's Slack thread with its screenshots, read with GETs only."""

import asyncio
import io
import urllib.error
import urllib.request
from email.message import Message

import pytest
from mcp import Client
from mcp.server.mcpserver import Image

from dss import slack, thread
from dss.server import build

POST_TS = '1792523850.000100'
FILES = 'https://files.slack.com/files-pri/T1-F1/'
PNG = b'\x89PNG\r\n\x1a\n made-up screenshot'


def attached(name: str, kind: str, size: int, url: str = '') -> dict:
	return {'id': 'F1', 'name': name, 'mimetype': kind, 'size': size, 'url_private_download': url}


THREAD = [
	{
		'ts': POST_TS,
		'bot_id': 'BWORKFLOW',
		'username': 'Data Support Request',
		'text': '<@UANA> created DSS-1234\n*DVT*: 1234\n*Summary*: Plot fails',
	},
	{
		'ts': '1792523850.000200',
		'text': 'Screenshot of error:',
		'files': [attached('error.png', 'image/png', 1_300_000, FILES + 'error.png')],
	},
	{
		'ts': '1792523940.000300',
		'user': 'UANA',
		'text': 'It used to look like this, log attached',
		'files': [
			attached('before.jpg', 'image/jpeg', 300_000, FILES + 'before.jpg'),
			attached('run.log', 'text/plain', 2_000, FILES + 'run.log'),
			attached('notes.pdf', 'application/pdf', 50_000, FILES + 'notes.pdf'),
			{'id': 'F9', 'name': 'gone.png', 'mimetype': 'image/png', 'mode': 'tombstone'},
		],
	},
	{'ts': '1792524600.000400', 'user': 'UDEV', 'text': 'That is a bug, fixed on my branch'},
]


class FakeSlack:
	"""conversations.replies in pages of *page_size*, and users.info; records each call."""

	def __init__(self, messages, page_size=10):
		self.messages, self.page_size, self.calls = messages, page_size, []

	def conversations_replies(self, channel, ts, limit, cursor='0'):
		self.calls.append((channel, ts, cursor))
		start = int(cursor)
		page: dict = {'messages': self.messages[start : start + self.page_size]}
		if start + self.page_size < len(self.messages):
			page['response_metadata'] = {'next_cursor': str(start + self.page_size)}
		return page

	def users_info(self, user):
		return {'user': {'profile': {'display_name': user.lower()}}}


class Response(io.BytesIO):
	def __init__(self, kind: str, data: bytes) -> None:
		super().__init__(data)
		self.headers = Message()
		self.headers['Content-Type'] = kind


class FakeFiles:
	"""files.slack.com, faked: {url: (content type, bytes) or an error}; records each request."""

	def __init__(self):
		self.files: dict = {}
		self.requests: list[urllib.request.Request] = []

	def open(self, request, timeout):
		self.requests.append(request)
		answer = self.files[request.full_url]
		if isinstance(answer, Exception):
			raise answer
		return Response(*answer)


@pytest.fixture
def slack_api(monkeypatch):
	"""The Slack Web API and file host, faked, with the made-up THREAD and its files."""
	monkeypatch.setenv('DSS_SLACK_BOT_TOKEN', 'xoxb-test')
	monkeypatch.delenv('DSS_SLACK_CHANNEL', raising=False)
	client, files = FakeSlack(THREAD), FakeFiles()
	files.files[FILES + 'error.png'] = ('image/png', PNG)
	files.files[FILES + 'before.jpg'] = ('image/jpeg', b'made-up jpeg')
	files.files[FILES + 'run.log'] = ('text/plain', b'line 1\nValueError: x')
	monkeypatch.setattr(thread, '_client', lambda: client)
	monkeypatch.setattr(thread, 'OPENER', files)
	return client, files


def texts(found: list) -> list[str]:
	parts = []
	for part in found:
		if isinstance(part, str):
			parts.append(part)
	return parts


def test_the_thread_comes_with_its_screenshots_and_text_files(slack_api):
	client, files = slack_api
	found = thread.thread(POST_TS)
	assert found[0] == f'Thread {POST_TS}: the post and 3 replies, oldest first, in local time.'
	post, first, second, log, last = texts(found)[1:]
	assert post.startswith('--- Post · Data Support Request · 20')
	assert post.endswith('\n@uana created DSS-1234\n*DVT*: 1234\n*Summary*: Plot fails')
	assert first.startswith('--- Reply 1 · workflow · ')
	assert first.endswith(
		'Screenshot of error:\nFile error.png (image/png, 1.2 MB): the image follows'
	)
	assert second.startswith('--- Reply 2 · uana · ')
	assert second.endswith(
		'It used to look like this, log attached\n'
		'File before.jpg (image/jpeg, 293 KB): the image follows\n'
		'File run.log (text/plain, 2 KB): its text follows\n'
		'File notes.pdf (application/pdf, 49 KB): this type cannot be read here\n'
		'File gone.png (image/png, size unknown): no Slack copy to read'
	)
	assert log == 'run.log:\nline 1\nValueError: x'
	assert last.startswith('--- Reply 3 · udev · ') and last.endswith('fixed on my branch')
	error_png, before_jpg = found[3], found[5]  # each image follows the reply it came with
	assert isinstance(error_png, Image) and isinstance(before_jpg, Image)
	assert error_png.data == PNG and error_png.to_image_content().mime_type == 'image/png'
	assert before_jpg.to_image_content().mime_type == 'image/jpeg'
	assert client.calls == [(slack.CHANNEL, POST_TS, '0')]
	for request in files.requests:
		assert request.get_method() == 'GET'
		assert request.get_header('Authorization') == 'Bearer xoxb-test'
	assert len(files.requests) == 3  # the pdf and the deleted file are not fetched


def test_a_triage_of_an_older_ticket_sees_the_thread_as_the_bot_would_have(slack_api, monkeypatch):
	monkeypatch.setenv('DSS_THREAD_UNTIL', '1792524150.000100')  # 300 s after the post
	found = thread.thread(POST_TS)
	assert found[0] == f'Thread {POST_TS}: the post and 2 replies, oldest first, in local time.'
	assert not any('That is a bug' in part for part in texts(found))


def test_long_threads_are_read_page_by_page(slack_api, monkeypatch):
	client = FakeSlack(THREAD, page_size=2)
	monkeypatch.setattr(thread, '_client', lambda: client)
	assert 'the post and 3 replies' in thread.thread(POST_TS)[0]
	assert [cursor for _channel, _ts, cursor in client.calls] == ['0', '2']


@pytest.mark.parametrize(
	'messages',
	[
		[],  # no such thread
		[{**THREAD[0], 'ts': '1792523850.000999'}],  # a reply's ts, not the post's
		[{**THREAD[0], 'bot_id': None, 'user': 'UANA'}],  # a person typing the words
		[{**THREAD[0], 'text': 'Deploy at noon'}],  # another workflow's post
	],
)
def test_only_dss_ticket_threads_are_read(slack_api, monkeypatch, messages):
	monkeypatch.setattr(thread, '_client', lambda: FakeSlack(messages))
	with pytest.raises(ValueError, match='is not a workflow\'s "created DSS-…" post'):
		thread.thread(POST_TS)


def test_only_the_watched_channel_is_read(slack_api, monkeypatch):
	client, _files = slack_api
	monkeypatch.setenv('DSS_SLACK_CHANNEL', 'CTEST')
	thread.thread(POST_TS)
	assert client.calls == [('CTEST', POST_TS, '0')]
	with pytest.raises(ValueError, match='not a Slack message ts'):
		thread.thread('C0793LJ1KDM/p1792523850000100')


def test_the_token_goes_only_to_slacks_file_host(slack_api, monkeypatch):
	_client, files = slack_api
	elsewhere = [
		attached('a.png', 'image/png', 10, 'https://acme.test/a.png'),
		attached('b.png', 'image/png', 10, 'http://files.slack.com/b.png'),
		attached('c.png', 'image/png', 10, 'https://files.slack.com.acme.test/c.png'),
	]
	messages = [THREAD[0], {'ts': '1792523851.0', 'text': '', 'files': elsewhere}]
	monkeypatch.setattr(thread, '_client', lambda: FakeSlack(messages))
	found = texts(thread.thread(POST_TS))
	assert found[2].count('could not be read: ') == 3
	assert 'is not a file on files.slack.com' in found[2]
	assert files.requests == []


def test_a_file_slack_will_not_send_says_why(slack_api, monkeypatch):
	_client, files = slack_api
	moved = urllib.error.HTTPError(FILES + 'error.png', 302, 'Found', Message(), None)
	files.files[FILES + 'error.png'] = moved  # what Slack does without files:read
	files.files[FILES + 'before.jpg'] = ('text/html', b'<html>sign in</html>')
	files.files[FILES + 'run.log'] = urllib.error.URLError('refused')
	found = texts(thread.thread(POST_TS))
	assert found[2].endswith(f'could not be read: {thread.NOT_SENT}')
	assert f'before.jpg (image/jpeg, 293 KB): could not be read: {thread.NOT_SENT}' in found[3]
	assert 'run.log (text/plain, 2 KB): could not be read: cannot reach Slack: refused' in found[3]
	assert found[-1].endswith('fixed on my branch')  # the rest of the thread still comes back
	files.files[FILES + 'error.png'] = urllib.error.HTTPError(
		'u', 404, 'Not Found', Message(), None
	)
	files.files[FILES + 'before.jpg'] = ('image/jpeg', b'made-up jpeg')
	monkeypatch.setattr(thread, 'MAX_FILE_BYTES', 10)
	found = texts(thread.thread(POST_TS))
	assert found[2].endswith('could not be read: Slack answered 404 Not Found')
	assert 'before.jpg (image/jpeg, 293 KB): could not be read: the file is over 0 MB' in found[3]


def test_images_and_text_are_capped(slack_api, monkeypatch):
	monkeypatch.setattr(thread, 'MAX_IMAGES', 1)
	monkeypatch.setattr(thread, 'MAX_TEXT_CHARS', 6)
	found = thread.thread(POST_TS)
	images = [part for part in found if isinstance(part, Image)]
	assert len(images) == 1
	second = texts(found)[3]
	assert 'before.jpg (image/jpeg, 293 KB): not shown, the thread has over 1 images' in second
	assert 'run.log (text/plain, 2 KB): its text follows (the first 6 of 20 characters)' in second
	assert texts(found)[4] == 'run.log:\nline 1'


def test_slack_says_which_setting_to_set(monkeypatch):
	monkeypatch.delenv('DSS_SLACK_BOT_TOKEN', raising=False)
	with pytest.raises(RuntimeError, match='set DSS_SLACK_BOT_TOKEN'):
		thread.thread(POST_TS)


def test_file_requests_never_follow_a_redirect():
	redirects = []
	for handler in thread.OPENER.handlers:
		if isinstance(handler, urllib.request.HTTPRedirectHandler):
			redirects.append(type(handler))
	assert redirects == [thread._NoRedirect]


def test_claude_gets_the_screenshots_as_images(slack_api):
	async def call():
		async with Client(build()) as client:
			return await client.call_tool('slack_thread', {'ts': POST_TS})

	result = asyncio.run(call())
	assert not result.is_error
	kinds = [part.type for part in result.content]
	assert kinds == ['text', 'text', 'text', 'image', 'text', 'image', 'text', 'text']
