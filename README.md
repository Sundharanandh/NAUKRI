# Job links and Excel digest

Receive new Java job links by email three times a day, with an automatically updated Excel workbook. Review and apply yourself. The scheduled workflow does **not** log into job websites, update your profile, answer screening questions, or submit applications.

This replaces the previous phase-based Naukri agent. Historical code and dashboard remain for reference. Start with this README; `LEGACY_SETUP.md` describes the retired workflow.

## What changed

- Persistent job/email history survives fresh GitHub Actions runners. Jobs already in the legacy dashboard history are excluded too.
- Exact skill patterns distinguish Java from JavaScript. All five configured skills are required.
- Matching checks the title, city, an explicit experience range including 2.5 years, posting age and known expiry. Incomplete evidence goes to **Needs review**, rather than being labelled a match.
- Every run creates `Job_Tracker.xlsx`, attaches it to the digest, and uploads it as a GitHub Actions artifact.
- Email-link reservations are pushed before SMTP delivery. If saving history fails, email is not sent. An ambiguous SMTP result holds the links instead of automatically resending them.
- The target schedule is **09:00, 14:00, 19:00 Asia/Kolkata**. GitHub schedules can be delayed or skipped; these are not guaranteed delivery times.

No Gemini, paid AI subscription, Chrome or job-platform password is needed for this workflow. Public company-board feeds do not require ATS credentials. GitHub Actions usage is subject to the account's included allowances and settings.

## Inputs and coverage

| Source | Input method | Configuration |
| --- | --- | --- |
| LinkedIn, Naukri, Indeed | Direct job links in labelled job-alert emails | Enabled by default |
| Foundit, Instahyre, Hirist | Direct job links in labelled job-alert emails | Enabled by default |
| Cutshort, Wellfound | Direct job links in labelled job-alert emails | Enabled by default |
| Greenhouse | Public job-board JSON feed, checked on each run | Add specific companies to `company_boards` |
| Lever (global or EU) | Public Postings API, checked on each run | Add specific companies to `company_boards` |
| Ashby | Public Job Postings API, checked on each run | Add specific companies to `company_boards` |
| Other company career sites | Direct links in labelled emails, public JobPosting details | Add the company's job-page host to `company_domains` |

### Platform alerts

The email input is **official job-alert emails** labelled `JobAlerts` in your Gmail account. It is not a search crawler or login bot for the eight platforms. Enable their alerts or recommendations yourself where offered, then use Gmail filters to label genuine job-alert emails. If a platform does not offer suitable emails, use its supported company feed or add selected job links for manual review instead; this code does not create alerts on its behalf. Forward alerts from your other email account or change delivery where supported. Forwarding may require your confirmation.

`enabled_platforms` can contain any of `LinkedIn`, `Naukri`, `Indeed`, `Foundit`, `Instahyre`, `Hirist`, `Cutshort`, `Wellfound`. Remove an entry to stop importing that platform. Homepage/search links and lookalike domains are rejected. Known tracking parameters and common URL aliases are normalized before deduplication. Cutshort short job links are recognized, but cross-host redirect or login-only pages still require manual review.

Some platforms block automated requests or omit public structured details. Blocked/incomplete pages go into **Needs review**, not the confirmed-match email list. Tracking links that cannot be safely unwrapped may not be imported. The workflow does not bypass login checks or CAPTCHAs. Running three times does not make a daily source alert arrive three times, and ten suitable openings daily cannot be guaranteed.

### Company career feeds

`company_boards` is empty by default: there is no preselected employer list. These connectors perform direct read-only feed checks, independently of incoming mail. Copy relevant entries from `company_boards.example.json`, replace the example board/company names, and enable them. You can find a board slug in a company's actual careers URL:

```json
"company_boards": [
  {"provider": "greenhouse", "board": "your-greenhouse-token", "company": "Company name"},
  {"provider": "lever", "board": "your-lever-site", "company": "Company name", "region": "global"},
  {"provider": "ashby", "board": "YourAshbyBoard", "company": "Company name"}
]
```

Examples are placeholders, not working company selections. Greenhouse URLs look like `job-boards.greenhouse.io/BOARD/jobs/ID`; Lever uses `jobs.lever.co/BOARD/ID` or `jobs.eu.lever.co/BOARD/ID` (set `region: "eu"`); Ashby uses `jobs.ashbyhq.com/BOARD/ID`. The same configured tenant links are also accepted from alert emails. Unknown employers on shared ATS hosts are not imported merely because another company uses that host.

The public fields are normalized into the same matching, history, email and Excel pipeline. Greenhouse `first_published` and Ashby `publishedAt` are used when present. **An update timestamp or Lever `createdAt` is not treated as publication.** A Lever listing without an explicit publication date remains in Needs review, even if its other criteria match. This preserves the recent-posting requirement rather than guessing freshness.

Up to 20 company boards and 300 postings per board are read by default; `max_company_jobs_per_board` accepts 1–1000. Lever is paginated. Greenhouse/Ashby returned jobs are ordered by known publication date before applying the cap. The shared processing budget remains `max_pages_per_run`; known newer company posts are prioritized among equally checked candidates. Caps are reported in source diagnostics, not silently treated as complete coverage.

A failed board does not prevent other boards from being checked. Gmail alert failures are also reported while remaining company feed results can still be processed. Email reports, `run_summary.json` and the workbook's **Source status** sheet expose partial failures; an empty result is not evidence that every source succeeded.

For other company careers, add the real job-page hostname to `company_domains`, enable its email alerts, and label those messages. Do not allowlist an entire shared ATS domain to select companies. Set `email_alerts_enabled: false` only if you want company-feed-only collection; Gmail SMTP credentials are still required to send the digest. A company-feed-only **preview** needs no Gmail password.

Provider references: [Greenhouse Job Board API](https://docs.greenhouse.io/job-board), [Lever Postings API](https://github.com/lever/postings-api), [Ashby Job Postings API](https://developers.ashbyhq.com/docs/public-job-posting-api).

## Setup in this repository

1. Merge the code into your repository's default branch. Scheduled workflows execute the default branch. Keep the old workflow file replaced; do not leave a second legacy auto-apply schedule enabled.
2. Open **Settings → Secrets and variables → Actions**. Set these **repository secrets**:

   | Secret | Value |
   | --- | --- |
   | `NOTIFY_EMAIL` | Your Gmail address — mailbox to read and sender |
   | `GMAIL_APP_PASSWORD` | A Google App Password created in your own Google account |

   Never put the app password in a source file, issue, screenshot, commit or chat. Google App Passwords generally require 2-Step Verification and may be unavailable on some accounts; see [Google's instructions](https://support.google.com/accounts/answer/185833). If unavailable, this implementation needs Gmail OAuth before it can run. Your normal Gmail password is not a substitute.

3. Optional **repository variables**:

   | Variable | Default |
   | --- | --- |
   | `DIGEST_EMAIL_TO` | Same address as `NOTIFY_EMAIL` |
   | `JOB_ALERT_LABEL` | `JobAlerts` |

4. In Gmail, create that label and set up the platform alerts and labelling filters described above. Gmail IMAP must be available for email-alert collection. Company feeds do not use IMAP.
5. Permit this workflow's `GITHUB_TOKEN` to write repository contents. The workflow requests `contents: write`; organization policies and branch rules must also allow creating/updating **`job-digest-state`**. No personal GitHub token is needed by the scheduled job.
6. Open **Actions → Job links and Excel digest → Run workflow**. Choose **preview** first. This reads alerts, creates the tracker and persists discovery history; it sends no email and does not reserve jobs as emailed.
7. Download the `job-tracker-…` artifact. Check `Job_Tracker.xlsx`, `Email_Preview.txt` and `run_summary.json`.
8. Run again with **send**. Check Gmail inbox/spam and the workbook's **Delivery log**. The schedule uses send mode automatically once the workflow is on the default branch.

Your PC can be off. GitHub Actions performs the scheduled runs. Inactive repositories and GitHub scheduling/account policies can affect automatic execution; check the Actions page if runs stop.

## Your configured matching policy

`digest_config.json` contains editable settings:

| Setting | Default |
| --- | --- |
| Experience | 2.5 years |
| Cities | Bengaluru/Bangalore, Chennai, Hyderabad, Secunderabad, Mumbai |
| Required skills | Java, Spring Boot, Microservices, Angular, AWS |
| Role | Java/Spring Boot developer or engineer; senior/lead and unrelated titles excluded |
| Recent window | Posted within 7 days; newest posting first |
| Links per email | Up to 10 new matches |
| Expected salary | INR 10 LPA, negotiable; **not a hard filter** |
| Notice period | 15 days; not used to assume employer acceptance |

These are conservative text checks, not a guarantee that every sentence in a posting is compatible. Single-number experience requirements, conflicting ranges, unknown remote eligibility, missing skills and missing dates require review. Remote roles without an explicit preferred city are queued for review rather than assuming India eligibility. Unknown salary is displayed for review. Titles without Java/Spring Boot are excluded even when the company may offer Java work. All five required skills can produce very few matches; relax the configuration only if you want a broader policy.

## Excel and persistent history

The **`job-digest-state` branch** stores `jobs.sqlite3`, `Job_Tracker.xlsx` and `run_summary.json`. It is restored before every run. Do not delete the database/branch to troubleshoot duplicates: doing so erases email history. A failed restore stops the run rather than treating it as new history.

Workbook sheets:

- **Matches:** listings passing the configured checks.
- **Needs review:** missing details or blocked public pages, with a reason.
- **Excluded:** stale/out-of-scope listings and imported historical dashboard jobs.
- **Delivery log:** SMTP acceptance, attempts or uncertain outcomes. SMTP acceptance does not prove inbox delivery.
- **Source status:** company feed/Gmail collection outcomes, caps, malformed records and error types. The JSON summary also breaks discovered links down by platform.

The email attachment is the workbook snapshot immediately before that send. The state branch and Actions artifact contain the final post-send workbook.

Cloud runs update discovery and email status. **Application status remains manual** because the workflow cannot know you applied elsewhere. Your downloaded workbook is a snapshot; local edits do not sync back to GitHub. When run locally, the exporter preserves your existing Application status / Applied date / My notes cells by URL.

State contains job URLs, publicly retrieved posting metadata, source health and delivery timestamps, not raw Gmail messages, credentials or resume content. On a public repository that state is publicly readable. Keep private application notes out of committed files; use a private repository if you want the tracker/history private.

URL normalization removes tracking parameters. Matching company/title/location/posting-date fingerprints additionally suppress likely duplicate advertisements. Conservative fingerprint grouping can merge distinct same-day similar vacancies. Existing dashboard history cannot reconstruct every link ever emailed, so a historical email absent from that history may appear once during migration.

## Diagnostics

- **No workbook:** inspect the first failed Actions step; missing secrets or Gmail label stop collection. Dependencies and regression tests run before collection.
- **No emails:** run **send**, check SMTP errors and Gmail spam, and confirm the Gmail App Password belongs to `NOTIFY_EMAIL`.
- **Same links:** ensure the state branch restores successfully, nobody deletes history, and no old workflow still sends reports. `concurrency` serializes runs in this repository. Separate repositories need their own coordination.
- **No confirmed matches:** inspect **Needs review** and the strict five-skill policy. The system does not fill the quota with unrelated or previously emailed jobs.
- **Pending/uncertain delivery:** check Gmail Sent before any retry. Attempted links are intentionally held; inspect the Excel URLs if an email was missed. This prioritizes avoiding repeats over automatic retries.
- **Changed criteria:** previously excluded/sent records remain in history. Review or deliberately migrate existing history if changing policy; do not blindly delete it.

The old HTML dashboard is retained as historical data and its existing manual-run button now requests a preview by default. It does not display the new Excel tracker or reflect its email delivery history. Use Actions, the workbook and state branch for the current automation.

## Local development / offline test

Python 3.11 or newer:

```powershell
py -3 -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
.\.venv\Scripts\python.exe -m unittest discover -s tests -v
```

For a live local preview, set `NOTIFY_EMAIL` and `GMAIL_APP_PASSWORD` in your process environment without committing them, then run `python digest_agent.py --preview`. Local files default to `local_state/`, which is gitignored. For offline preview, `--input path/to/jobs.json --preview` accepts an array of structured `JobPosting` objects with an extra `url` field. Provide `NOTIFY_EMAIL` for preview formatting; no Gmail connection or app password is required in that offline mode.

Tests cover exact-skill matching, eligibility/date handling, all new platform URL rules and mail ingestion, company feed normalization/pagination/caps/failure isolation, combined feed/email deduplication, Excel notes/source status, successful/ambiguous delivery deduplication, authentication/persistence failures and restoring history through a real local Git remote. They do not certify live Gmail, third-party site availability or GitHub scheduling. Validate preview then send after configuring your account.
