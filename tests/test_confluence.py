"""Confluence read tools against a fake Atlassian API."""

import io
import json
import urllib.error
from datetime import UTC, datetime, timedelta
from email.message import Message

import pytest

from dss import confluence


@pytest.fixture
def site(monkeypatch):
	for name, value in zip(confluence.ENV, ('https://dv.atlassian.net/wiki/', 'a@dv', 't')):
		monkeypatch.setenv(name, value)
	monkeypatch.setenv('CONFLUENCE_SPACES', 'DD, OPS')
	requests = []

	def answer(request, timeout):
		requests.append(request)
		if '/pages/404' in request.full_url:
			raise urllib.error.HTTPError(request.full_url, 404, 'Not Found', Message(), None)
		body: dict
		if '/search' in request.full_url:
			body = {
				'results': [
					{
						'content': {'id': '42'},
						'title': 'Pup joints SOP',
						'excerpt': '@@@hl@@@pup@@@endhl@@@ joints &amp; collars',
						'resultGlobalContainer': {'title': 'Data Dev'},
						'url': '/spaces/DD/pages/42',
					}
				]
			}
		else:
			body = {
				'title': 'Pup joints SOP',
				'version': {'createdAt': '2026-09-01'},
				'_links': {'webui': '/spaces/DD/pages/42'},
				'body': {
					'storage': {
						'value': '<h2>Steps</h2><ul><li>Find <b>pups</b></li></ul>'
						'<table><tr><th>OD</th><td>5.5</td></tr></table>'
					}
				},
			}
		return io.BytesIO(json.dumps(body).encode())

	monkeypatch.setattr(confluence.urllib.request, 'urlopen', answer)
	return requests


def test_search_and_page(site):
	(hit,) = confluence.search('pup joints')
	assert hit == {
		'id': '42',
		'title': 'Pup joints SOP',
		'space': 'Data Dev',
		'excerpt': 'pup joints & collars',
		'updated': '',
		'url': 'https://dv.atlassian.net/wiki/spaces/DD/pages/42',
	}
	cql = site[0].full_url
	assert 'space+in+%28%22DD%22%2C+%22OPS%22%29' in cql and site[0].headers['Authorization']
	page = confluence.page('42')
	assert page['text'] == '## Steps\n\n- Find pups\n\n| OD | 5.5'
	assert page['url'].endswith('/wiki/spaces/DD/pages/42')


def test_refusals(site, monkeypatch):
	with pytest.raises(confluence.ConfluenceError, match='no such page'):
		confluence.page('404')
	for bad in ('../x', '', '42; drop'):
		with pytest.raises(confluence.ConfluenceError, match='page id'):
			confluence.page(bad)
	with pytest.raises(confluence.ConfluenceError, match='empty'):
		confluence.search('  ')
	monkeypatch.delenv('CONFLUENCE_TOKEN')
	with pytest.raises(confluence.ConfluenceError, match='set CONFLUENCE_TOKEN'):
		confluence.search('x')


def test_long_pages_are_cut_and_outages_explained(site, monkeypatch):
	monkeypatch.setattr(confluence, 'MAX_PAGE_CHARS', 10)
	assert confluence.page('42')['text'].endswith('\n[... cut at 10 characters]')

	def unreachable(request, timeout):
		raise urllib.error.URLError('no route to host')

	monkeypatch.setattr(confluence.urllib.request, 'urlopen', unreachable)
	with pytest.raises(confluence.ConfluenceError, match='cannot reach Confluence'):
		confluence.search('pup joints')


def test_tools_answer_through_mcp(site, mcp_call):
	failed, found = mcp_call('confluence_search', {'text': 'pup joints', 'limit': 3})
	assert not failed and 'Pup joints SOP' in found
	failed, text = mcp_call('confluence_page', {'page_id': '42'})
	assert not failed and 'Find pups' in text


def test_without_named_spaces_the_search_covers_every_space(site, monkeypatch):
	monkeypatch.delenv('CONFLUENCE_SPACES')
	confluence.search('pup joints')
	assert 'space' not in site[-1].full_url


def test_a_page_not_edited_for_over_a_year_may_be_out_of_date(monkeypatch):
	now = datetime.now(UTC)
	edited = {'400': now - timedelta(days=400), '30': now - timedelta(days=30)}  # days ago

	def get(path, params):
		if path.endswith('/search'):
			hits = []
			for page_id, moment in edited.items():
				hits.append({'content': {'id': page_id}, 'lastModified': moment.isoformat()})
			return {'results': hits}
		version = {'createdAt': edited[path.rsplit('/', 1)[1]].isoformat()}
		return {'title': 'SOP', 'body': {'storage': {'value': '<p>steps</p>'}}, 'version': version}

	monkeypatch.setattr(confluence, '_get', get)
	note = confluence.page('400')['may_be_out_of_date']
	assert note == f'last edited {edited["400"].astimezone():%Y-%m-%d}, 1.1 years before the ticket'
	assert 'may_be_out_of_date' not in confluence.page('30')
	hits = {}
	for hit in confluence.search('casing tally'):
		hits[hit['id']] = hit
	assert hits['400']['may_be_out_of_date'] == note
	assert 'may_be_out_of_date' not in hits['30']
