"""Slack desk: workflow ticket post -> checked triage -> the developers' channel, with a fake Slack
client and a fake Claude."""

import json
import re
import time

import pytest

from dss import code, record, slack
from dss.store import AnswerStore

CHANNEL, DEVELOPERS = slack.CHANNEL, 'CDEVELOPERS'
EXAMPLE = json.loads(record.SCHEMA_FILE.with_name('triage_record.example.json').read_text('utf-8'))
IN_ORDER = ('jira_get_issue', 'jira_search', 'code_log_s', 'code_read')


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
			if method == 'chat_postMessage':  # the first message posted is 900.0
				return {'ok': True, 'ts': f'900.{len(self.sent(method)) - 1}'}
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
	"""Answers the example record, after the tool calls of a run that searched past issues."""

	def __init__(self, answer='', error=None):
		self.answer = answer or json.dumps({'triage': EXAMPLE['triage'], 'class': EXAMPLE['class']})
		self.error, self.prompts = error, []

	def __call__(self, prompt, trace=None):
		self.prompts.append(prompt)
		if self.error:
			raise self.error
		if trace is not None:
			lines = []
			for tool in IN_ORDER:
				lines.append(json.dumps({'tool': tool, 'arguments': {}, 'seconds': 0.1}) + '\n')
			trace.write_text(''.join(lines), encoding='utf-8')
		usage = {'input_tokens': 1000, 'output_tokens': 234}
		return {'result': self.answer, 'total_cost_usd': 0.02, 'usage': usage}


@pytest.fixture(autouse=True)
def ticket_facts(monkeypatch):
	"""What the record reads before Claude starts, faked: no Jira, index or telemetry."""

	def build_ticket(key, posted):
		ticket = {**EXAMPLE['dss_ticket'], 'key': key}
		ticket.pop('inputs')
		return ticket, []

	monkeypatch.setattr(record, 'build_ticket', build_ticket)
	monkeypatch.setattr(code, 'web_links', lambda text: {})
	for name in ('JIRA_URL', 'CONFLUENCE_URL', 'DSS_SLACK_POST_CHANNEL', 'DSS_SLACK_REVIEWER'):
		monkeypatch.delenv(name, raising=False)


def desk(tmp_path, claude=None, history=()):
	store = AnswerStore(tmp_path / 'answers')
	return slack.SlackDesk(FakeSlack(history), DEVELOPERS, store, ask=claude or FakeClaude())


def ticket(ts='1792523850.5', **extra):
	text = '<@UANA|ana> created DSS-1234\n*Client:* ACME\n*DVT*: 4321\n*Summary*: Plot is wrong'
	event = {'channel': CHANNEL, 'bot_id': 'BWORKFLOW', 'subtype': 'bot_message', 'ts': ts}
	return event | {'text': text, **extra}


def test_a_new_ticket_is_triaged_as_a_record_and_posted_to_the_developers(tmp_path):
	claude = FakeClaude()
	bot = desk(tmp_path, claude)
	bot.on_message(ticket())
	assert 'New ticket DSS-1234, posted 20' in claude.prompts[0]
	assert '`slack_thread("1792523850.5")`' in claude.prompts[0]  # Claude reads the thread
	assert '*Client:* ACME' in claude.prompts[0]  # the workflow's post goes along
	decision, proof = bot.client.sent('chat_postMessage')
	assert decision['channel'] == proof['channel'] == DEVELOPERS  # nothing in the DSS channel
	head = 'DSS-1234 · Input problem: casing ID differs from the tally · proved'
	assert decision['text'] == head  # the first line without its link, for notifications
	(block,) = decision['blocks']
	assert block['type'] == 'markdown'
	# the key links to the DSS post, which Slack shows below; no other link shows a preview
	assert block['text'].startswith(
		'**[DSS-1234](https://slack/p1) · Input problem: casing ID differs from the tally · proved**'
		'\n**Bot Summary:** '
	)
	# the analyst's Slack name, as text: a mention would notify them of the draft
	assert '\n**uana Todos:**\n1. Set the ID' in block['text'] and '<@UANA' not in block['text']
	assert '\n\n_Draft by Claude · 1,234 tokens · ≈ $0.02 · ' in block['text']
	assert decision['unfurl_media'] is False and 'unfurl_links' not in decision
	assert proof['thread_ts'] == '900.0' and proof['blocks'][0]['text'].startswith('**Why:** ')
	saved = bot.store.get(CHANNEL, '1792523850.5')
	assert saved['ticket'] == 'DSS-1234' and saved['tokens'] == 1234 and saved['problems'] == []
	assert saved['trial'].endswith('.json')


def test_a_record_that_fails_the_check_is_still_posted(tmp_path):
	bot = desk(tmp_path, FakeClaude(answer='I could not finish the triage.'))
	bot.on_message(ticket())
	decision, proof = bot.client.sent('chat_postMessage')
	assert decision['blocks'][0]['text'].startswith(
		'**[DSS-1234](https://slack/p1) · the trial answer failed the'
	)
	assert 'I could not finish the triage.' in proof['blocks'][0]['text']


def test_a_new_ticket_waits_for_the_workflows_screenshot(tmp_path):
	waits: list[float] = []
	claude = FakeClaude()
	bot = slack.SlackDesk(
		FakeSlack(), DEVELOPERS, AnswerStore(tmp_path), ask=claude, sleep=waits.append
	)
	bot.on_message(ticket(ts=f'{time.time() - 1:.6f}'))  # posted a second ago
	(waited,) = waits
	assert slack.TRIAGE_DELAY_S - 5 < waited <= slack.TRIAGE_DELAY_S
	assert len(claude.prompts) == 1
	bot.on_message(ticket(ts=f'{time.time() - slack.TRIAGE_DELAY_S - 1:.6f}'))  # caught up late
	assert len(waits) == 1 and len(claude.prompts) == 2


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
	decision, _ = broken.client.sent('chat_postMessage')
	assert '(https://slack.com/archives/C0793LJ1KDM/p17925238515)' in decision['blocks'][0]['text']


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


def test_triage_failure_reaches_the_developers(tmp_path):
	bot = desk(tmp_path, FakeClaude(error=RuntimeError('Invalid API key')))
	bot.on_message(ticket())
	(message,) = bot.client.sent('chat_postMessage')
	text = message['blocks'][0]['text']
	assert text.startswith(':warning: Could not triage [DSS-1234](https://slack/p1): Claude is not')
	assert message['channel'] == DEVELOPERS
	assert bot.store.get(CHANNEL, '1792523850.5') is None


def test_analyses_never_go_to_the_dss_channel(tmp_path, monkeypatch):
	store = AnswerStore(tmp_path)
	with pytest.raises(ValueError, match='never go to #data-dev-support'):
		slack.SlackDesk(FakeSlack(), CHANNEL, store)
	with pytest.raises(ValueError, match='never go to #data-dev-support or the watched channel'):
		slack.SlackDesk(FakeSlack(), 'CTEST', store, channel='CTEST')
	bot = desk(tmp_path)
	bot.destination = CHANNEL  # even if it were changed after the start
	with pytest.raises(RuntimeError, match='never go to #data-dev-support'):
		bot._post('x')
	assert bot.client.sent('chat_postMessage') == []
	for name, value in (('DSS_SLACK_BOT_TOKEN', 'xoxb-t'), ('DSS_SLACK_APP_TOKEN', 'xapp-t')):
		monkeypatch.setenv(name, value)
	monkeypatch.setenv('DSS_SLACK_POST_CHANNEL', CHANNEL)
	with pytest.raises(SystemExit, match='is the watched channel; analyses never go there'):
		slack.main()


def test_analyses_go_to_the_post_channel_else_the_reviewers_dm(monkeypatch):
	assert slack.destination() == ''
	monkeypatch.setenv('DSS_SLACK_REVIEWER', 'UREVIEWER')
	assert slack.destination() == 'UREVIEWER'
	monkeypatch.setenv('DSS_SLACK_POST_CHANNEL', DEVELOPERS)
	assert slack.destination() == DEVELOPERS


def test_bot_says_which_settings_to_set(monkeypatch):
	for name in slack.ENV:
		monkeypatch.delenv(name, raising=False)
	expected = (
		'Set DSS_SLACK_BOT_TOKEN, DSS_SLACK_APP_TOKEN, DSS_SLACK_POST_CHANNEL or '
		'DSS_SLACK_REVIEWER first'
	)
	with pytest.raises(SystemExit, match=expected):
		slack.main()


def test_bot_watches_the_test_channel_when_one_is_set(monkeypatch):
	monkeypatch.delenv('DSS_SLACK_CHANNEL', raising=False)
	assert slack.watched_channel() == CHANNEL
	monkeypatch.setenv('DSS_SLACK_CHANNEL', 'CTEST')
	assert slack.watched_channel() == 'CTEST'


def test_store_names_stay_inside_its_folder(tmp_path):
	store = AnswerStore(tmp_path)
	for channel, ts in (('..', '1.0'), (CHANNEL, '../1.0'), (CHANNEL, '1.0\\x')):
		with pytest.raises(ValueError, match='not a Slack channel'):
			store.get(channel, ts)
