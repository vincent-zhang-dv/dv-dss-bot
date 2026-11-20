# dv-dss-bot

Triages new DSS (Data Support) tickets. When a support-request workflow posts "created DSS-…" in #data-dev-support, a locked-down, read-only Claude works through the ticket and sends its analysis to a data developer's Slack DM. Nothing is posted in the channel and nothing is written anywhere.

> **Principle:** Claude reads and proves; a data developer decides what reaches the analyst.

Owners: Vincent Zhang and Annika. Status log: [docs/STATUS.md](docs/STATUS.md).

## How it works

```
#data-dev-support: a workflow posts "created DSS-1234"
  -> dss/slack.py    new ticket, triaged 15 s after the post so its thread holds the workflow's
                     screenshot (on start, also any from the last 24 h with no saved answer)
  -> dss/record.py   the checked record: ticket facts and Gate C's inputs read by code and
                     handed to Claude, the prompt, the check
  -> dss/triage.py   the prompt: ticket key, time posted, the workflow's message, its thread
  -> dss/runner.py   claude -p with no user setup and no built-in tools; system prompt dss/triage.md
  -> dss/server.py   MCP server `dss`, read-only tools:
       slack_thread  dss/thread.py      the ticket's thread: replies, screenshots (Slack API)
       jira_*        dss/jira.py        ticket fields, similar tickets (Jira REST API)
       cerb_*        dss/cerb.py        project folder, telemetry, files (project index);
                                        client info (Cerberus venv)
       code_*        dss/code.py        Cerberus source at the prod release commit (git)
       matlab_*      dss/matlab.py      MATLAB CAT, which Cerberus was ported from (git)
       vizapp_*      dss/vizapp.py      VizApp code and release notes, DVCode (GitHub API)
       confluence_*  dss/confluence.py  SOPs and how-tos
  -> #data-dev-support-assist: the decision, with the proof in its thread (never #data-dev-support);
     the record is saved in out/trial/, and out/answers/ notes which tickets are done
```

The triage flow is the system prompt, [dss/triage.md](dss/triage.md). Claude first states the request, then searches past issues: DSS and DTN tickets, git history and branches, and troubleshooting pages. It then checks whether a past fix applies to this analyst's build. Only after that does it investigate. It goes through Gate A (is it a Cerberus issue?), Gate B (is it a known setup problem?) and Gate C (are the inputs there?), then follows one playbook for the symptom. Only proved results go under "Checked"; everything else goes under "Not proved", with what would settle it. Trial mode enforces the order: the record must state the request and the past issues, and the run's trace must show the Jira and git searches before any code or project file is read. The `dss` server holds those reads back until both searches have run (`DSS_STEP_ORDER`, set by the runner), so a run cannot fail the order check by reading too early.

| Tool | Reads |
|---|---|
| `slack_thread` | The ticket's Slack thread: the workflow's post and every reply, oldest first, with screenshots as images and text files as text. Only threads of "created DSS-…" posts in the watched channel. |
| `jira_get_issue`, `jira_search` | A ticket's triage fields, with its resolution, fix versions (released or not), newest comments and attachment names; and similar tickets with their resolution and fix versions. |
| `cerb_find_project`, `cerb_list`, `cerb_read_text` | The DVT's project folder, a folder's entries, and a text file in 400-line pages, through the project index. |
| `cerb_last_session` | The reporter's newest Cerberus telemetry log before the ticket: version, failed script runs, last saved config. From Cerberus 1.1 on, also the session's terminal log (`cerberus\terminal_log`, named like the telemetry log): its errors before the ticket, each with the end of its traceback, and its warnings, with line numbers. Before 1.1 there is no terminal log. |
| `cerb_client_info` | A `client_info.xlsm` as Cerberus reads it. A joint column that holds one value for every joint is given once under `every_joint`, not once per joint; no value is left out. |
| `cerb_read_sheet` | Any `.xlsx` or `.xlsm` on the share (statistics spreadsheets, other client info sheets): its sheets and sizes, then 100-row pages with Excel's row numbers, optionally only the rows containing a text. Copied through the index and read with openpyxl in the Cerberus venv. |
| `cerb_read_document` | The text of a `.pdf` or `.docx` on the share (deliverable reports, intro pages): up to 20 pages per answer (Word in parts of about 3,000 characters), optionally only the pages containing a text. Pages that are only a picture show no text. Read with pypdf and python-docx in the Cerberus venv. |
| `cerb_read_image` | An image on the share (`.png`, `.jpg` and other bitmaps), or the pictures in one page of a PDF on the share (plots `cerb_read_document` can't read), given to Claude as images: scaled to 1568 pixels on the long side, logos left out, at most 6 per page. Read with Pillow and pypdf in the Cerberus venv. |
| `cerb_read_table` | A `.parquet` file, or a table of a SQLite `.db` (opened read-only; `Thumbs.db` is refused), paged like a sheet with row 1 the column names. Pickle files are never read: loading one runs code. |
| `cerb_read_caliper` | A `.dvcaliper` or caliper `.npy`, read by the prod Cerberus's own reader: type, size, depth range, and for a depth window (m) up to 60 slices evenly spread plus the 10 highest, with a picture of the window. A `.dvcaliper` is read by HTTP ranges: only the window is fetched (at most 20,000 slices), so a 4 GB file is never copied. |
| `cerb_prod_version` | The version of the prod Cerberus wheel. |
| `code_grep`, `code_read`, `code_log_s` | Cerberus source through git at the prod release commit, never the working tree. |
| `code_log`, `code_branches`, `code_contains` | Cerberus history: recent commits on a file or folder, branches named with a DTN key or word, and whether a build (prod, a release or the analyst's UAT build) includes a commit. Git reads only. |
| `matlab_grep`, `matlab_read`, `matlab_log` | MATLAB CAT (DataAnalysis), which Cerberus was ported from, through git at origin/master: what a step did before the port, and when a behaviour came in. A second reference for intended behaviour beside the Confluence SOPs; the two can disagree, and the rules send the decision to the developers when they do. |
| `vizapp_search`, `vizapp_read` | VizApp's code and release notes (DVCode), which writes the dvcalipers and bookmarks Cerberus reads: GitHub code search of master, and one file at master (in a replay, at master's last commit before the post), through the gh CLI, GET only. DVCode is over 4 GB, so the bot keeps no clone. When VizApp is the cause, `triage.md` sends the analyst to #dh-ask-support. |
| `confluence_search`, `confluence_page` | Confluence pages. A page last edited over a year before the ticket says so (`may_be_out_of_date`), and `triage.md` has Claude check it against the code and not settle anything on it alone. |

`cerb_client_info` and `cerb_read_sheet` run in the Cerberus venv through [dss/worker.py](dss/worker.py), so this repo needs none of its dependencies.

## Repository layout

| Path | What it does |
|---|---|
| `dss/__main__.py` | CLI: `triage`, `trial`, `replay`, `eval`, `cerberus`, `slack`, `mcp`. Reads `.env`. |
| `dss/triage.md` | The triage system prompt. Most behaviour changes happen here. |
| `dss/triage.py` | One triage: prompt, Claude run, tokens and cost. |
| `dss/eval.py`, `docs/golden.example.jsonl` | Scores saved answers against the golden set; a made-up golden row. |
| `dss/learn.py` | The review page's submitted reviews as golden rows and a digest of what reviewers corrected. |
| `docs/triage_record.schema.json`, `docs/triage_record.example.json`, `docs/known_causes.json` | The triage record's schema (a proposal), a made-up example, and a draft list of known causes. Only trial mode uses them. |
| `dss/record.py`, `dss/triage_record.md` | Trial mode: the triage as a checked record, and the answer section that replaces `triage.md`'s. |
| `dss/replay.py`, `dss/cutoff.py` | Replay mode: old tickets triaged as they stood when posted, and the cutoff every tool answers by. |
| `dss/runner.py` | The locked-down `claude -p` command line; the system prompt goes in a file. |
| `dss/server.py` | The `dss` MCP server. |
| `dss/thread.py`, `jira.py`, `cerb.py`, `code.py`, `confluence.py` | The tools, one module per source. |
| `dss/worker.py` | Runs a function in another app's venv. |
| `dss/slack.py`, `dss/store.py` | The Slack bot and its saved answers. |
| `slack_manifest.yaml` | The bot's Slack app. |
| `review/` | The DSS Bot Review page, where developers rate the bot's posts, and its parser tests (Node). |
| `tests/` | pytest suite. External services are faked. |
| `CLAUDE.md` | Rules for Claude Code sessions in this repo. |

## Setup

Requirements: Windows, Python 3.11 and [uv](https://docs.astral.sh/uv/).

1. `uv sync`
2. Install the `claude` CLI and log in once (`claude`). Triages run under that login.
3. **Cerberus:** a checkout of `darkvisiontech/DataDHPython` with its venv. Fetch it regularly, so the `code_*` tools see new release commits.
4. **Project index:** your own login. The approval covers read-only use on DSS tickets. Check that it covers you before you use it.
5. **Jira and Confluence:** a read-only account's Atlassian API token. One token serves both.
6. `copy .env.example .env` and fill it in. `.env` is git-ignored.

Check the setup with one triage: `uv run python -m dss triage DSS-1234`.

## Usage

```powershell
uv run python -m dss triage DSS-1234                                 # print one analysis; no Slack
uv run python -m dss triage DSS-1234 --posted 2026-10-21_09-30-00    # an older ticket
uv run python -m dss triage DSS-1234 --post-file post.txt            # with the workflow's Slack post
uv run python -m dss triage DSS-1234 --thread <permalink of the post> # with its Slack thread
uv run python -m dss trial DSS-1234 --thread ... --post-file ...     # the same triage as a checked record
uv run python -m dss replay DSS-1229 DSS-1101                        # old tickets as posted: the plan only
uv run python -m dss replay DSS-1229 --go [--post-test]              # run them (see Replay mode)
uv run python -m dss slack                                           # the bot
```

The analysis goes to stdout, and turns, tokens and cost to stderr. `--posted` is the ticket time, in local time, and defaults to now. `cerb_last_session` uses it to pick the reporter's last session before the ticket, so pass it for an older ticket. `--post-file` is a text file with the support-request workflow's post (summary, client, DVT), which the Slack bot passes on its own; it gives Claude the ticket facts when Jira is down.

`--thread` takes the workflow post's permalink (or its ts) and lets Claude read the thread with `slack_thread`, as the bot does. `--posted` then defaults to the post's time. Replies posted more than 15 seconds after the post are left out, so a triage of an older ticket sees what the bot would have seen, not the developers' answers. It needs `DSS_SLACK_BOT_TOKEN`; the bot token alone starts no bot.

Every triage, from the CLI or the bot, adds a row to `out/usage.csv`: run time, ticket, turns, seconds, tokens by kind and cost. With `DSS_TRACE=1`, its tool calls also go to `out/trace/<key>_<run time>.jsonl`, one JSON line each: tool, arguments, seconds, and the error or the result's size and first 2,000 characters. Both hold client data and stay in the git-ignored `out/`.

### Trial mode

`python -m dss trial` runs the triage record design (the DSS Triage Flow page) by hand, without changing the bot. It takes the same arguments as `triage` and runs the same locked-down Claude with the same steps and rules. Five things differ:

- Before Claude starts, code reads the `dss_ticket` block: the reporter, DVT, project folder, workflow, Toolbox Name, the reporter's Cerberus version, last failed run and terminal log (1.1 and later), and the prod version. Code also lists the project folder and checks Gate C's inputs for the job type (`inputs`: the client info file and `Bookmarks` for every job, the baseline calculation for PE, `DVCaliper_Files` for WI and CI), by the names Cerberus' project picker looks for. A source that fails leaves its facts null and is noted; it doesn't stop the triage.
- The prompt gives Claude what code read (the Jira issue, the last session, the project listing), each as the tool's own answer, and tells it not to make those calls again. The trace keeps them as lines marked `by: code`: a replay's leak check covers them, and Claude's call count and the step-order check leave them out. When both versions are release numbers, the prompt also gives Gate B's comparison of the reporter's Cerberus version with prod (older, newer, the same, or two builds of one release), made by code.
- Claude answers with one JSON object (the `triage` and `class` blocks) through claude's structured output (`--json-schema`). Code builds that schema from `docs/triage_record.schema.json` (`record.answer_schema`); each field's description is what Claude reads about that field, and claude asks Claude again until the answer fits. [dss/triage_record.md](dss/triage_record.md) replaces `triage.md`'s Markdown answer section with the few rules that aren't about one field, and the known causes that can apply to the ticket's workflow.
- Code checks the record against `docs/triage_record.schema.json`.
- Code renders a post (the decision) and a thread reply (the proof), as the Slack bot posts them (below). An answer that fails the check is shown as given, with the problems.

The command prints the DM, then the thread reply. Each trial is saved as `out/trial/<key>_<run time>.json`, never in `out/answers/`, the folder the bot reads to tell which tickets are done. The trial doesn't post to Slack; whoever runs it delivers the DM and the thread reply to the reviewer.

### Replay mode

`python -m dss replay <keys> [--golden out/eval/golden.jsonl]` triages existing DSS tickets as they stood when the workflow posted them, so an answer can't come from what happened later. It finds each ticket's "created DSS-…" post in #data-dev-support, takes the post's time as the cutoff, and runs a trial in which every tool answers as of then:

| Source | What a replay sees |
|---|---|
| Slack | The ticket's thread as raised: the request, the workflow's first reply "Screenshot of error:" with the analyst's screenshot, the reminder, and whatever the analyst who raised it (the @mention in "@… created DSS-…") added in the first 30 minutes, up to the first reply from anyone else. Other tickets' threads show only what was posted before the ticket; threads posted later are refused. A post edited since says so. |
| Cerberus code | The analyst's build, from their last session before the post; without one, the release that was prod then. History is what GitHub held at the post: GitHub's push history (its activity API, through `gh`, kept in `out/archive/github_activity.json`) gives each branch's commit then, so history searches walk only those, `code_branches` lists the branches of then (deleted ones too, when the clone has them), and a commit made before the post but pushed after it is refused. Without the push history, commit dates stand in. `cerb_client_info` runs the analyst's build, exported from git (no wheel is built). |
| Prod version | The newest release that was out by then: a release counts from the latest of its release commit, the day after its Jira release date (DTN's `CERBERUS v…` versions) and, for today's wheel, when the wheel was written. |
| Jira | Each ticket as it stood then, rebuilt from its whole changelog (status, resolution, Toolbox Name, Investigation Outcome, links, fix versions by name only, description). The replayed ticket shows no comments; other tickets show only comments written before the post, and a comment edited since shows no text. Tickets created later are refused. Searches may test only what a ticket keeps once filed (project, key, type, created, reporter, summary, description), with absolute dates, sorted by created or key: `status = Done`, the Outcome, links, functions such as `linkedIssues()` and dates such as `-30d` would pick tickets by what happened later, so they are refused. `text ~` searches only the summary and description, and as Jira matches today's text, a hit edited since is kept only if its text then has the searched words. |
| Project share | Sessions started later are left out, and log lines from the post on. A listing marks files changed since (`changed_after_ticket`, without when or their size now). Reading one gives the file as it is now and says so. Claude compares the files with the screenshot and the description: a file that differs and changed since was likely fixed there, so Claude writes the answer as the ticket stood, as if not fixed, and notes the file with the value then and now (`replay.fixed_later`), shown in the post as "Fixed later in a file"; a file that differs but hasn't changed was like that at the post, so the analyst may have looked at another file or run. Left out of listings: changed files and folders whose name says they were made later (the ticket's key, or a later date such as `Analysis 2026-10-30.docx`), and any path through such a folder is refused (a `Deliverables\DSS-1163` folder). Projects dated after the ticket's month aren't found. |
| Confluence | Pages created later aren't found. A page is read at the version it had then, however many versions it has, and searched by its text then: a hit changed since is kept only if that version has the searched words, and shows that version's title and an excerpt from it. Versions read are kept in `out/archive/confluence/`, an archive of pages as they were that grows with each replay. |
| Prompt | Known causes learned only from this ticket or later ones, and any line naming it or a later ticket, are left out; the general rules stay. |

A refusal never says when the hidden thing happened, as that would date the fix. The tools stop answering after 40 calls (Claude then answers with what it has), and claude stops a run at $1.50. Code looks in every tool answer for the ticket's own key, for later DSS tickets (keys are numbered in order) and for dates after it; Claude also lists anything that looked later. Both go in the record's `replay` block. The post shows Claude's leaks, each with the tool call it came through, and how many tool answers code flagged; the thread lists those.

What can still leak, as no source keeps it: a ticket or page that matched a search then but no longer does isn't found (only today's matches are checked against their text then), and Jira and Confluence rank hits by today's text; the project index has no creation times or old file contents, so a file made later with a plain name is listed as changed, and a changed file is read as it is now (by design, with the fix noted).

Without `--go`, the command shows each ticket's post time, build and commit, and runs nothing. With it, each replay is a real triage under your login; results go to `out/replay/<date>/` with their traces, and a summary line per ticket is printed. `--post-test` also posts each post and thread to #data-dev-support-assist-test (`DSS_SLACK_TEST_CHANNEL`); the command refuses #data-dev-support. It needs `DSS_SLACK_BOT_TOKEN` to read the channel.

### Scoring answers

```powershell
uv run python -m dss eval out/eval/golden.jsonl out/eval/2026-10-30_baseline           # no Claude runs
uv run python -m dss eval out/eval/golden.jsonl out/eval/2026-10-30_baseline --judge   # plus a Haiku judge
```

The golden set has one JSON line per closed DSS ticket with its known resolution: the ticket and its time, the gate that should settle it, the outcome in one line, the evidence the run should read, phrases of known false leads, the Toolbox Name and Investigation Outcome, and where the answer comes from. `docs/golden.example.jsonl` is a made-up row to copy; the real rows hold client data and live in the git-ignored `out/eval/`. The run folder holds each ticket's answer as `<key>.md` and its trace as `<key>_<run time>.jsonl`. For each ticket, `eval` prints and saves (`scores.jsonl` in the run folder) whether the run read each evidence item, whether every Jira key, Confluence page and code `file:line` under Checked exists (looked up read-only, code at the commit the trace shows), any false lead found, whether Suggested labels name the Jira labels, and the length. `--judge` adds a locked-down Haiku (`claude -p` with no tools) saying whether the answer reached the outcome; each judgement is one more run under your login.

### The bot's Cerberus

The bot reads the Cerberus analysts run, not whatever branch a developer has checked out:

- `code_*` read the bot's own clone (`DSS_CERB_GIT`) at the commit of the version in the prod folder (`release: v1.0.4`), never a working tree. Each triage first fetches every branch, so new releases, fix branches and pushed UAT builds are seen. A UAT build made only on a developer's PC is invisible until it is pushed.
- `cerb_client_info` runs Cerberus's own client info reader from the prod wheel itself, unpacked into `DSS_CERB_PROD_CODE/<version>` the first time a version is seen. The Cerberus venv still provides the dependencies; only Cerberus's own code comes from the wheel.
- `matlab_*` read the bot's own clone of DataAnalysis (`DSS_MATLAB_GIT`) at origin/master, fetched before each triage like the Cerberus clone. The clone leaves out files over 1 MB (data, not code), so it is about 30 MB.

```powershell
uv run python -m dss cerberus   # clone if missing, fetch, unpack the prod wheel, and say what each tool reads
```

A fetch or an unpack that fails is told to Claude as a note and doesn't stop the triage.

To try the tools by hand in Claude Code:

```powershell
claude mcp add dss -- uv run -q --directory <this repo> python -m dss mcp
```

### The Slack bot

The bot runs on the `ddev-support-bot` app (Socket Mode, the `message.groups` and `message.channels` events, an app-level token with `connections:write`), with the `DSS_SLACK_*` variables set. Slack hands each event to one of an app's open connections, so only one listener may run on the app.

Each analysis is a checked record, as in trial mode, posted to `DSS_SLACK_POST_CHANNEL` (#data-dev-support-assist), or without that setting to `DSS_SLACK_REVIEWER`'s DM. The bot refuses #data-dev-support, or the watched channel, as a destination. New features are tried in #data-dev-support-assist-test.

- **The post:** the headline (the verdict in a few words, its ticket key linked to the DSS post), Bot Summary (the problem in one sentence), numbered todos for the analyst (headed by their Slack name) and for the data developers (including the questions to ask and the checks that would settle what is not proved), and the Draft Reply to the analyst last, in plain words with no code. A todo list with nothing in it is left out. Under it, a "Please review the bot's report" button opens the ticket on the review page (below); a button, because Slack would preview a link to the page. When the record passed its checks, an "Edit & copy draft" button opens the Draft Reply in a pop-up: the developer edits it, copies it and posts it in the DSS thread themselves (the bot never posts there; Slack buttons cannot copy to the clipboard). The button carries the draft, so it works without the saved answer files; replays posted with `--post-test` get it too.
- **Its thread**, each section only when it has something:

  | Section | Record fields | Shown when |
  |---|---|---|
  | Why | The cause in one sentence, and where it lives (file, folder, setting, code or version) | There's a cause |
  | Past issues | Matching DSS or DTN tickets, commits or pages, each with its state (fixed, open, explained) and whether it applies here | Always, as "none found" if nothing matched |
  | Evidence | Each fact the bot read, with the tool that read it; a ref the run's trace does not show is marked | There's evidence |
  | What the questions settle | What each question for the analyst would decide | There are questions |
  | Details | Crash: the error, where it's raised, and whether that code changed since the analyst's version. Wrong numbers: both values and the first input that differs. Setup: versions, the failed run, the config. Not Cerberus: the owner and the SOP link | Settled at one of those four |
  | Also found | A second, separate problem the analyst would hit next | There is one |
  | Confidence | High, medium or low; where it settled; anything the bot or code couldn't read | Always |

  The class, the labels, the existing DTN ticket and the facts code read are left out of the thread but kept in the saved record, for the planning summaries and for debugging. The bot writes no DTN drafts: the team writes DTN tickets with its own skill.
- **Evidence check:** after the run, code compares each evidence item's source with the run's trace (`record.evidence_gaps`). A source no call of the run read, or a file line no read or search showed, is marked in the thread, and the post says how many; the answer still goes out. Code reads count whatever tool name the item gives, as Claude often cites a `matlab_read` or a grep hit as `code_read`.
- **Links:** DSS and DTN keys link to Jira; Cerberus commits, branches, PRs and releases link to GitHub, a branch or release to its PR when there is one (`code.web_links`, from the bot's clone and GitHub's pull refs). The Draft Reply links only Jira tickets, since analysts can't open GitHub. Slack previews only the DSS post below the post.

The bot waits 15 seconds after the post before it triages (`TRIAGE_DELAY_S`). The workflow posts the analyst's "Screenshot of error" within a second, and waiting longer would let the developers' own first answers into the thread the triage reads. `slack_thread` downloads the screenshots, which needs the `files:read` scope.

Slack doesn't resend events the bot missed. On start it therefore triages every ticket from the last 24 hours that has no saved answer, oldest first.

To run it around the clock, run it under the reviewer's own Windows account, because the `claude` login belongs to that session. Register a task that starts the bot at logon and restarts it if it stops:

```powershell
$action = New-ScheduledTaskAction -Execute 'uv' -Argument 'run python -m dss slack' -WorkingDirectory '<this repo>'
$settings = New-ScheduledTaskSettingsSet -RestartCount 999 -RestartInterval (New-TimeSpan -Minutes 1) -ExecutionTimeLimit 0
Register-ScheduledTask -TaskName 'DSS triage bot' -Action $action -Settings $settings -Trigger (New-ScheduledTaskTrigger -AtLogOn -User $env:USERNAME)
powercfg /change standby-timeout-ac 0   # never sleep on mains power; locking the screen is fine
```

After a reboot, someone has to log in before the bot starts again.

### The review page

[DSS Bot Review](https://claude.ai/artifact/E9iuWzrKfHo6iAxXLhvdwt) is a claude.ai page where the data developers rate each bot post against its real DSS thread. Its source is `review/dss-bot-review.html`; `#DSS-1234` at the end of its link opens that ticket.

- **Tickets:** Refresh from Slack reads the bot's posts in the assist channels from DSS-1256 on, and their DSS threads, with the viewer's own Slack connector (`slack_read_channel` and `slack_read_thread` only). It saves each changed ticket in the page's database, `tickets/DSS-<n>`, for everyone.
- **Reviews:** one shared review per ticket, `reviews/DSS-<n>`, that any developer can change; each rating keeps who gave it. A change saves only the section it touched.
- **Access:** rating and refreshing need Contributor access to the page; republishing needs Editor.
- **Changing it:** edit the source, run `node --test review/slack_parse.test.js` (or `uv run pytest tests/test_review_page.py`), then have Claude Code publish the file to the page's link with the capabilities `db`, `user` (scope `profile`) and `mcp` (Slack: `slack_read_channel`, `slack_read_thread`). The tests also check that the page splits posts at the headings `dss/record.py` writes and skips the bot's review link, so a change to either side shows there.
- **Data:** tickets and reviews hold client names. They stay in the page's database and are never committed.
- **Learning from it:** export the page's `reviews` and `tickets` collections to a folder (Claude Code's ArtifactData `list` with `out_dir`), then run `python -m dss learn <folder>`. It writes each submitted review as a golden row to `out/eval/golden_reviews.jsonl`, for `dss eval` and `dss replay --golden`, and prints a digest: the reasons by ticket count, those worth a fix (given on two or more tickets, or a post marked harmful) and each ticket's corrections. The outcome is what the reviewers say Why should say, or the bot's Why when they rated it Correct. These rows have no gate. `dss eval` scores a replay folder too: each ticket's newest replay record, its post and thread, is the answer.
- **The weekly run:** the `dss-learn` Claude Code skill (`.claude/skills/dss-learn/`) exports the reviews, runs `dss learn`, drafts fixes for the reasons worth one, replays up to 6 affected tickets, scores them with `dss eval --judge`, and commits on a branch; pushing and the pull request wait for an owner. The "DSS learning reminder" routine on claude.ai (Mondays 16:30 UTC: 8:30 in BC in winter, 9:30 in summer) reads the page's reviews and DMs Vincent only when some were submitted in the past 7 days; then say "learn from new reviews" in Claude Code.

## Working together

- **Branches and reviews:** work on a branch and open a pull request to `main`. The other owner reviews it ([.github/CODEOWNERS](.github/CODEOWNERS); branch protection requires one approval). Nobody outside the two owners needs to review.
- **Before review:** `uv run pytest`, `uv run ruff check .` and `uv run ruff format .` all pass. Code uses tabs, single quotes and a 100-character line length.
- **Prompt and tool changes:** test with `python -m dss triage` on a few real tickets and compare the answers before and after. Put the comparison in the pull request.
- **Slack changes:** use a dev Slack app (the same manifest under another name) and a test channel (`DSS_SLACK_CHANNEL`). Only one copy of the production bot runs, on the reviewer's PC.
- **Status log:** add an entry to [docs/STATUS.md](docs/STATUS.md) for each work session.

## Safety rules

- **Read-only everywhere.** The tools send only GET requests and git reads, and Claude has no other tools. Adding a tool that writes (to Jira, a Slack channel, the share or Cerberus) needs both owners to agree. The project index approval covers read-only use only.
- **Nothing reaches an analyst without a person.** Analyses go to the developers' channel, #data-dev-support-assist, never to #data-dev-support.
- **No secrets or client data in git.** Analyses contain client names and project details. `.env` and `out/` are git-ignored, and the repo stays private.

## Environment variables

All of them can go in `.env`; see [.env.example](.env.example).

| Variable | Used for |
|---|---|
| `DSS_CERB_PY`, `DSS_CERB_CWD` | The Cerberus venv's python and checkout: `cerb_client_info` and `cerb_read_sheet` run in it; `code_*` read the checkout when `DSS_CERB_GIT` is not set. |
| `DSS_CERB_GIT` | The bot's own Cerberus clone, read by `code_*` and fetched before each triage. |
| `DSS_MATLAB_GIT` | The bot's own MATLAB CAT (DataAnalysis) clone, read by `matlab_*` and fetched before each triage. Unset, the `matlab_*` tools are off. |
| `DSS_CERB_PROD_CODE` | Where the prod wheel is unpacked, one folder per version, so `cerb_client_info` runs the Cerberus analysts installed. |
| `DV_PROJECT_INDEX_USER`, `DV_PROJECT_INDEX_PASSWORD` | Login for the project index. Sent as HTTP Basic auth over plain HTTP. |
| `DV_PROJECT_INDEX_URL`, `DV_PROJECT_INDEX_ROOT` | The index address and the share it indexes. Default `http://192.168.25.120` and `\\192.168.24.252\projects`. |
| `DV_PROJECT_INDEX_MAX_MB` | The biggest file the sheet, document, image and client info tools copy from the share, in MB. Default 1024. Copies stream to disk, are reused for 15 minutes and deleted after a day; `cerb_read_text` still reads at most 50 MB. |
| `DV_CERB_PROD_DIR` | The folder analysts install Cerberus from. Default `F:/DarkVision Dropbox/Analysis/CERBERUS/prod`. |
| `DV_CERB_REF` | A Cerberus commit for `code_*` to read instead of the prod release commit. |
| `CONFLUENCE_URL`, `CONFLUENCE_EMAIL`, `CONFLUENCE_TOKEN`, `CONFLUENCE_SPACES` | The read-only Confluence account, and optionally the spaces to search. |
| `JIRA_URL`, `JIRA_EMAIL`, `JIRA_TOKEN` | The read-only Jira account. Each defaults to its `CONFLUENCE_*` value. |
| `DSS_SLACK_BOT_TOKEN`, `DSS_SLACK_APP_TOKEN` | The bot's Slack tokens. `slack_thread` reads with the bot token, also in `triage` and `trial`. |
| `DSS_SLACK_POST_CHANNEL` | Where the bot posts analyses: #data-dev-support-assist's channel id. Never #data-dev-support. |
| `DSS_SLACK_REVIEWER` | The Slack member id whose DM gets the analyses when `DSS_SLACK_POST_CHANNEL` is not set. |
| `DSS_SLACK_CHANNEL` | The channel to watch. Default #data-dev-support. |
| `DSS_SLACK_TEST_CHANNEL` | Where `replay --post-test` posts. Default #data-dev-support-assist-test. Never #data-dev-support. |
| `DSS_GH` | The gh CLI a replay reads GitHub's push history with (GET only, under your gh login). Default `gh` on the PATH, else `C:\Program Files\GitHub CLI\gh.exe`. |
| `DSS_TRACE` | `1` writes each triage's tool calls to `out/trace/`. |

## Origin

This repo was split out of dv-auto-setup-agent (Arsenii Stolbov) on 2026-10-21, at commit `b3f182e`. `runner.py`, `worker.py`, `confluence.py`, `cerb.client_info` and the Slack formatting helpers are copies cut down for triage, so fixes made there don't reach this repo on their own. The longer-term plan in that repo is a shared `dv-llm` package for the runner and tools, which both repos would then depend on.
