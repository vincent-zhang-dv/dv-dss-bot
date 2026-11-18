"""`python -m dss learn`: the review page's submitted reviews as golden rows and a digest."""

import json
import re
import sys
from datetime import datetime
from pathlib import Path

import pytest

from dss import __main__ as cli
from dss import eval, learn

PAGE = Path(__file__).resolve().parents[1] / 'review' / 'dss-bot-review.html'
CREATED = '2027-01-15T16:00:00.000Z'


def ticket(key: str, why: str) -> dict:
	return {
		'key': key,
		'filed': {
			'summary': 'Wrong casing ID',
			'client': 'ACME',
			'dvt': '1234',
			'created': CREATED,
		},
		'post': [
			{'id': 'headline', 'md': f'**[{key}](https://slack/p1) · Input problem · proved**'}
		],
		'thread': [{'id': 'why', 'md': f'**Why:** {why} (`client info` row 3)'}],
	}


def export(tmp_path: Path) -> Path:
	folder = tmp_path / 'export'
	files = {
		'tickets/DSS-9001.json': ticket('DSS-9001', 'The tally says 4.778.'),
		'tickets/DSS-9002.json': ticket(
			'DSS-9002', 'The casing ID in [DVT1234](https://x/y) is stale.'
		),
		'tickets/DSS-9003.json': ticket('DSS-9003', 'Not checked yet.'),
		'reviews/DSS-9001.json': {
			'key': 'DSS-9001',
			'submittedAt': '2027-01-16T10:00:00Z',
			'sections': {
				'headline': {'rating': 'correct', 'by': 'u1'},
				'why': {
					'rating': 'partial',
					'tags': ['missed_known'],
					'should': ' The   DTN-9 fix\nwas not released. ',
					'by': 'u2',
				},
				'reply': {
					'rating': 'incorrect',
					'tags': ['wrong_fact'],
					'why': 'tone',
					'should': 'Say sorry',
					'by': 'u1',
				},
			},
			'overall': {'act': 'edits', 'note': 'close'},
		},
		'reviews/DSS-9002.json': {
			'key': 'DSS-9002',
			'submittedAt': '2027-01-17T10:00:00Z',
			'sections': {
				'why': {'rating': 'correct', 'by': 'u2'},
				'past': {
					'rating': 'incorrect',
					'tags': ['missed_known'],
					'should': 'DSS-8',
					'by': 'u2',
				},
			},
			'overall': {
				'act': 'no',
				'harmful': True,
				'note': 'sends the analyst to the wrong file',
			},
		},
		'reviews/DSS-9003.json': {
			'key': 'DSS-9003',
			'sections': {'why': {'rating': 'partial', 'tags': ['vague']}},
		},
		'reviews/u_old.DSS-9001.json': {'key': 'DSS-9001', 'session': 'u_old', 'sections': {}},
	}
	for name, body in files.items():
		path = folder / name
		path.parent.mkdir(parents=True, exist_ok=True)
		path.write_text(json.dumps(body), encoding='utf-8')
	return folder


def test_each_submitted_review_becomes_a_golden_row_eval_reads(tmp_path):
	golden = tmp_path / 'golden.jsonl'
	learn.run(export(tmp_path), golden)
	rows = eval.load_golden(golden)
	posted = datetime.fromisoformat(CREATED).astimezone().strftime('%Y-%m-%d_%H-%M-%S')
	assert rows == [
		{
			'key': 'DSS-9001',
			'posted': posted,
			# what the reviewers say Why should say
			'outcome': 'The DTN-9 fix was not released.',
			'source_of_truth': 'DSS Bot Review page, submitted 2027-01-16',
		},
		{
			'key': 'DSS-9002',
			'posted': posted,
			# the bot's Why, rated Correct
			'outcome': 'The casing ID in DVT1234 is stale. (client info row 3)',
			'source_of_truth': 'DSS Bot Review page, submitted 2027-01-17',
		},
	]


def test_the_digest_names_the_reasons_worth_a_fix(tmp_path):
	lines = learn.run(export(tmp_path), tmp_path / 'golden.jsonl')
	text = '\n'.join(lines)
	# the old per-reviewer review of DSS-9001 is left out
	assert lines[0] == 'Reviews: 2 submitted of 3; 3 tickets on the page'
	assert lines[1] == 'Sections: correct 2 · partial 1 · incorrect 2'
	assert '  Missed a known issue: 2 tickets (DSS-9001, DSS-9002)' in lines
	worth = text.split('Worth a fix')[1].split('Corrections:')[0]
	assert '  - Missed a known issue: DSS-9001, DSS-9002' in worth
	assert 'harmful, DSS-9002: sends the analyst to the wrong file' in worth
	assert 'Wrong fact' not in worth  # one ticket is not a pattern yet
	assert 'DSS-9002 · would act on it: No · HARMFUL' in lines
	assert '  Draft Reply (incorrect; Wrong fact; Other reason: tone): Say sorry' in lines
	assert 'DSS-9003' not in text.split('Corrections:')[1]  # not submitted


def test_a_review_without_an_outcome_or_ticket_is_named_not_written(tmp_path):
	folder = export(tmp_path)
	(folder / 'tickets' / 'DSS-9002.json').unlink()
	golden = tmp_path / 'golden.jsonl'
	lines = learn.run(folder, golden)
	assert [row['key'] for row in eval.load_golden(golden)] == ['DSS-9001']
	assert 'No golden row (no ticket, or no outcome given): DSS-9002' in lines


def test_learn_knows_the_page_sections_and_reasons():
	page = PAGE.read_text(encoding='utf-8')
	sections = re.search(r'const SECTIONS = \{(.*?)\n\};', page, re.DOTALL).group(1)
	assert re.findall(r"^\t(\w+): \{\n\t\ttitle: '([^']+)'", sections, re.MULTILINE) == list(
		learn.SECTIONS.items()
	)
	block = re.search(r'const TAGS = \[(.*?)\n\];', page, re.DOTALL).group(1)
	found = re.findall(r"""^\t\['(\w+)', (?:'([^']+)'|"([^"]+)"), """, block, re.MULTILINE)
	tags = {key: single or double for key, single, double in found}
	assert {**tags, 'other': 'Other reason'} == learn.TAGS
	assert (
		dict(re.findall(r"\['(\w+)', '([^']+)', '(?:correct|partial|incorrect)'\]", page))
		== learn.ACTS
	)


def test_a_ticket_without_its_filing_time_gives_no_row(tmp_path):
	folder = export(tmp_path)
	broken = ticket('DSS-9002', 'x')
	del broken['filed']['created']
	(folder / 'tickets' / 'DSS-9002.json').write_text(json.dumps(broken), encoding='utf-8')
	lines = learn.run(folder, tmp_path / 'golden.jsonl')
	assert 'No golden row (no ticket, or no outcome given): DSS-9002' in lines


def test_a_wrong_export_folder_stops_and_keeps_the_golden_rows(tmp_path, monkeypatch):
	golden = tmp_path / 'golden.jsonl'
	golden.write_text('{"key": "DSS-9001"}\n', encoding='utf-8')
	monkeypatch.setattr(learn, 'GOLDEN', golden)
	monkeypatch.setattr(sys, 'argv', ['dss', 'learn', str(tmp_path / 'no-such-export')])
	monkeypatch.setattr(cli, 'load_env', lambda: None)
	with pytest.raises(SystemExit, match='cannot read the export: no reviews folder'):
		cli.main()
	assert golden.read_text(encoding='utf-8') == '{"key": "DSS-9001"}\n'


def test_cli_learn_prints_the_digest(tmp_path, monkeypatch, capsys):
	folder = export(tmp_path)
	monkeypatch.setattr(learn, 'GOLDEN', tmp_path / 'golden.jsonl')
	monkeypatch.setattr(sys, 'argv', ['dss', 'learn', str(folder)])
	monkeypatch.setattr(cli, 'load_env', lambda: None)
	cli.main()
	assert capsys.readouterr().out.startswith('Reviews: 2 submitted of 3')
	assert (tmp_path / 'golden.jsonl').exists()
