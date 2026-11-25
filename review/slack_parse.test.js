// The DSS Bot Review page's Slack parser, run on made-up connector output:
// `node --test review/slack_parse.test.js` (also run by tests/test_review_page.py).
const assert = require('node:assert');
const fs = require('node:fs');
const path = require('node:path');
const test = require('node:test');

const PAGE = fs.readFileSync(path.join(__dirname, 'dss-bot-review.html'), 'utf8');
const BLOCK = PAGE.slice(PAGE.indexOf('/* parse:start */'), PAGE.indexOf('/* parse:end */'));
const SECTIONS = Object.fromEntries(
	['headline', 'summary', 'analyst_todos', 'dev_todos', 'reply', 'why', 'past', 'evidence', 'confidence'].map((id) => [id, {}]),
);
const parser = new Function('SECTIONS', `${BLOCK}; return { fromSlack, threadMessages, channelMessages, botPost, buildTicket, usableTicket, BOT_CHANNELS };`)(SECTIONS);

const DSS_TS = '1800000000.000100';
const BOT_TS = '1800000090.000200';
const LINK = 'https://darkvision.slack.com/archives/C0793LJ1KDM/p1800000000000100?thread_ts=1800000000.000100&cid=C0793LJ1KDM';
const BOT = 'DDEV Support Bot <botuser-x@slack-bots.com> (U0CCHQUDX5E)';

function message(from, ts, text) {
	return `From: ${from}\nTime: 2027-01-15 09:00:00 MST\nMessage TS: ${ts}\n${text}`;
}
// as the connector now shows a read: '> ' before each line of each message body
function quoted(read) {
	const lines = [];
	for (const line of read.split('\n')) {
		const frame = line === '' || /^(From|Time|Message TS): |^(===|---) /.test(line);
		lines.push(frame ? line : `> ${line}`);
	}
	return lines.join('\n');
}
function thread(parent, replies) {
	let text = `=== THREAD PARENT MESSAGE ===\n${parent}\n\n=== THREAD REPLIES (${replies.length} total) ===\n`;
	replies.forEach((reply, index) => {
		text += `\n--- Reply ${index + 1} of ${replies.length} ---\n${reply}\n`;
	});
	return text;
}
const POST = [
	`*<${LINK}|DSS-9001> · Input problem: casing ID differs from the tally · proved*`,
	'*Bot Summary:* The analyst sees a wrong ID for ACME on DVT1234.',
	'*Data Dev Todos:*',
	'1. Confirm: Read the tally.',
	'*Draft Reply:*',
	'> Set the ID to 4.778 in the client info.',
	'_Draft by Claude · 12,345 tokens · ≈ $0.21 · 42 s_',
	"Please review the bot's report button",  // how the Slack connector shows the review button
	'Edit &amp; copy draft button',
	`Forwarded message from Data Support Request (${LINK}): <@UANA|ana> created DSS-9001`,
].join('\n');
const PROOF = '*Why:* The tally says 4.778.\n*Evidence*\n1. Tally row 3 (`cerb_read_sheet:ACME`)\n*Confidence:* high';
const BOT_THREAD = thread(message(BOT, BOT_TS, POST), [
	message(BOT, '1800000090.000300', PROOF),
	message('dev <dev@x.com> (UDEV1)', '1800000500.000100', 'a comment on the post'),
]);
const DSS_THREAD = thread(
	message('Data Support Request (B07N3137WUU)', DSS_TS,
		'<@UANA|ana> created DSS-9001\n*Client:* ACME\n*DVT*: 1234\n*Summary*: Wrong casing ID\n*Internal review due date*: 2027-01-20'),
	[
		message('', '1800000001.000100', 'Screenshot of error:\nFiles: shot.png (ID: F1, image/png, 1 KB)'),
		message('ana <ana@x.com> (UANA)', '1800000030.000100', 'it says *4.5*'),
		message('dev <dev@x.com> (UDEV1)', '1800000300.000100', 'looking <@UANA|ana>'),
		message('Jira <botuser-y@slack-bots.com> (UJIRA)', '1800000400.000100', 'Status: *To Do*'),
		message('dev <dev@x.com> (UDEV1)', '1800003900.000100', 'fixed, see <https://example.com/pr/1|PR 1>\nReactions: tada (1)'),
	],
);
const POST_INFO = { key: 'DSS-9001', ts: BOT_TS, dssTs: DSS_TS, latest: '', channel: { id: 'C0CF1J345EY', name: 'data-dev-support-assist-test' } };

test('Slack markup becomes the page Markdown', () => {
	assert.strictEqual(
		parser.fromSlack('*Bold:* <https://a.b/c|label> <@U1|ana> <https://a.b> a &amp; b'),
		'**Bold:** [label](https://a.b/c) @ana https://a.b a & b',
	);
});

test('only the bot live posts from DSS-1256 on are tickets', () => {
	const head = (ts, from) => `=== Message from ${from} at 2027-01-15 09:00:00 MST === \nMessage TS: ${ts}\n`;
	const page = [
		`${head('1800000090.000200', BOT)}*<${LINK}|DSS-9001> · A · proved*\nThread: 2 replies (latest: 2027-01-15 09:10:00 MST)\n`,
		`${head('1800000091.000200', BOT)}*<${LINK}|DSS-1200> · old*\n`,
		`${head('1800000092.000200', BOT)}*<${LINK}|DSS-9002> · B*\n_Replay of DSS-9002 as posted_\n`,
		`${head('1800000093.000200', 'dev <dev@x.com> (UDEV1)')}*<${LINK}|DSS-9003> · quoted by a person*\n`,
	].join('\n');
	const messages = parser.channelMessages([page]);
	assert.strictEqual(messages.length, 4);
	const posts = messages.map((item) => parser.botPost(item, parser.BOT_CHANNELS[0])).filter(Boolean);
	assert.deepStrictEqual(posts.map((post) => post.key), ['DSS-9001']);
	assert.strictEqual(posts[0].dssTs, DSS_TS);
	assert.strictEqual(posts[0].latest, '2027-01-15 09:10:00 MST');
});

test('a thread read in pages keeps each message once, oldest first', () => {
	const messages = parser.threadMessages([DSS_THREAD, DSS_THREAD]);
	assert.deepStrictEqual(messages.map((item) => item.ts), [DSS_TS, '1800000001.000100', '1800000030.000100', '1800000300.000100', '1800000400.000100', '1800003900.000100']);
	assert.deepStrictEqual(messages[1].files, ['shot.png']);
	assert.ok(!messages[5].text.includes('Reactions'));
});

test('a ticket keeps the post sections, the proof and the real thread', () => {
	const ticket = parser.buildTicket(POST_INFO, parser.threadMessages([BOT_THREAD]), parser.threadMessages([DSS_THREAD]));
	const post = Object.fromEntries(ticket.post.map((part) => [part.id, part.md]));
	assert.ok(post.headline.startsWith('**[DSS-9001](https://darkvision.slack.com/'));
	assert.ok(post.summary.startsWith('**Bot Summary:** The analyst'));
	assert.strictEqual(post.analyst_todos, null);  // a rated section the post left out stays, empty
	assert.strictEqual(post.dev_todos, '**Data Dev Todos:**\n1. Confirm: Read the tally.');
	assert.strictEqual(post.reply, '**Draft Reply:**\n> Set the ID to 4.778 in the client info.');
	assert.ok(post.footer.startsWith('_Draft by Claude'));
	const all = JSON.stringify(ticket.post);
	assert.ok(!all.includes('Please review') && !all.includes('copy draft') && !all.includes('Forwarded message'));
	const proof = Object.fromEntries(ticket.thread.map((part) => [part.id, part.md]));
	assert.deepStrictEqual(Object.keys(proof), ['why', 'past', 'evidence', 'confidence']);
	assert.strictEqual(proof.past, null);
	assert.ok(!JSON.stringify(ticket).includes('a comment on the post'));  // only the real thread is kept
	assert.deepStrictEqual(ticket.filed, { summary: 'Wrong casing ID', client: 'ACME', dvt: '1234', due: '2027-01-20', reporter: 'ana', created: new Date(1800000000000.1).toISOString() });
	const roles = ticket.real.map((item) => (item.marker ? 'BOT' : `${item.who}:${item.role}`));
	assert.deepStrictEqual(roles, ['Data Support Request:workflow', 'Data Support Request:workflow', 'ana:analyst', 'BOT', 'dev:other', 'Jira:workflow', 'dev:other']);
	assert.strictEqual(ticket.real[2].text, 'it says **4.5**');
	assert.deepStrictEqual(ticket.time.bot, { tokens: 12345, cost_usd: 0.21, seconds: 42 });
	assert.strictEqual(ticket.time.developers.replies, 2);
	assert.strictEqual(ticket.time.developers.response_minutes, 5);
	assert.strictEqual(ticket.time.developers.solving_minutes, 60);
	assert.strictEqual(ticket.botLink, 'https://darkvision.slack.com/archives/C0CF1J345EY/p1800000090000200');
});

test('the page shows only tickets with Slack links and the parts it reads', () => {
	const ticket = parser.buildTicket(POST_INFO, parser.threadMessages([BOT_THREAD]), parser.threadMessages([DSS_THREAD]));
	assert.ok(parser.usableTicket(ticket));
	assert.ok(!parser.usableTicket({ ...ticket, link: 'javascript:alert(1)' }));
	assert.ok(!parser.usableTicket({ ...ticket, botLink: 'https://example.com/p1' }));
	assert.ok(!parser.usableTicket({ ...ticket, filed: undefined }));
	assert.ok(!parser.usableTicket({ ...ticket, thread: null }));
	assert.ok(!parser.usableTicket({ ...ticket, key: 'x' }));
});

test('a post missing from its thread is an error, not an empty ticket', () => {
	assert.throws(() => parser.buildTicket(POST_INFO, [], []), /not in its thread/);
});

test('message bodies the connector quotes read as unquoted ones', () => {
	const page = quoted(`=== Message from ${BOT} at 2027-01-15 09:00:00 MST === \nMessage TS: ${BOT_TS}\n${POST}\nThread: 2 replies (latest: 2027-01-15 09:10:00 MST)\n`);
	assert.ok(page.includes('\n> *<https://'));
	const [post] = parser.channelMessages([page]).map((item) => parser.botPost(item, parser.BOT_CHANNELS[0]));
	assert.deepStrictEqual(post, { ...POST_INFO, latest: '2027-01-15 09:10:00 MST', channel: parser.BOT_CHANNELS[0] });
	const plain = parser.buildTicket(POST_INFO, parser.threadMessages([BOT_THREAD]), parser.threadMessages([DSS_THREAD]));
	const fromQuoted = parser.buildTicket(POST_INFO, parser.threadMessages([quoted(BOT_THREAD)]), parser.threadMessages([quoted(DSS_THREAD)]));
	assert.ok(quoted(DSS_THREAD).includes('\n> it says *4.5*'));
	assert.deepStrictEqual(fromQuoted, plain);
});
