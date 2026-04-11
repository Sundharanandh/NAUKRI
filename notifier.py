"""
notifier.py — Daily email report (all phases)
"""

import smtplib
import logging
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart
from datetime import datetime

logger = logging.getLogger(__name__)


def send_daily_report(config: dict, report: dict):
    try:
        sender       = config["notifications"]["email"]
        app_password = config["notifications"]["gmail_app_password"]
        subject      = f"[Naukri Agent] Daily Report — {datetime.now().strftime('%d %b %Y')}"

        p1_login   = "✅" if report.get("login_success")   else "❌"
        p1_profile = "✅" if report.get("profile_updated") else "❌"
        p1_resume  = "✅" if report.get("resume_loaded")   else "❌"

        jobs_found   = report.get("jobs_found", 0)
        jobs_matched = report.get("jobs_matched", 0)

        matched_block = ""
        for job in report.get("matched_jobs", [])[:10]:
            matched_block += (
                f"\n  [{job.get('score',0):>3}/100] {job.get('title','?')} @ {job.get('company','?')}"
                f"\n         Reason : {job.get('reason','')}"
                f"\n         Link   : {job.get('url','')}\n"
            )
        if not matched_block:
            matched_block = "  No matches today."

        total_applied = report.get("total_applied", 0)
        applied_block = ""
        for job in report.get("applied_jobs", []):
            applied_block += f"\n  ✅ {job.get('title')} @ {job.get('company')} (score:{job.get('score')})"
        if not applied_block:
            applied_block = "  None today."

        failed_block = ""
        for job in report.get("failed_jobs", []):
            failed_block += f"\n  ❌ {job.get('title')} @ {job.get('company')}"
        if not failed_block:
            failed_block = "  None."

        errors = report.get("errors", [])
        error_block = "\n".join(f"  • {e}" for e in errors) if errors else "  None"

        phase = report.get("phase", 1)

        body = f"""
╔══════════════════════════════════════════╗
   NAUKRI AGENT — DAILY REPORT
   {datetime.now().strftime('%A, %d %B %Y — %I:%M %p')}
   Running Phase {phase}
╚══════════════════════════════════════════╝

── PHASE 1: Login & Profile ─────────────
  Login          : {p1_login}
  Profile Update : {p1_profile}
  Resume Loaded  : {p1_resume}

── PHASE 2: Job Search & AI Match ───────
  Jobs Found     : {jobs_found}
  Good Matches   : {jobs_matched} (score ≥ 70/100)

  Matched Jobs:
{matched_block}

── PHASE 3: Auto Apply ──────────────────
  Applied Today  : {total_applied} jobs
{applied_block}

  Failed:
{failed_block}

── Errors ───────────────────────────────
{error_block}

══════════════════════════════════════════
  Runs daily 9AM IST — GitHub Actions
  Repo: github.com/udhayakumar6100/naukri-agent
══════════════════════════════════════════
"""

        msg = MIMEMultipart()
        msg["From"]    = sender
        msg["To"]      = sender
        msg["Subject"] = subject
        msg.attach(MIMEText(body, "plain"))

        with smtplib.SMTP_SSL("smtp.gmail.com", 465) as server:
            server.login(sender, app_password)
            server.sendmail(sender, sender, msg.as_string())

        logger.info("📧 Daily report sent!")

    except Exception as e:
        logger.error(f"❌ Email failed: {e}")
