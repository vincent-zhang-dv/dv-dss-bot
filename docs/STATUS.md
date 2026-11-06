# Status (newest first; add an entry per work session)

### 2026-11-06: DSS-1249's two failures, and MATLAB CAT as a reference (Vincent, with Claude)
- **Why:** the live bot posted two failures for DSS-1249 to the test channel:
  - ":warning: Could not triage DSS-1249: Claude failed: Expecting value: line 1 column 1".
    `docs/known_causes.json` had four stray characters typed before its `[` (saved Nov 5, never
    committed). The record prompt reads that file, so the triage stopped before Claude ran.
  - After the file was fixed and the bot restarted on `106dc63`, catch-up re-triaged it
    ($0.93). Claude searched Jira, then read code before any git history search, so the order
    check refused the answer and the post said "failed the record check".
- **Done** (branch `step-order-gate`):
  - `server.ReportingTools.waiting`: with `DSS_STEP_ORDER=1`, which the runner now sets, the
    reading tools (`record.INVESTIGATION_TOOLS`) answer "Not yet" until the run has called
    `jira_search` and a git history tool. A search that failed counts, so a source that is down
    never blocks the triage. The held-back call is traced as `blocked`, and `traced_tools`
    leaves it out of the order check. `triage.md` step 2 says so.
  - A JSON file the record prompt reads that is not valid JSON is named in the error
    (`record._json_file`). `triage.explain` says "The triage failed:" instead of "Claude
    failed:"; Claude's own failures already say `claude -p`.
  - New known cause `las-maxpen-not-peak-preserved` (DSS-1249): with a Depth Step, the
    Cerberus LAS resamples `idMaxPen` with `np.interp`. Checked on the ticket's project: the LAS
    equals the interpolated full-resolution curve to 4 decimals, so a 0.30 in pit reads 0.239 in.
    The MATLAB export (`cat_export_deliv_las.m` in DataAnalysis) has peak-preserved `idMaxPen`
    and `idMaxWL` since 2023-02-09, while its Confluence SOP says LAS resampling does not
    preserve peaks "and that's OK". DSS-1249 was resolved the same day: the LAS has no
    peak-preservation requirement (DTN-3747 cancelled). The known cause says so.
  - The record module's docstring no longer says the Slack bot does not use it.
  - The lesson from DSS-1249: whether a behaviour is intended needs both references, the
    Confluence SOP and the MATLAB CAT code Cerberus was ported from, and either can be wrong.
    Here the SOP says LAS resampling keeps no peaks "and that's OK", while CAT has kept the
    `idMaxPen` and `idMaxWL` peaks since 2023-02-09 (CAT 2.0).
    - New `dss/matlab.py`: `matlab_grep`, `matlab_read` and `matlab_log` read DataAnalysis
      through git at origin/master. The bot's own clone (`DSS_MATLAB_GIT`, made by
      `python -m dss cerberus` without files over 1 MB) is about 30 MB and is fetched before
      each triage. Paths are confined by `code._checked`; git reads only.
    - `matlab_read` is an investigation tool, so it waits for the past-issue searches too.
    - New rule in `triage.md`: when code alone can't say whether a behaviour is intended, check
      the SOP and CAT, quote each, and leave a disagreement to the developers under Not proved.
    - CLAUDE.md lists DataAnalysis as a read-only related repo; README and `.env.example`
      updated.
- **Checked:** 202 tests pass, 4 of them new. Ruff is clean on the changed files. The record
  prompt is 19,561 characters (limit 20,000). The MATLAB tools found the CAT peak preservation
  and its 2023 commit on the real clone. Live:
  - DSS-1249 re-triaged with the order gate ($0.58), posted to the test channel: it passed the
    check, searching Jira and git history before reading code with no call held back.
  - A trial with the MATLAB tools ($0.27): passed the check, read `cat_export_deliv_las.m` and
    the Cerberus LAS page, cited the Jira resolution, and left the restore decision to the
    developers.
- **Not done:**
  - The record prompt has about 400 characters left before the command-line limit; the next
    rule or known cause needs room made first.
  - Refused posts for schema problems remain possible (2 of 17 trials so far); a repair turn is
    an option.

### 2026-11-05 (later): Cerberus's terminal log as a source (Vincent, with Claude)
- **Why:** from Cerberus 1.1 (`ab952f341`, DTN-3710), each session writes loguru's DEBUG output
  to `<project>\cerberus\terminal_log\<login>_terminal_log_<start>_<pid>.log`, named like its
  telemetry log. The bot can read the error text itself instead of asking the analyst. Cerberus
  1.0.4, and the UAT build `1.0.4+uat20261031`, write none.
- **Done** (branch `terminal-log`):
  - `cerb.last_session` also reads the terminal log paired with the session
    (`cerb.terminal_log`, `cerb.log_problems`):
    - Only lines before the ticket time are read.
    - Each distinct ERROR or CRITICAL is listed once, at its newest line, with how often it fired.
      It keeps the end of its traceback (its deepest frames and the exception) or its banner's
      lines, without the variable values loguru prints.
    - Each WARNING call site is listed once, at its first line.
    - Every item has its line number for `cerb_read_text`.
  - With no log: "Cerberus 1.0.4 writes no terminal log; Cerberus 1.1 and later do", or why a
    1.1 session's log wasn't read. Only the second becomes a note for the reviewer.
  - The record's `dss_ticket` block has a new `terminal_log` field (schema, example), so Claude
    gets the errors without a tool call.
  - `triage.md`:
    - Step 1 reads the last session.
    - Gates B and C and the crash playbook look in the terminal log first.
    - Claude asks for the error text only for sessions before 1.1, or when the log shows no error
      for the problem.
  - `triage_record.md`: the new fact, and `terminal_log:line <n>` as an evidence ref.
  - README updated.
- **Checked:**
  - 198 tests pass, 7 of them new.
  - Ruff is clean on the changed files, apart from the old SIM108 in `cerb_client_info`.
  - mypy reports the same 13 older errors in `cerb.py`.
  - Live, code only, on the terminal logs of two recent DSS tickets' projects. A 716 KB log
    became about 4,000 characters, including a CATCALL assertion with its frames and a
    PermissionError while saving the defect summary.
  - DSS-1247's facts at its post time: Cerberus 1.0.4, no log, no note. At a later time: the
    reporter's 1.1.0.68 session, with 1 error and 7 warning call sites, passing the schema.
- **Not done:**
  - No Claude trial: no DSS ticket yet comes after a 1.1 session.
  - Not committed. The live bot needs a restart after the merge.
  - The DSS Triage Flow page still lists the terminal log as planned.

### 2026-11-05: a shorter thread under the post (Vincent, with Claude)
- **Decided by Vincent**, after Annika's and Sean's comments:
  - The thread keeps Why, Past issues, Evidence, What the questions settle, Details, Also found
    and Confidence.
  - The bot writes no DTN drafts, since the team writes DTN tickets with its own skill.
  - The class, the labels, the existing DTN ticket (`escalate`) and the facts code read leave
    the thread but stay in the saved record.
- **Done** (branch `thread-sections`):
  - `record._proof` renders the seven sections:
    - Claude's flags and code's notes fold into the Confidence line as "could not read", each
      said once.
    - Also found shows only the cause.
    - Confidence reads "high, settled at the wrong numbers playbook".
  - A past issue's text now ends in a full stop before the reason it applies; the two ran
    together.
  - `_planning`, `_labels` and `_facts` are removed, and so is `_request`, which nothing called.
  - `escalate` takes only an existing DTN ticket: the schema refuses a `draft`, and
    `triage_record.md` says to write none. The developers' "raise a DTN ticket" todo no longer
    points to a draft.
  - README: the thread as a table.
- **Token cost of these changes** (16 saved trials, 17 logged runs since 2026-10-30):
  - The thread changes cost nothing either way: code renders the thread after Claude answers.
  - 2 of the 16 trials wrote a DTN draft (517 and 903 characters, about 130 to 230 tokens). A
    run's median output is 6,847 tokens, so dropping drafts saves well under 1% of output.
  - Most of a run's cost is reading: the cached context is read again on every turn (median
    about 225k cache-read tokens; 1.2M on a 58-turn run). Fewer turns is the lever for cost.
- **Checked:**
  - 191 tests pass, one of them new.
  - Ruff is clean on the changed files.
  - mypy is clean on `record.py`; the 13 errors it reports are all in `cerb.py`.
  - The example record was rendered by hand.
  - No live trial: the prompt change is one phrase.
- **Not done:**
  - Not committed. The live bot runs `main` from the main checkout and needs a restart after the
    merge.
  - From Annika: Jira's comments, outcome, status and links leak the answer into trials of older
    DSS tickets. Only the Slack thread is cut at the post. Next: an as-posted Jira built from the
    change history.
  - The DSS Triage Flow page and the Reply Quality Tracker still describe the older format.

### 2026-11-04 (night): todo titles, links and a shorter footer (Vincent, with Claude)
- **Asked by Vincent:**
  - number the todos 1., 2. (Slack letters a list inside a bullet a., b.);
  - drop `dss-triage/3` and "not posted in the DSS thread · the DSS post" from the footer;
  - link DSS and DTN tickets and GitHub commits, branches, PRs and releases, with no previews
    except the DSS post's;
  - replace Actions with "Bot Summary", "<analyst's Slack name> Todos", "Data Dev Todos" and
    "Draft Reply", leaving out a todo list with nothing in it.
- **Done** (branch `source-gaps`):
  - Each todo list is a top-level numbered list under a bold title. The analyst's title is their
    Slack display name as plain text: a mention could notify them of a draft. The Jira reporter
    stands in without Slack (the `trial` CLI). No `'s`, as many names end in s.
  - The headline's key links to the DSS post, which gives the snapshot below the post. The
    footer is "Draft by Claude · tokens · cost · time".
  - `code.web_links` finds commits (checked with `rev-parse`), remote branches (a branch's PR
    when GitHub's pull refs, read with `ls-remote`, name its tip), "Cerberus x.y" releases (the
    release commit's PR) and "PR #N" or "(#N)". `master` is not linked. A failed lookup becomes a
    note, and the post goes out with Jira links only.
  - The Draft Reply links only Jira tickets: analysts cannot open GitHub.
  - `unfurl_links` stays at Slack's default. `False` also hid the DSS post's preview (tried in the
    test channel); Jira and GitHub links show no preview at the default. `unfurl_media` is off.
- **Checked:**
  - 190 tests pass; ruff is clean apart from the old SIM108s, FURB167s and SIM110.
  - DSS-1247 re-posted in the new format (16:15): the DSS snapshot shows, nothing else unfurls.
  - The links of DSS-1247 found in 1.2 s; the DTN-3726 branch links to PR #1018 and
    `develop-v1.1-release` to the 1.1 release PR #978.
  - A test run of DSS-1237 (posted 2026-10-31; 58 turns, 181 s, $1.39) in the new format. Its
    verdict leaned on a rerun log a developer put on the share after the ticket: the thread is
    cut at the post, but the share and Jira are read as they are now.
- **Live:** the bot restarted at 16:44 with the new format, still posting to
  #data-dev-support-assist-test.
- **Not done:**
  - "As posted" trials, which DSS-1237 showed matter for replaying old tickets.
  - A logon task.
  - Annika's OK on the destination, then `DSS_SLACK_POST_CHANNEL` back to #data-dev-support-assist.

### 2026-11-04 (evening): a shorter post, agreed with Annika (Vincent, with Claude)
- **Feedback from Vincent and Annika on the first posts:**
  - say each thing once;
  - give analysts the answer, not the internals;
  - "Reported" and "Request" were the same;
  - name exact data, not "here";
  - don't state what the reader can tell already;
  - combine the actions and the notes (Annika).
- **Done** (branch `source-gaps`; record `dss-triage/3`):
  - The post: a headline with the verdict in a few words, then Summary.
  - Then Actions for the analyst and for the developers. Questions become "Answer" actions;
    what is not proved becomes "Confirm" checks.
  - The Reply comes last: at most 3 sentences, solution first, exact facts. No code, functions
    or line numbers, and no release unless a DTN fix version names it. The check refuses `.py`,
    `cerberus/` paths and line numbers in it.
  - The thread: why, past issues, evidence, DTN draft, confidence, class.
- **Also fixed:** DSS-1242's first trial lost its answer to an `AttributeError` in the trace
  check, on a malformed `triage` block. That check now tolerates it, and any error while checking
  or rendering becomes a listed problem, with the traceback and the answer saved.
- **Checked:**
  - 185 tests pass; ruff is clean; the system prompt is 17,786 characters.
  - The DSS-1242 trial (`dss-triage/2`, likely, $0.40) was laid out by hand in the new format to
    agree it.
- **Live:** the bot restarted, posting to #data-dev-support-assist-test (`DSS_SLACK_POST_CHANNEL`
  set to the test channel in `.env`) until the format is checked on new tickets.
  - Its first live ticket, DSS-1247 (44 s, $0.23): known issue DTN-3726, fixed in 1.1.
  - The run exposed a gap: Cerberus 1.1 went to prod today as `cerberus-1.1.0.68`, but the
    release commit is `release: 1.1 (#978)`, so `prod_ref` found nothing.
    - The lookup now also tries the version without the build number and without a zero patch.
    - All 444 files of the 1.1.0.68 wheel match `ab952f341`.
  - Actions now render as bullets (Analyst, Developers), with a numbered list inside. Slack
    letters the inner list a., b.
  - The bot restarted with both changes; 187 tests pass.
- **Not done:**
  - "As posted" trials: hide sources newer than the post, so old tickets don't find later fixes.
  - The PR lookup from git pull refs, with links (done that night).
  - Merging `source-gaps`.
  - A logon task.
  - Annika's OK on the destination.

### 2026-11-04 (late afternoon): the bot goes live in the developers' channel (Vincent, with Claude)
- **Decided by Vincent:**
  1. Triage 15 s after the post (the workflow's screenshot lands within a second).
  2. The live bot uses the checked record.
  3. Analyses go to #data-dev-support-assist; new features and debugging go to
     #data-dev-support-assist-test; nothing goes to #data-dev-support until the replies are
     consistently good.
- **Done** (branch `source-gaps`):
  - `SlackDesk` runs `record.trial` and posts the decision to `DSS_SLACK_POST_CHANNEL`, with the
    proof in its thread, as Markdown blocks. Without that setting it posts to the reviewer's DM.
  - It refuses #data-dev-support, or the watched channel, as a destination, at start and on every
    post.
  - A blank line now ends each list in the DM and thread, so "Evidence" numbers from 1 (Slack had
    run on from "Past issues").
  - CLAUDE.md, README and `.env.example` updated.
- **Live:**
  - `ddev-support-bot` has Socket Mode, an app-level token and the message events (set up by
    Vincent as a collaborator).
  - The bot started on Vincent's PC about 14:00 and connected. DSS-1245 and DSS-1246 were marked
    done first, so it starts with the next new ticket.
  - It runs as a background task of a Claude Code session, so it stops when that session ends,
    until a logon task runs it.
- **Checked:**
  - 176 tests pass; ruff is clean on the changed files.
  - One Markdown-block post to the test channel rendered bold, code and escapes correctly.
- **Not done:**
  - Merge `source-gaps`.
  - A Windows logon task, so the bot survives the session.
  - Annika's OK on the destination change (CLAUDE.md says both owners).
  - The first live ticket end to end.

### 2026-11-04 (evening): the sources the bot could not see (Vincent, with Claude)
- **Why:** a live probe of every source (Jira, Confluence, project index, telemetry, git, Slack)
  found all of them up. What the tools could not see:
  - Jira comments and fix versions;
  - PDF and Word files;
  - images on the share;
  - files over 50 MB;
  - data files.
- **Done** (branch `source-gaps`, one commit each):
  1. `jira_get_issue` adds:
     - resolution;
     - fix versions, released or not, with dates;
     - the newest 10 comments;
     - attachment names.

     `jira_search` adds resolution and fix versions.
  2. `cerb_read_document`: the text of `.pdf` and `.docx`, page by page (pypdf and python-docx
     in the Cerberus venv).
  3. `cerb_read_image`: images on the share, and the pictures embedded in a PDF page. Scaled to
     1568 px, logos left out.
  4. Copies stream to disk up to `DV_PROJECT_INDEX_MAX_MB` (default 1 GB; Vincent to confirm),
     are reused for 15 minutes, and are deleted after a day.
  5. `cerb_read_table` (Parquet; SQLite read-only) and `cerb_read_caliper` (`.dvcaliper` and
     caliper `.npy`, by the prod Cerberus's reader).
     - A `.dvcaliper` window is read by HTTP ranges, so a 4 GB file is never copied: header,
       binary search on depth, then only the window.
     - Pickles and `Thumbs.db` are refused.
- **Checked:**
  - 177 tests pass. Ruff is clean apart from the old SIM108.
  - Live:
    - DTN-3726 shows its fix version (CERBERUS v1.1, not released) and the developer's comment.
    - The 2D log intro pages PDF: its text, and page 2's overview plot as an image, with the
      repeated "0.2".
    - A 496 MB Word file and a 282 MB PDF (943 pages) copied in 2-4 s and reused.
    - A Parquet file of 1,451 rows x 155 columns.
    - DSS-1241's 4 GB wall-loss caliper: summary in 2 s, and the window at 15026 ft in 3 s.
      It shows the breach (0.007722 m = 0.304 in) at 15026.25 ft, inside a ring of shallower
      loss around most of the pipe.
  - All 14 `.db` files in three projects were `Thumbs.db`.
- **Not done:**
  - Not pushed.
  - Open PR titles (needs a GitHub token), other Slack channels (invites), UAT builds pushed.
  - Word files' embedded pictures.
  - Jira attachment downloads (the download redirects to another host with the login).

### 2026-11-04 (afternoon): Slack thread live, and the bot's own Cerberus (Vincent, with Claude)
- **Merged:** PR #8 (`trial-followups`: threads and screenshots, workbooks, past issues first).
- **Slack:**
  - `.env` here has the bot token of Annika's `ddev-support-bot` app, and no app token, so no
    live bot can start on this PC.
  - The bot was invited to #data-dev-support. These reads worked:
    - the channel's history;
    - the threads of DSS-1240 to DSS-1245, with 10 screenshots downloaded (one checked by eye).
  - One test post to #data-dev-support-assist-test, Vincent's test channel, at his request.
    Nothing was written to #data-dev-support.
  - The app lacks `channels:read` (the bot can't list its channels; not needed).
- **The bot's Cerberus** (branch `cerberus-prod-copy`, at Vincent's request):
  - `DSS_CERB_GIT`: the bot's own clone. `code_*` read it, and each triage fetches every branch
    first (`code.refresh`). Unset, they read the developer checkout as before.
  - `DSS_CERB_PROD_CODE`: the prod wheel's Cerberus is unpacked there, one folder per version.
    `cerb_client_info` imports it first, so it runs the Cerberus analysts installed, not the
    checkout's branch. The venv still provides the dependencies; installing the wheel's 62
    (GPU, Qt, two internal packages) was not worth it.
  - A failed fetch or unpack is told to Claude as a note and doesn't stop the triage.
  - `python -m dss cerberus` sets both up and says what each tool reads.
  - Found while checking it live: `python -c` puts its working folder (the checkout) ahead of
    `PYTHONPATH`. The worker now puts the prod folder first itself, and the tests use a decoy
    checkout.
  - `.gitignore` now also covers `.env.*` and `*.swp`: an editor swap file of `.env` had
    appeared, holding the tokens.
- **Checked:**
  - 163 tests pass; ruff clean on the changed files apart from the two old SIM108s.
  - Live on this PC (`F:\git\dss-bot-cerberus`):
    - the clone and a fetch;
    - prod 1.0.4 = `ff8a0091e`;
    - `cerb_client_info` loads from `prod\1.0.4\cerberus`;
    - two real projects' client info (19 and 292 joints) parse identically with the prod copy
      and the 1.1.0 checkout.
- **Not done:**
  - Not committed.
  - The live listener: an app token, Socket Mode and `message.groups` events on Annika's app,
    agreed with her so only one listener runs.
  - UAT builds must be pushed for the bot's clone to see them.
  - Still open: the triage delay (5 min vs about 15 s), and the first trial with `--thread`.

### 2026-11-04: trial on DSS-1243 and a demo of visible investigations (Vincent, with Claude)
- **Checked:**
  - A trial on DSS-1243 (two "0.2 in" labels on the eccentricity scale), from the post only. It
    took 68 s and $0.38, passed the record check, and answered likely, medium. It found the
    cause in `techlog/intro_pages.py` (`MaxNLocator(nbins=7)` plus `'%.1f'`), the duplicate
    DSS-1232 and the unreleased fix DTN-3726 (`2ea08f0ea`). It asked for a screenshot that was
    already in the thread.
  - The session proved the rest:
    - Cerberus's matplotlib 3.10.8 (prod's pin) reproduces the screenshot's labels on a 0 to
      0.5 in axis: ticks 0.08 apart, with 0.16 and 0.24 both printing 0.2.
    - The fix gives distinct labels.
    - DTN-3726 is Done, with fix version CERBERUS v1.1 (not released).
    - The UAT build `e7d6f928c` (1.0.4+uat20261031) lacks the fix.
  - The session read the developer's replies by mistake before checking: a `slack_read_thread`
    call with `limit` returns the newest replies, not the first ones.
- **Done:**
  - A demo page for making investigations visible: steps, evidence-graded confidence, hypothesis
    curves, mistakes and a reviewer's "Flag a mistake" button. It is a private artifact, built
    from one JSON block.
  - The page proposes four changes:
    - `hypotheses` and `steps` fields in the record;
    - code-set score bands checked against the trace;
    - `python -m dss report`;
    - an outcome and corrections log.
- **Done (later the same day), at Vincent's request: past issues first.**
  - `triage.md` now runs every ticket as:
    1. state the request;
    2. search past DSS and DTN tickets, git history and branches, and Confluence;
    3. check whether a past fix applies, including whether it is in the analyst's build;
    4. only then the gates and playbooks.
  - Stopping rule: "likely" only when no tool can read the last link. The "one `jira_search`"
    rule is now up to three.
  - New git-read tools in `dss/code.py`:
    - `code_log` (recent commits on a path);
    - `code_branches` (remote branch names);
    - `code_contains` (does prod, a release, a UAT build or a remote branch include a commit or
      branch).
  - Record `dss-triage/2`:
    - required `request` and `prior_art`;
    - `settled_at` gains `prior_art`, which needs an item that applies, and `record` is renamed
      `request`.
  - Enforcement:
    - every trial now keeps its trace;
    - the record check refuses a run that read code or project files before a `jira_search`
      and a git history search (except `not_cerberus`).
  - The DM shows the request; the thread lists past issues.
- **Checked:**
  - Tests: 156 pass. Ruff is clean on the changed files apart from the old SIM108 in
    `code.py`'s `grep`, left as a block.
  - Live `code_contains` and `code_branches` against the real checkout.
  - Re-ran DSS-1243, DSS-1240 and DSS-1235 from the post only ($1.57 in total). All three passed
    the check and settled at past issues.
    - DSS-1243: reached DTN-3726 at call 3 (was 11). It checked the UAT build and offered the
      rerun with the fix, but went to low confidence because the plot was unseen.
    - DSS-1235: found Annika's `DSS-1210-work-annika-raid-headers`, which the old run missed. It
      cost $0.90 (was $0.39): 4 of its calls failed on branch names, so `code_contains` now takes
      branch names.
    - DSS-1240: proved, but DTN-3741 was created after the old run, so it is not a fair
      comparison.
  - Comparison page: a private artifact, built from the traces by a scratch script.
- **Not done:**
  - Nothing committed.
  - The production bot's Markdown answer follows the new order only through the prompt; nothing
    checks it there.
  - Cherry-picked fixes are invisible to `code_contains`.
  - No tool for open PR titles (needs a GitHub token; both owners).
  - Annika to review the new order and the `dss-triage/2` schema. The DSS Triage Flow page
    still describes `dss-triage/1`.

### 2026-11-03: trial on DSS-1240 (Vincent, with Claude)
- **Checked:**
  - A trial on DSS-1240 (the reference perforation plots combine all calibrations), from the
    post only: this PC has no bot token, so `--thread` couldn't be used. It took 103 s and
    $0.68 and failed the record check: `escalate.kind` came as `draft`.
    - Its reading went further than earlier trials. It used `cerb_read_sheet` on Stage Designs
      and the baseline calculation, and read the plot's SVG as text.
    - It called the ticket a likely feature request (one figure for all stages). It asked whether
      the spec lines sit at different heights, which it couldn't see.
  - Against the screenshots and the project's files, read by the session:
    - Both delivered reference plots have one spec, median and ±1 SD line across all 84
      reference perforations: each SVG LineCollection holds one segment spanning the axis. The
      spec line is 0.42 in for all three charges (0.38, 0.40 and 0.42 in).
    - Since DTN-3214 (b7970ca41, in release 0.14.1) the plot groups its lines by `stage`.
      Before that it grouped them by charge type (395cd1003).
    - Cerberus's `stage` is Perf Info's `dvStage`, which is 69 for all reference perforations,
      while their client stages are 69, 70 and 71. Cerberus's saved calibration table has
      stage 69 for all 84, so the plot draws one group. The document's "one stage" comes from
      the same column.
  - `cerb_read_sheet` now refuses an Excel lock file (`~$…`) with a readable message, with a
    test. `uv run pytest` (143 passed).
  - A trial on DSS-1241 (two tiny defects reported as one large one), from the post only: 74 s,
    $0.53, record check passed, "needs info, low confidence".
    - It found the interaction rule (3 × 0.304 in nominal wall) and the 12.5 % threshold, and
      named the 0°/360° seam handling as a possible bug.
    - Without the thread it asked the analyst for the depth and the two defects' positions,
      which the screenshots show, and it guessed the wrong defect row.
    - The session found the row in the delivered Defect Summary: 3.48 in long and 13.32 in wide
      at 100 % wall loss. On that joint's 4.892 in ID, 13.32 in is 312° of the circumference.
      Whether a band of shallow wall loss joins the two patches or the seam handling measures
      the long way round was not settled.
- **Not done:**
  - Whether the plot should group by stage and charge type, or the project's `dvStage` should
    differ, is for the Cerberus owners.
  - The trial prompt's `escalate` wording (`kind` plus `draft`) was misread again, after
    DSS-1234's shape errors.

### 2026-11-02: the triage reads the DSS ticket's Slack thread and screenshots (Vincent, with Claude)
- **Why:** the DSS-1236 and DSS-1239 trials were wrong or noisy because Claude saw only the
  workflow's post. Both threads already held the deciding screenshots when the trials ran, and
  the DSS-1236 trial asked the analyst for exactly those. Vincent's priority: a proved answer
  first; spending many tokens on a complicated ticket is fine, waste is not.
- **Changed:**
  - New read-only tool `slack_thread(ts)` (`dss/thread.py`). It returns the post and every reply,
    oldest first, with each screenshot as an image and each text file as text; other files are
    named.
    - It reads only threads of a workflow's "created DSS-…" post in the watched channel, with GET
      requests: `conversations.replies`, `users.info`, and the files.
    - The bot token is sent only to `https://files.slack.com`, and redirects are refused. A file
      that can't be read is named with the reason; the rest of the thread still comes back.
    - Limits: 200 messages, 20 images, 20 MB per file, 20,000 characters per text file.
  - The bot waits until 5 minutes after the post before it triages (`TRIAGE_DELAY_S`). It passes
    the post's ts, and the prompt tells Claude to call `slack_thread` first.
  - `triage` and `trial` take `--thread <permalink or ts>`. `--posted` then defaults to the post's
    time, and replies more than 5 minutes after the post are left out (`DSS_THREAD_UNTIL`), so a
    trial of an older ticket stays blind to the developers' answers. Trial files save
    `thread_ts`.
  - `triage.md`:
    - Puts a proved answer before saving calls.
    - Reads every screenshot and quotes it.
    - Starts a crash from the traceback's deepest Cerberus frame, then the input it reads.
    - Never asks the analyst for what the thread already has.
    - Keeps confidence low when Claude could not see the symptom itself.
    - Drops "Read little" and "You cannot open screenshots".
  - `triage_record.md`: `slack_thread` is an evidence ref.
  - `slack_manifest.yaml`: the `files:read` scope.
  - `SlackDesk`'s name lookup moved to `slack.Names`, shared with the tool. `slack._channel` is
    now `slack.watched_channel`.
  - New read-only tool `cerb_read_sheet(path, sheet, first_row, contains)` (`dss/cerb.py`) for
    any `.xlsx` or `.xlsm` on the share. The project index already fetched these files;
    `cerb_read_text` refused them as binary.
    - It copies the workbook through the index (`local_copy`, share paths only) and reads it with
      openpyxl in the Cerberus venv (`cerb.workbook`). Nothing is added to this repo.
    - Without a sheet it lists the sheets and their sizes. With one it returns up to 100 rows or
      30,000 characters, by Excel's row numbers, cells tab-separated, formulas as last saved, and
      where the next page starts.
    - `contains` keeps only the rows with a cell holding that text. A filtered or later page also
      shows the top 3 rows, for the column names.
    - The old binary `.xls` is refused.
  - `triage.md` names `cerb_read_sheet` in the crash and wrong-numbers playbooks.
- **Checked:**
  - `uv run pytest` (143 passed). Ruff check and format pass on the changed files, apart from one
    older SIM108 in `cerb.py` (`cerb_client_info`'s if/else, kept as a block on purpose). Ruff
    also flags `code.py`, `eval.py` and `test_eval.py`'s formatting, which this session didn't
    touch.
  - One live Haiku run (outside the `dss` server, $0.07) with the runner's locked-down flags and a
    made-up 6.4 MB PNG returned by an MCP tool. The image reached Claude and its text was copied
    exactly, at about 2.8k tokens, so screenshots need no resizing here.
  - `cerb.read_sheet` live on DSS-1239's project (code only, no Claude run):
    - It listed the Perforation Statistics workbook's 4 sheets in 1.1 s, then returned pages of
      the 1,451-row sheet in about 0.6 s each.
    - `contains="7x4"` returned the design's 28 perforation rows, the count found by hand on
      2026-11-02.
    - It listed the client info's 20 sheets.
- **Not done:**
  - `slack_thread` has not run against real Slack. This PC has no bot token, and the production
    app lacks `files:read` until the scope is added and the app reinstalled.
  - No live triage with the new prompt yet. Next: re-run DSS-1235, 1236 and 1239 with `--thread`
    and compare with the known answers (about $0.50 each).
  - Annika to agree to the 5-minute delay and the new scope.
  - No Claude run has used `cerb_read_sheet` yet; the DSS-1239 re-run will show whether it
    finds the design with no measured perforations.

### 2026-11-02: trial on DSS-1239 (Vincent, with Claude)
- **Checked:**
  - A blind trial on DSS-1239, where making the PE analysis document fails. It took 51 s and
    $0.25, passed the record check as "needs info, low confidence", and was sent to Vincent's
    DM.
  - Against the screenshot in the DSS ticket's thread, which the session read and the run can't:
    - The traceback is `ValueError: arange: cannot compute length` in the completions histogram
      (`histogram_plots.py` `base_histogram`) for one stage design.
    - That design's only stage has no measured perforations (all plugged, debris or no data),
      so the bin range is NaN. Calling `base_histogram` on the project's exported statistics
      reproduced the error for that design; designs with even one volume pass.
    - Prod, master and `develop-v1.1-release` have no guard for this.
    - The run asked the analyst for the error text the screenshot already shows, and followed
      the analyst's guess about a failed plug.
  - The low status was right. The gap is the screenshot again: all three trials since DSS-1235
    needed it.
  - The same screenshot shows Cerberus's telemetry sink failing on flush
    (`OSError: [Errno 22]`), which may be why the run found no telemetry for the failed
    session; not checked.
- **Not done:** DSS-1237 and DSS-1238 got no trial (Vincent picked the newest). Reading the
  workflow's screenshot is still open (see 2026-10-31).

### 2026-10-31: first trial on a new DSS ticket (Vincent, with Claude)
- **Changed:** the trial DM leaves a blank line after the escalation draft, so Slack no longer
  pulls "Suggested reply" into the quote (`record._decision`, with a test).
- **Checked:**
  - A blind trial on DSS-1235 (an overlapping RAID 2D Log header), 15 minutes after it was
    posted, from the workflow post only. The record check passed in 62 s for $0.39. The result
    was sent to Vincent's DM, with the proof in the thread.
  - Against the data developers' answer in Slack (a known header font problem, fixed on
    Arsenii's and Annika's branches, not released):
    - It found the cause, Arsenii's fix (d99479197, only on `DTN3703-work-ars-killer-2dlog`),
      that prod 1.0.4 lacks it, and the earlier DSS-1210.
    - It missed Annika's fix (1552cf587, only on `DSS-1210-work-annika-raid-headers`). That
      commit changes only label alignment, so the `code_log_s` text searches can't find it,
      and no tool lists branches by name.
    - Its workaround (wider tracks in the RADX layout) was not checked. A data developer said
      the font size is not a setting.
    - Neither fix is on `develop-v1.1-release`.
  - A blind trial on DSS-1236 (a Techlog 2D log missing the white line between joints), 61 s
    and $0.49, was wrong. It said "likely, medium confidence: casing sections, mark casingSplit".
    A data developer called it a bug. The screenshots, which the run can't see, show the line
    missing at a collar in the column beside the depth numbers, between two joints with defects.
    The run had flagged the white line as not proved, yet still rated its answer likely and
    gave the analyst an action.
  - `uv run pytest` (122 passed); ruff check and format pass on the changed files.
- **Not done:**
  - The run can't see the workflow's screenshot, the key evidence in both trials. Passing it to
    Claude needs the Slack app to read files; for the owners to decide.
  - A prompt rule that an unconfirmed symptom caps the status below "likely", to test on a
    few tickets first.
  - A read-only tool that lists remote branches whose names mention a DSS or DTN key would
    have found Annika's fix; for the owners to decide.
  - `known_causes.json` has no entry for this header problem.

### 2026-10-30: trial mode for the triage record (Vincent, with Claude)
- **Changed:** `python -m dss trial <key>` runs the triage record design by hand; the bot is
  unchanged. It takes the same arguments as `triage`. It runs the same locked-down Claude with
  `triage.md`'s steps and rules, but with `dss/triage_record.md` as the answer section.
  - Code first reads the `dss_ticket` block: Jira, the project folder, the workflow from the
    folder name, the reporter's last session, and the prod version. A failed source is noted
    rather than fatal.
  - Code checks Claude's JSON against the schema and renders a DM (the decision) and a thread
    reply (the proof). Claude's text is escaped so paths and names show as written.
  - Each trial is saved in `out/trial/`, never in `out/answers/`, which the bot reads.
  - Small changes, with no change for the bot: `runner` takes an optional system prompt;
    `jira.get_issue` takes optional fields; `triage.run` is split out of `triage.triage`; the
    CLI shares its ticket arguments.
  - `jsonschema` is now a runtime dependency.
  - Schema: an optional `project` field in `dss_ticket` (the page lists it as an input).
    `class.cause_family`, `feature` and `prevent` may be null, so a needs-info answer isn't
    pushed to guess them; this is for the owners to confirm.
- **Checked:**
  - `uv run pytest` (121 passed); mypy clean apart from the Cerberus import; ruff check and
    format pass on the changed files.
  - Live trials on DSS-1234, the newest DSS ticket: about 70 s and $0.35–0.38 each.
    - Run 1 failed the record check: `not_proved` items came as objects and `secondary.class` as
      one value. The prompt now spells out both shapes.
    - Run 2 passed. It found the known cause `path-too-long` (DTN-3727) and was sent to
      Vincent's DM through the Slack connector.
    - Two rendering faults in Slack Markdown (eaten backslashes, `__init__` as bold) and a footer
      joined to the quoted reply were fixed afterwards. The thread reply went out fixed; the DM
      had already gone out.
  - Merged as PR #7, which included #6. On `main` after the merge: 121 tests pass, and code read
    DSS-1233's `dss_ticket` facts from every source with no failure notes (no Claude run).
- **Not done:**
  - The bot posting the trial itself: this PC has no Slack bot tokens, so the session delivers
    the DM.
  - A repeat between Claude's `flags` and code's notes when both say the same thing.
  - Jira shows today's state: a DSS ticket two days old already showed its DTN link, so the run
    saw the answer. Not a problem for new DSS tickets.

### 2026-10-29: the triage record proposal as files; its planning fields tested on 45 DSS tickets (Vincent, with Claude)
- **Changed (a proposal; the bot does not use these yet):**
  - `docs/triage_record.schema.json`: the triage record from the DSS Triage Flow page, as JSON
    Schema. `dss_ticket` is filled by code, `triage` and `class` by Claude, `resolved` by a person.
    It checks the status rules:
    - proved and likely need a cause, evidence and `do_now`, and likely also needs `not_proved`;
    - needs info needs questions;
    - not Cerberus needs an owner;
    - proved wrong numbers needs both values and the first difference.
  - `docs/triage_record.example.json`: the page's example, with made-up data.
  - `docs/known_causes.json`: 17 known causes drafted from settled DSS tickets. Each gives how to
    prove the cause and the fix; 10 have two or more DSS tickets.
  - `tests/test_triage_record.py`. `jsonschema` is now a dev dependency; it was already installed
    through `mcp`.
- **Checked:** `uv run pytest` (97 passed); mypy clean apart from the Cerberus import. Each case
  the tests expect the schema to refuse was checked to fail for its intended reason. `ruff check`
  and `ruff format --check` pass on the changed files. On the rest of the repo, ruff 0.17.1
  reports 9 lint findings and 3 unformatted test files, all already on `main`; left alone here.
- **Tested: the planning fields on 45 more closed Cerberus DSS tickets.** They are a fixed-seed
  sample of the "ready" DSS tickets not sorted before. Claude sorted them from the Jira text; the
  result is in `out/eval/class_test.jsonl`.
  - The cause families held: 42 of 45 fit, and the other 3 have no information in Jira.
  - The first `feature` list did not: only 17 of 45 fit. The rest would only fit by lumping four
    PE deliverables into `pe_plots_tables`, and catcall, LAS export, the AIA and burst reports
    and tubeviews were missing. The schema has a revised list of 19 values, prefixed by workflow.
  - 14 of 45 have no resolution text in Jira, and none states the impact. A back-fill from Jira
    alone can set the cause family, feature and prevent for most DSS tickets. It cannot set the
    impact, and the fix type stays unclear for 11 of 45.
  - Two boundary questions for the team:
    - a `setting` family, for causes that are a Cerberus setting (DSS-1048, DSS-1204);
    - merging `outdated_install` into `environment` (DSS-945 and DSS-1018 sat on the line).
- **Outside the repo:**
  - PR #5 merged.
  - The DSS Triage Flow page was restructured: a contents column, live and planned tags, stale
    parts removed. It now holds the triage record proposal and Sean's planning use.
- **Not done:** the team's agreement on the record; nothing in the bot reads or writes it yet.

### 2026-10-29: baseline of the 30 golden tickets; the golden set goes on a review page (Vincent, with Claude)
- **Changed:** `dss eval` no longer reads a host such as `8000@ACME-12` as a Jira key (one test
  added).
- **Checked:** `uv run pytest` (78 passed); mypy clean. Baseline: the 30 golden tickets triaged
  with `python -m dss triage --posted`, traces on. Each run read Cerberus as of the ticket date:
  `DV_CERB_REF` was set to the last origin/master commit before the ticket. Cost about $7 in all,
  30 to 75 s per run, no failures. `dss eval` on them: every citation resolved except one, a
  license host written like a key. 18 of 30 answers are over the 3,500-character limit. 10 of 30
  start with a status line ("I've finished the checks…") before the title, and that line would
  reach the DM too.
- **Claude's draft verdicts (Vincent to check):** 17 right, 9 partly, 4 missed. Code bugs are the
  strongest: 9 of 13 playbook tickets right. Setup problems are the weakest. Of the pilot
  tickets, DSS-1225 and DSS-1226 are now right, DSS-1228 and DSS-1229 partly, DSS-1217 still
  missed.
- **Found: the replay is not yet fair.** These need fixing before the scores mean much:
  - A closed ticket still shows the bot its status, its Investigation Outcome and its linked
    DTN titles.
  - Some descriptions were edited after the fix; one is now the answer itself.
  - `cerb_prod_version` reports today's version while the code is pinned. Four answers therefore
    wrongly say a fix "is not in prod 1.0.4".
  - `code_log_s` across all branches finds fixes made after the ticket.
  - Project folders show today's state: folders renamed since, and runs made after the fix.
  - Some Confluence pages were written after the ticket.

  Separately, `cerb_client_info` output was too large to read in 6 answers.
- **Jira:** DSS-877's Toolbox Name changed from CERB-WI to CAT, as Vincent asked; read back.
- **Outside the repo:**
  - PR #5 opened for `dss eval`.
  - The private claude.ai page "DSS Golden Set" holds the 30 tickets. Each shows what the
    ticket said at the start, the real outcome, the bot's answer, and Claude's draft key and
    verdict. Vincent checks them there.
  - `out/eval/export_golden.py` writes `golden.jsonl` from the saved checks.
  - `run_baseline.py` now pins the code per ticket and skips tickets it has already run.
- **Planned (tracker Phase 3b, for both owners to agree):** standard inputs and outputs.
  - Input: code builds a ticket record before Claude starts. Each fact carries its source and
    is taken as of the ticket time: description as opened, project and workflow, versions then,
    inputs present, last failed run. Taking facts as of the ticket time is also the replay mode.
  - Output: Claude answers in one JSON schema, a shared part plus a details block per step or
    playbook. Code checks the required fields per status and renders the DM.
  - The design is on the DSS Triage Flow page.
- **Not done:**
  - Vincent's check of the 30.
  - A replay mode in the tools for closed tickets, which needs both owners.
  - The judge has not run live.
  - Ruff.

### 2026-10-28: answer scoring (`dss eval`) and a golden-set draft (Vincent, with Claude)
- **Changed:**
  - `python -m dss eval <golden> <run folder> [--judge]` scores saved answers against golden
    rows (`dss/eval.py`): evidence read (from the trace's call arguments), citations under
    Checked that resolve (Jira keys, Confluence page ids, code `file:line` at the commit the
    trace shows), known false leads, Suggested labels against Jira's, length. `--judge` asks
    Haiku through `claude -p` with no tools, MCP servers or settings; off by default.
  - `docs/golden.example.jsonl`: the golden row format, with made-up data.
  - `code.files` and `code.line_count`, read-only git helpers for the citation check (not
    tools).
- **Checked:** `uv run pytest` (78 passed; `tests/test_eval.py` runs on the example row with
  faked lookups); mypy clean. Live, no Claude run: `dss eval` on the saved DSS-1229 smoke
  answer parsed all six sections, resolved both Jira citations and matched both labels
  (evidence 0/2, since that run had no trace).
- **Outside the repo (client data, `out/eval/`):** Vincent reviewed the 38 high-confidence
  drafts on the review page; their labels and answers are now in Jira (27 field edits, 18
  CAUSE/EVIDENCE/SOLUTION comments, through Vincent's Atlassian login, each checked back).
  The test-set sort now has 161 ready, 27 needing information, 192 out. A 30-row golden draft
  (`golden_draft.jsonl`: 9 Gate B, 7 Gate C, 13 playbook, 1 Gate A; 5 pilot tickets the bot
  got wrong or stopped short on) and a dry-run-by-default baseline loop (`run_baseline.py`).
- **Not done:** the golden rows need Vincent's evidence pointers and false leads; no baseline
  run (the run budget is not agreed); the judge has not been run live.

### 2026-10-28: smoke runs on Opus; usage log, tool-call traces, `--post-file` (Vincent, with Claude)
- **Smoke runs** (Opus 5.5, high effort; the tracker's Phase 0 smoke tickets): DSS-1228 cost
  $0.24 (123k tokens, 40 s) and DSS-1229 $0.17 (75k tokens, 36 s). Both stopped at Gate C:
  without the workflow's post they had no error text (DSS-1228) and no run folder (DSS-1229).
- **Changed:**
  - Every triage appends a row to `out/usage.csv` (`triage.log_usage`): run time, key,
    posted, turns, seconds, each token kind, cost. The CLI's cost line shows the turns too.
  - `DSS_TRACE=1` traces a triage's tool calls to `out/trace/<key>_<run time>.jsonl`. The
    runner passes the file to `claude -p` as `DSS_TRACE_FILE`; the MCP server inherits it, and
    `server.ReportingTools` writes one JSON line per call: tool, arguments, seconds, then the
    error, or the result's size and first 2,000 characters. The file is named by run time,
    not ticket time, so a rerun keeps the earlier trace for comparison.
  - `python -m dss triage --post-file post.txt` gives Claude the workflow's post, as the Slack
    bot does.
  - The `ask` callable now takes `trace` as a keyword argument (`runner.run_reply` and the
    test fakes).
- **Checked:** `uv run pytest` (62 passed; new tests for the usage rows, the trace lines, the
  trace setting and `--post-file`); mypy clean apart from the Cerberus import it can't see.
  Live: `claude -p` with the real `dss` server called `cerb_prod_version`, and the trace file
  got its line.
- **Not done:** no full triage with `DSS_TRACE=1` or `--post-file` yet; the next DSS-1228 and
  DSS-1229 reruns with their posts would show both.

### 2026-10-28: the triage runs on Opus 5.5 at high effort (Vincent, with Claude)
- **Changed:** `runner.MODEL` is `claude-opus-5-5` (was `claude-sonnet-5-5`), and `claude -p`
  gets `--effort high` (`runner.EFFORT`). This is for now, while reply quality is measured;
  `python -m dss triage --model` still picks another model for one run.
- **Checked:** `uv run pytest` (57 passed); the runner test checks the `--effort` value. The CLI
  (2.1.292) ran Opus 5.5 at high effort on a one-word prompt with no tools.
- **Not done:** no triage on a real ticket yet. Opus at high effort costs more per run than
  Sonnet, so the run budget agreed with Annika should allow for it.

### 2026-10-27: arch dropped; `jira_*` on the Jira REST API (Vincent, with Claude)
- **Why:** arch is not part of this project. Cerberus reads only the client info and the other
  spreadsheets in the DVT folder, which the `cerb_*` tools already read through the project
  index; arch's data has to match client info, so client info is the source here. The `jira_*`
  tools used arch only for its `DVJira` client (a Jira API token in AWS Secrets Manager).
  If Cerberus starts reading arch directly one day, arch becomes a source the triage must read
  too, and a read-only arch tool comes back (both owners agree first).
- **Changed:** `dss/jira.py` calls Jira Cloud's REST API directly (`/rest/api/2/issue/<key>`
  and `/rest/api/2/search/jql`, GET only, standard library, like `confluence.py`). Settings
  `JIRA_URL`, `JIRA_EMAIL`, `JIRA_TOKEN`; each falls back to its `CONFLUENCE_*` value, since one
  Atlassian site and API token serve both. Errors reach Claude as `JiraError` (not set up, token
  refused, no access, no such ticket, bad JQL). `worker.APPS` is `('cerb',)`; `DSS_ARCH_*` and
  `DV_JIRA_EMAIL` are gone from `.env.example`, README and CLAUDE.md.
- **Checked:** `uv run pytest` (57 passed); the Jira tests fake `urlopen` and check the URLs,
  fields and basic-auth header. Live on Vincent's PC (2026-10-28), with a classic Atlassian API
  token in `CONFLUENCE_*`, through the MCP server as Claude calls the tools: `jira_get_issue`
  returns every triage field for a real ticket (458 characters), so the custom field ids in
  `TICKET_FIELDS` are right; `jira_search` and `confluence_search` return results.
- **Not done:** a scoped read-only token (`read:jira-work`, `search:confluence`,
  `read:page:confluence`) would make the token itself read-only; the classic one has the
  account's rights, though the tools only send GET requests. Confluence titles come back
  HTML-escaped (`&amp;`), and the DVT number as a float (`4834.0`); both are small fixes.

### 2026-10-21 to 22: watcher moved here; first commit with fixes and tests (Vincent)
- **Watcher:** Vincent's private DSS watcher runs in a Claude Code session in this repo, as a
  session schedule at minutes 7 and 37, from `out/watcher/` (git-ignored): runbook, state,
  metrics, `smoke.py` (which bot tools work on this PC) and `project_facts.py` (one ticket's
  project facts through the index). It runs `python -m dss triage` only when the smoke check
  says the bot is ready; the session finishes what the bot can't. DSS-1226 was the first new
  ticket; the session triaged it itself, because the bot has no Jira yet.
- **Setup on Vincent's PC:** `.env` has the Cerberus checkout and venv (prod 1.0.4 is
  `ff8a0091e`) and the project index login. A live read of DSS-1225's project passed every
  project tool: `find_project`, `list_folder`, `last_session`, `read_text` and `client_info`.
  Still unset: arch (no `arch-master` worktree yet, AWS access, `DV_JIRA_EMAIL`) and
  Confluence. Until then the bot stops at Record.
- **Blind test on DSS-1225:** given only the workflow's post, the bot missed the cause. The
  client info had been fixed after the ticket, it never searched the code for "2D log", and it
  called a by-design metres/feet difference a lead. Lessons under Next.
- **Fixed in the first commit:**
  - Tool errors reach Claude with their message. mcp 2.x answers any exception other than
    `ToolError` with only "Error executing tool <name>"; `server.ReportingTools` re-raises a
    tool's exceptions as `ToolError`.
  - `code_log_s` no longer times out. Git for Windows runs its `astextplain` text filter on every
    .docx and .pdf blob during `git log -S`: 59 s on Cerberus, then a failure on a committed
    Office lock file. With `--no-textconv` it takes 0.6 s at the release, 4.6 s over all remote
    branches.
  - `DV_PROJECT_INDEX_URL`, `DV_PROJECT_INDEX_ROOT` and `DV_CERB_PROD_DIR` are read at each call.
    They were read at import, before `python -m dss` loads `.env`, so `python -m dss mcp`
    ignored their `.env` values.
  - An index request that times out now says "did not answer within 60 s". It said "cannot reach
    the project index", which misled the DSS-1226 triage: other files read fine.
  - Readability: no inline conditionals or one-letter names, constants at the top of each module,
    formal one-line comments. `worker` names an unknown app instead of raising a bare `KeyError`.
- **Checked:** 55 tests pass (24 before), 98% line coverage (not covered: the Slack Socket Mode
  connection and the `__main__` guard), mypy clean with `--check-untyped-defs`, no problems in
  VS Code. The two defect fixes have tests that fail without them. ruff left to Vincent.
- **For dv-auto-setup-agent (to pass on):** its MCP server may hide tool error messages in the
  same way, if it uses mcp 2.x and its tools raise ordinary exceptions.
- **Proved on DSS-1225, for the prompt:** a missing `casingSplit` column is added empty, so there
  are no split marks (`clientinfo.py:520-535` at `ff8a0091e`). Without marks, the 2D log starts a
  new casing interval at any ID/OD change or any depth gap between joints, with no tolerance
  (`:589`). `check_casing_record` allows gaps up to 1e-4 (`:47`), so a 1.6e-12 ft gap split one
  casing size in two without a warning (DTN-3717).
- Removed a client name from the 2026-10-20 entry: client names stay out of commits.
- **Next** (tool and prompt changes, each tested on real tickets before and after):
  - `code_grep` case-insensitive ("no_analysis" missed NO_ANALYSIS).
  - `python -m dss triage --post-file`: the workflow's post, so the bot has the summary, client
    and DVT when Jira is down; the reply's turn count in the usage line.
  - File times in `cerb_list`, so the bot can tell an input changed after the ticket.
  - `dss/triage.md`: the DSS-1225 facts above; search the code for the output's name as the
    analyst writes it; a unit difference is a lead only with the code that reads it.
  - A knowledge base of past tickets (prototype in `F:\git\dss-kb`, no data) waits on the
    decision to store ticket data locally, in `out/kb/`.
  - An event-driven trigger for the watcher (`out/watcher/poll.py`) is being built in another
    session.

### 2026-10-21: split out of dv-auto-setup-agent (Vincent)
- **Why:** the triage shares nothing with the casing intake and auto-setup work in
  dv-auto-setup-agent (Arsenii's repo), and needs its own reviewers (Vincent and Annika). The DSS
  work was all uncommitted there, so it moved with no history to untangle.
- **Moved:** `dv_mcp/jira.py`, `dv_mcp/code.py`, the project-index tools of `dv_mcp/cerb.py`,
  `dv_mcp/dss_triage.md` (now `dss/triage.md`, unchanged), `tests/test_dss.py` (now
  `tests/test_tools.py`), and `SlackDesk.on_message` / `catch_up` with their tests.
- **Copied from dv-auto-setup-agent at b3f182e, cut down to triage:** `dv_mcp/claude.py` (now
  `dss/runner.py`: one prompt, no Read, no `--add-dir`), `dv_mcp/worker.py`, `dv_mcp/confluence.py`
  with its tests, `cerb.client_info`, and the Slack formatting helpers. Fixes there don't reach
  this repo on their own.
- **Changed in the move:**
  - The MCP server is `dss` (tools `mcp__dss__*`) and serves the 13 triage tools only.
  - No chat sessions: a triage is one answer, saved as `out/answers/<channel>_<ts>.json` so
    `catch_up` knows which tickets are done.
  - `python -m dss triage DSS-1234` runs a triage without Slack, for development.
  - The venv settings are required (`DSS_CERB_PY`/`_CWD`, `DSS_ARCH_PY`/`_CWD`) instead of
    defaulting to another machine's paths. The Slack variables are `DSS_SLACK_*`, so the bot
    can't pick up the DV Assistant app's tokens by accident.
  - A `.env` file at the repo root fills in unset variables.
  - Its own Slack app (`slack_manifest.yaml`) with the `message.channels` / `message.groups`
    events the DV Assistant manifest lacked.
  - `CLAUDE.md` with the rules for Claude Code sessions, shared by both owners.
- Checked: 24 tests pass, ruff clean; `python -m dss mcp` lists 13 tools over stdio.

### 2026-10-20: DSS ticket triage (Vincent; in dv-auto-setup-agent, branch `work-vincent-dss-profile`, read-only)
Paths below are this repo's.
- **Why:** Vincent's private DSS watcher (Claude Code with Slack and Jira MCP, Oct 16-20) showed
  that a fixed triage order keeps Claude on track: on the new flow it went through its sources
  once each, in order, read no screenshots and proved every claim. But it ran in a full session
  where "never post, never edit" were only instructions. The triage moved onto a locked-down
  runner, where those limits are enforced.
- **System prompt** `dss/triage.md` is the triage flow: record, Gate A (Cerberus?), Gate B (known
  setup problem?), Gate C (inputs there?), one playbook, only proved results. Tools only, no Read.
- **Read-only tools:**
  - `jira_get_issue` / `jira_search` (`dss/jira.py`): arch's `DVJira` in the arch venv (token
    from AWS Secrets Manager, account email from `DV_JIRA_EMAIL`). Only the triage fields are
    requested.
  - `cerb_find_project`, `cerb_last_session`, `cerb_prod_version` (`dss/cerb.py`): the DVT's
    project folder (through the project index, see Project access), the reporter's newest
    telemetry log before the ticket (Cerberus version, failed `script_run`s, last
    `config_saved`), and the prod wheel version.
  - `code_grep` (fixed-string by default), `code_read`, `code_log_s` (`dss/code.py`): git reads
    at the `release: v<prod version>` commit on origin/master (or `DV_CERB_REF`), never the
    working tree, so answers match what analysts run.
- **Slack:** `SlackDesk.on_message` triages each "created DSS-…" workflow post (900 s timeout)
  and sends the analysis to the reviewer's DM only, with no Post button. People's messages,
  thread replies, other channels and redelivered events are ignored.
- **Around the clock (off-hours support):** the bot runs on a PC that stays on, started at logon
  by a scheduled task (README). This replaces Vincent's 30-minute polling, which cost about 0.5k
  tokens per empty check and could leave a ticket waiting 30 minutes. Slack does not resend
  missed events, so on start `catch_up` triages tickets from the last 24 h with no saved answer,
  oldest first, alongside live events. Failed triages are retried there too.
- **Project access (2026-10-21):** Z: opens a project only after it is requested in VizApp, for
  45 days, so the bot's login could read 19 of 357 client folders. The `cerb_*` project tools
  read through the project index (Everything HTTP server at 192.168.25.120 over
  `\\192.168.24.252\projects`), approved for read-only use on DSS tickets. They send only GET
  requests, and only for paths inside that share. The login is `DV_PROJECT_INDEX_USER` and
  `DV_PROJECT_INDEX_PASSWORD`. Tools: `cerb_list` (a folder's direct entries, for Gate C inputs)
  and `cerb_read_text` (400-line pages, for the config named by `config_saved`).
  `cerb_client_info` copies a share file to the temp folder first. Checked live on Vincent's PC:
  `find_project` returns a real client project and `list_folder` (`parent:`) lists its
  direct entries; a telemetry log started at 13:02 the same day was already indexed, and
  `read_text` and `last_session` read it (Cerberus 1.0.4); `local_copy` + `client_info` read
  the project's client info.
- **Not done:**
  - Not run against real Jira or Slack. That needs `DV_JIRA_EMAIL` plus arch's AWS access, and
    the bot tokens.
  - Checked live (read-only) on Vincent's PC: prod version 1.0.4 maps to `ff8a0091e`;
    `code_grep`/`code_read` return the expected lines.
- **Next:**
  - Pilot alongside the watcher and compare drafts and tokens per ticket.
  - Then decide whether the reviewer gets a Post button for ticket triage.
