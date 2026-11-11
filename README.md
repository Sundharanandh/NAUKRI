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

No Gemini, paid AI subscription, Chrome, Naukri password, LinkedIn password or Indeed password is needed for this workflow. GitHub Actions usage is subject to the account's included allowances and settings.

## Inputs and coverage

The input is **official job-alert emails** labelled `JobAlerts` in your Gmail account. The script extracts direct job links from LinkedIn, Naukri and Indeed, plus explicitly allowlisted company-career domains. It reads public `JobPosting` structured data when the page permits access.

Enable the platform alerts yourself. Existing Naukri credentials do not automatically create those alerts. Forward LinkedIn alerts from your other email account or change alert delivery where supported, so they arrive in the mailbox being read. Forwarding may require your confirmation. Use Gmail filters to apply `JobAlerts` to genuine job-alert messages; do not label unrelated mail.

Some platforms block automated requests, use redirects the parser cannot safely unwrap, or omit public structured details. Blocked/incomplete pages go into the workbook for review. The workflow does not bypass login checks or CAPTCHAs. It cannot guarantee ten suitable openings daily. Running three times does not cause sources to issue new alert emails three times.

Company careers: put the actual job-page hostnames into `company_domains` in `digest_config.json`, enable the company's email alerts, and label those messages too. No company domains are preselected. For ATS platforms, prefer a company's own hostname rather than allowlisting every tenant of a shared ATS domain.

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

4. In Gmail, create that label and set up the platform alerts and labelling filters described above. Gmail IMAP must be available for the account.
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

The email attachment is the workbook snapshot immediately before that send. The state branch and Actions artifact contain the final post-send workbook.

Cloud runs update discovery and email status. **Application status remains manual** because the workflow cannot know you applied elsewhere. Your downloaded workbook is a snapshot; local edits do not sync back to GitHub. When run locally, the exporter preserves your existing Application status / Applied date / My notes cells by URL.

State contains job URLs, publicly retrieved posting metadata and delivery timestamps, not raw Gmail messages, credentials or resume content. On a public repository that state is publicly readable. Keep private application notes out of committed files; use a private repository if you want the tracker/history private.

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

Tests cover exact-skill matching, eligibility/date handling, Excel notes, successful/ambiguous delivery deduplication, authentication/persistence failures and restoring history through a real local Git remote. They do not certify live Gmail, third-party site availability or GitHub scheduling. Validate preview then send after configuring your account.
