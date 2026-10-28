"""Slack desk: workflow ticket post -> triage -> the reviewer's DM, with a fake Slack client."""

import re

import pytest

from dss import slack
from dss.store import AnswerStore

CHANNEL, REVIEWER = slack.CHANNEL, 'UREV'


class FakeSlack:
	"""A slack_sdk WebClient stand-in that records calls; methods in *failing* raise."""

	def __init__(self, history=()):
		self.calls, self.history = [], list(history)
		self.failing: set[str] = set()

	def __getattr__(self, method):
		def call(**kwargs):
			self.calls.append((method, kwargs))
			if method in self.failing:
				raise RuntimeError(f'{method} failed')
			if method == 'users_info':
				return {'user': {'profile': {'display_name': kwargs['user'].lower()}}}
			if method == 'conversations_history':
				return {'messages': self.history}
			if method == 'chat_getPermalink':
				return {'permalink': 'https://slack/p1'}
			return {'ok': True}

		call.__name__ = method
		return call

	def sent(self, method):
		calls = []
		for name, kwargs in self.calls:
			if name == method:
				calls.append(kwargs)
		return calls


class FakeClaude:
	def __init__(self, answer='**DSS-1234 · Plot is wrong**', error=None):
		self.answer, self.error, self.prompts = answer, error, []

	def __call__(self, prompt, trace=None):
		self.prompts.append(prompt)
		if self.error:
			raise self.error
		usage = {'input_tokens': 1000, 'output_tokens': 234}
		return {'result': self.answer, 'total_cost_usd': 0.02, 'usage': usage}


def desk(tmp_path, claude=None, history=()):
	store = AnswerStore(tmp_path / 'answers')
	return slack.SlackDesk(FakeSlack(history), REVIEWER, store, ask=claude or FakeClaude())


def ticket(ts='1792523850.5', **extra):
	text = '<@UANA|ana> created DSS-1234\n*Client:* ACME\n*DVT*: 4321\n*Summary*: Plot is wrong'
	event = {'channel': CHANNEL, 'bot_id': 'BWORKFLOW', 'subtype': 'bot_message', 'ts': ts}
	return event | {'text': text, **extra}


def test_new_ticket_is_triaged_and_drafted_to_the_reviewer_only(tmp_path):
	claude = FakeClaude()
	bot = desk(tmp_path, claude)
	bot.on_message(ticket())
	assert 'New ticket DSS-1234, posted 20' in claude.prompts[0]
	assert '*Client:* ACME' in claude.prompts[0]  # the workflow's post goes along
	sent = bot.client.sent('chat_postMessage')
	assert [message['channel'] for message in sent] == [REVIEWER]  # nothing in the channel
	assert all(block['type'] != 'actions' for block in sent[0]['blocks'])  # no Post button
	assert '*DSS-1234 · Plot is wrong*' in str(sent[0]['blocks'])
	assert '1,234 tokens · ≈ $0.02' in str(sent[0]['blocks'][-1])
	saved = bot.store.get(CHANNEL, '1792523850.5')
	assert saved['ticket'] == 'DSS-1234' and saved['tokens'] == 1234


def test_only_new_workflow_tickets_are_triaged(tmp_path):
	claude = FakeClaude()
	bot = desk(tmp_path, claude)
	bot.on_message(ticket(bot_id=None, user='UANA'))  # a person typing the words
	bot.on_message(ticket(ts='2.0', thread_ts='1.0'))  # the workflow's reply in a thread
	bot.on_message(ticket(channel='COTHER'))
	bot.on_message(ticket())
	bot.on_message(ticket())  # Slack redelivers events
	assert len(claude.prompts) == 1


def test_mentions_become_names_and_slack_failures_fall_back(tmp_path):
	claude = FakeClaude()
	bot = desk(tmp_path, claude)
	bot.on_message(ticket(text='<@UANA> created DSS-1234 <https://acme.test/x|the doc>'))
	assert '@uana created DSS-1234 the doc (https://acme.test/x)' in claude.prompts[0]
	broken = desk(tmp_path / 'broken', claude)
	broken.client.failing = {'users_info', 'chat_getPermalink'}
	broken.on_message(ticket(ts='1792523851.5', text='<@UANA> created DSS-1235'))
	assert '@UANA created DSS-1235' in claude.prompts[1]
	(dm,) = broken.client.sent('chat_postMessage')
	assert 'https://slack.com/archives/C0793LJ1KDM/p17925238515' in str(dm['blocks'][-1])


def test_catch_up_triages_missed_tickets_oldest_first_and_only_once(tmp_path):
	claude = FakeClaude(error=RuntimeError('claude is down'))
	missed = [  # Slack lists newest first and leaves the channel out
		ticket(ts='3.0', text='<@UANA|ana> created DSS-3'),
		{'ts': '2.0', 'user': 'UANA', 'text': 'thanks!'},
		ticket(ts='1.0', text='<@UANA|ana> created DSS-1'),
	]
	history = []
	for message in missed:
		without_channel = dict(message)
		without_channel.pop('channel', None)
		history.append(without_channel)
	bot = desk(tmp_path, claude, history=history)
	bot.catch_up()  # both fail and save no answer, so they stay due
	bot._seen.clear()  # a restart forgets the events it saw
	claude.error, claude.prompts = None, []
	bot.catch_up()
	asked = [re.findall(r'New ticket (DSS-\d+)', prompt)[0] for prompt in claude.prompts]
	assert asked == ['DSS-1', 'DSS-3']
	bot._seen.clear()
	bot.catch_up()
	assert len(claude.prompts) == 2  # already triaged


def test_a_failed_catch_up_is_logged_and_the_bot_keeps_running(tmp_path, caplog):
	bot = desk(tmp_path)
	bot.client.failing = {'conversations_history'}
	slack._catch_up_logged(bot)
	assert 'catching up on missed DSS tickets failed' in caplog.text


def test_triage_failure_reaches_the_reviewer(tmp_path):
	bot = desk(tmp_path, FakeClaude(error=RuntimeError('Invalid API key')))
	bot.on_message(ticket())
	(dm,) = bot.client.sent('chat_postMessage')
	assert 'Could not triage DSS-1234' in dm['text'] and 'not logged in' in dm['text']
	assert bot.store.get(CHANNEL, '1792523850.5') is None


def test_bot_says_which_settings_to_set(monkeypatch):
	for name in slack.ENV:
		monkeypatch.delenv(name, raising=False)
	expected = 'Set DSS_SLACK_BOT_TOKEN, DSS_SLACK_APP_TOKEN, DSS_SLACK_REVIEWER first'
	with pytest.raises(SystemExit, match=expected):
		slack.main()


def test_bot_watches_the_test_channel_when_one_is_set(monkeypatch):
	monkeypatch.delenv('DSS_SLACK_CHANNEL', raising=False)
	assert slack._channel() == CHANNEL
	monkeypatch.setenv('DSS_SLACK_CHANNEL', 'CTEST')
	assert slack._channel() == 'CTEST'


def test_store_names_stay_inside_its_folder(tmp_path):
	store = AnswerStore(tmp_path)
	for channel, ts in (('..', '1.0'), (CHANNEL, '../1.0'), (CHANNEL, '1.0\\x')):
		with pytest.raises(ValueError, match='not a Slack channel'):
			store.get(channel, ts)


@pytest.mark.parametrize(
	('markdown', 'expected'),
	[
		('## Title\n- a **b** <c>', '*Title*\n• a *b* &lt;c&gt;'),
		('```\n**x** <y>\n```', '```\n**x** <y>\n```'),
		('[the SOP](https://acme.test/sop)', '<https://acme.test/sop|the SOP>'),
	],
)
def test_mrkdwn(markdown, expected):
	assert slack.mrkdwn(markdown) == expected


def test_long_answers_split_into_blocks():
	blocks = slack._sections(('x' * 100 + '\n') * 80 + 'y' * 7000)
	assert all(len(block['text']['text']) <= 3000 for block in blocks) and len(blocks) == 6
	assert slack._sections('  \n') == [slack._section('(empty answer)')]
	assert len(slack._sections('z\n' * 2000 * 60)) == slack.MAX_SECTIONS
