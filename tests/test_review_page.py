"""The DSS Bot Review page (review/dss-bot-review.html) against the bot whose posts it reads."""

import json
import re
import shutil
import subprocess
from pathlib import Path

import pytest

from dss import record, slack

ROOT = Path(__file__).resolve().parents[1]
PAGE = ROOT / 'review' / 'dss-bot-review.html'
PARSER_TEST = ROOT / 'review' / 'slack_parse.test.js'
EXAMPLE = json.loads((ROOT / 'docs' / 'triage_record.example.json').read_text(encoding='utf-8'))


def test_the_page_splits_a_post_at_the_headings_the_bot_writes():
	triage = {
		**EXAMPLE['triage'],
		'actions': {**EXAMPLE['triage']['actions'], 'developers': ['Raise a DTN ticket']},
	}
	decision, proof = record.render(
		{'dss_ticket': {'key': 'DSS-9001', 'reporter': 'Ana'}, 'triage': triage}, []
	)
	page = PAGE.read_text(encoding='utf-8')
	heads = re.findall(r"\['(\*\*[^']+)', '\w+'\]", page)  # POST_HEADS and THREAD_HEADS
	assert len(heads) == 11
	for head in ('**Bot Summary:**', '**Data Dev Todos:**', '**Draft Reply:**'):
		assert head in heads and f'\n{head}' in decision, head
	for head in ('**Why:**', '**Past issues', '**Evidence**', '**Confidence:**'):
		assert head in heads and head in proof, head
	assert re.search(r'^\*\*.* Todos:\*\*$', decision, re.MULTILINE)  # the analyst's todos, by name


def test_the_page_leaves_out_the_buttons_the_bot_adds():
	page = PAGE.read_text(encoding='utf-8')
	assert f'const REVIEW_LINK = "{slack.REVIEW_TEXT}";' in page
	assert f"const COPY_LINK = '{slack.COPY_TEXT}';" in page


@pytest.mark.skipif(shutil.which('node') is None, reason='the page parser tests need Node')
def test_the_page_parser_passes_its_node_tests():
	result = subprocess.run(
		['node', '--test', str(PARSER_TEST)],
		capture_output=True,
		text=True,
		encoding='utf-8',
		timeout=60,
		check=False,
	)
	assert result.returncode == 0, result.stdout[-3000:]
