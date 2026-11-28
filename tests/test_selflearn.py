"""`python -m dss selflearn`: unreviewed tickets the developers answered in Slack."""

import json
import sys

import pytest

from dss import __main__ as cli
from dss import selflearn
from dss.learn import SELF

DEVS = 'CDEVS'
DAY = 86400
NOW = 1_900_000_000.0
DSS_TS = f'{NOW - 5 * DAY:.6f}'
POST_TS = f'{NOW - 5 * DAY + 60:.6f}'


class FakeSlack:
	"""Slack's read calls over one DSS thread and the bot's post about it."""

	def __init__(self, dss_replies: list[dict], dev_replies: list[dict]) -> None:
		head = {'ts': DSS_TS, 'bot_id': 'BWF', 'text': '<@UANA> created DSS-9001'}
		post = {
			'ts': POST_TS,
			'user': 'UBOT',
			'bot_id': 'B1',
			'text': 'DSS-9001 · Input problem · proved',
		}
		proof = {'ts': f'{float(POST_TS) + 1:.6f}', 'user': 'UBOT', 'bot_id': 'B1', 'text': 'Why'}
		self.threads = {DSS_TS: [head, *dss_replies], POST_TS: [post, proof, *dev_replies]}
		self.history = [post, {'ts': '1.0', 'user': 'UDEV', 'text': 'DSS-9000 · not the bot'}]

	def api_call(self, method, http_verb='POST', params=None):
		assert (method, http_verb) == ('auth.test', 'GET')
		return {'user_id': 'UBOT'}

	def conversations_history(self, **options):
		assert options['channel'] == DEVS and '.' not in options['oldest']
		return {'messages': self.history}

	def conversations_replies(self, **options):
		return {'messages': self.threads[options['ts']]}

	def users_info(self, user):
		return {'user': {'real_name': {'UANA': 'Ana', 'UDEV': 'Dev'}.get(user, user)}}


def said_by(user: str, days_ago: float, text: str) -> dict:
	return {'ts': f'{NOW - days_ago * DAY:.6f}', 'user': user, 'text': text}


@pytest.fixture
def answers(tmp_path, monkeypatch):
	"""The bot's saved answer to DSS-9001 and its trial record."""
	monkeypatch.setenv('DSS_SLACK_POST_CHANNEL', DEVS)
	monkeypatch.setattr(selflearn, 'ANSWERS', tmp_path / 'answers')
	trial = tmp_path / 'trial.json'
	trial.write_text(
		json.dumps(
			{
				'key': 'DSS-9001',
				'posted': '2030-03-12_00-00-00',
				'thread_ts': DSS_TS,
				'dm': 'DSS-9001 · Input problem · proved',
				'thread': '**Why:** The ACME tally says 4.778.',
			}
		),
		encoding='utf-8',
	)
	(tmp_path / 'answers').mkdir()
	answer = tmp_path / 'answers' / f'C0793LJ1KDM_{DSS_TS}.json'
	answer.write_text(json.dumps({'ticket': 'DSS-9001', 'trial': str(trial)}), encoding='utf-8')
	return tmp_path / 'export'


def write_review(export, review: dict) -> None:
	(export / 'reviews').mkdir(parents=True, exist_ok=True)
	(export / 'reviews' / 'DSS-9001.json').write_text(json.dumps(review), encoding='utf-8')


def test_a_quiet_answered_ticket_gets_both_threads(answers):
	client = FakeSlack(
		[said_by('UANA', 4.5, 'more logs'), said_by('UDEV', 4, 'the tally is stale')],
		[said_by('UDEV', 3, 'the bot missed the stale tally')],
	)
	lines = selflearn.gather(answers, NOW, client)
	assert lines == [f'DSS-9001: due, {answers / "self" / "DSS-9001.json"}']
	bundle = json.loads((answers / 'self' / 'DSS-9001.json').read_text(encoding='utf-8'))
	assert bundle['run'] == POST_TS
	assert (
		bundle['links']['bot']
		== f'https://darkvision.slack.com/archives/{DEVS}/p{POST_TS.replace(".", "")}'
	)
	assert [line['role'] for line in bundle['dss_thread']] == ['bot', 'analyst', 'helper']
	# the bot's own post and proof are in bot_post and bot_thread already
	assert [line['text'] for line in bundle['developers_channel_thread']] == [
		'the bot missed the stale tally'
	]
	assert bundle['bot_thread'] == '**Why:** The ACME tally says 4.778.'


@pytest.mark.parametrize(
	('dss_replies', 'review', 'reason'),
	[
		(
			[said_by('UDEV', 4, 'fixed')],
			{'sections': {'why': {'rating': 'partial', 'by': 'U1'}}},
			'a person reviewed it',
		),
		([said_by('UANA', 4, 'anyone?')], None, 'nobody but the analyst replied in the DSS thread'),
		([said_by('UDEV', 1, 'still looking')], None, 'a reply came in the last 2 days'),
		(
			[said_by('UDEV', 4, 'fixed')],
			{'source': SELF, 'by': SELF, 'updatedAt': '2030-03-15T00:00:00+00:00'},
			'learned already and no reply since',
		),
	],
)
def test_a_ticket_is_skipped_with_its_reason(answers, dss_replies, review, reason):
	if review is not None:
		write_review(answers, {'key': 'DSS-9001', **review})
	lines = selflearn.gather(answers, NOW, FakeSlack(dss_replies, []))
	assert lines == [f'DSS-9001: skipped, {reason}']


def test_a_self_learning_review_is_redone_after_new_replies(answers):
	write_review(
		answers,
		{'key': 'DSS-9001', 'source': SELF, 'by': SELF, 'updatedAt': '2030-03-12T00:00:00+00:00'},
	)
	lines = selflearn.gather(answers, NOW, FakeSlack([said_by('UDEV', 3, 'it was the casing')], []))
	assert lines[0].startswith('DSS-9001: due')
	bundle = json.loads((answers / 'self' / 'DSS-9001.json').read_text(encoding='utf-8'))
	assert bundle['review_before']['source'] == SELF


def good_review() -> dict:
	return {
		'key': 'DSS-9001',
		'run': POST_TS,
		'source': SELF,
		'by': SELF,
		'basis': 'Dev in the DSS thread: the tally is stale',
		'sections': {
			'why': {
				'rating': 'incorrect',
				'tags': ['wrong_cause'],
				'should': 'Stale tally',
				'by': SELF,
			},
			'headline': {'rating': 'correct', 'by': SELF},
		},
		'overall': {'act': 'no', 'harmful': False, 'by': SELF},
	}


def test_a_well_formed_review_passes_the_check():
	assert selflearn.problems(good_review(), 'DSS-9001', POST_TS) == []


def test_the_check_asks_what_the_page_asks_of_a_person():
	review = good_review()
	review['submittedAt'] = '2030-03-15T00:00:00Z'
	review['basis'] = ' '
	review['sections']['why'] = {'rating': 'partial', 'tags': ['made_up'], 'by': 'U1'}
	review['overall'] = {'harmful': True, 'by': SELF}
	assert selflearn.problems(review, 'DSS-9001', POST_TS) == [
		'a self-learning review is never submitted: a person submits',
		'basis must say which replies the review rests on',
		f'why: by must be {SELF!r}',
		'why: unknown reasons made_up',
		'why: a partial rating needs what it should say',
		'overall: say what harm it would do',
	]


def test_cli_check_fails_on_a_bad_review(answers, monkeypatch, capsys):
	selflearn.gather(answers, NOW, FakeSlack([said_by('UDEV', 4, 'fixed')], []))
	review = good_review()
	review['run'] = '1.0'
	(answers / 'self' / 'DSS-9001.review.json').write_text(json.dumps(review), encoding='utf-8')
	monkeypatch.setattr(sys, 'argv', ['dss', 'selflearn', str(answers), '--check'])
	monkeypatch.setattr(cli, 'load_env', lambda: None)
	with pytest.raises(SystemExit):
		cli.main()
	assert f'key and run must be DSS-9001 and its bundle run {POST_TS}' in capsys.readouterr().out
