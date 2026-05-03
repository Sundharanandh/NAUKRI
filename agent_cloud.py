"""
agent_cloud.py — Naukri Agent (All 3 Phases) + Dashboard Update
Runs on GitHub Actions every day at 9:00 AM IST.
After each run, commits updated jobs_data.json so dashboard reflects latest data.
"""

import json
import logging
import os
import sys
import subprocess
from datetime import datetime, date

from browser       import NaukriBrowser
from notifier      import send_daily_report
from resume_parser import extract_resume_text
from job_search    import search_jobs
from job_matcher   import score_jobs
from job_apply     import apply_to_jobs

# ── Logging ──────────────────────────────────────────────────────────
os.makedirs("logs", exist_ok=True)
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s  [%(levelname)s]  %(message)s",
    handlers=[
        logging.FileHandler("logs/agent_log.txt", encoding="utf-8"),
        logging.StreamHandler(sys.stdout)
    ]
)
logger = logging.getLogger(__name__)


def load_config():
    with open("config.json", "r") as f:
        return json.load(f)

def save_config(config):
    with open("config.json", "w") as f:
        json.dump(config, f, indent=2)


def get_phase(config: dict) -> int:
    if os.environ.get("TEST_ALL_PHASES", "").lower() == "true":
        logger.info("🧪 TEST MODE — Running all 3 phases!")
        return 3

    # GitHub Secret AGENT_START_DATE overrides config.json
    env_start = os.environ.get("AGENT_START_DATE", "").strip()
    if env_start:
        start_str = env_start
        logger.info(f"📅 Start date from GitHub Secret: {start_str}")
    else:
        start_str = config.get("agent_start_date")

    if not start_str:
        today = date.today().isoformat()
        config["agent_start_date"] = today
        save_config(config)
        logger.info(f"📅 First run! Start date recorded: {today}")
        return 1

    start = date.fromisoformat(start_str)
    days  = (date.today() - start).days + 1
    phase = 1 if days <= 7 else (2 if days <= 14 else 3)
    logger.info(f"📅 Day {days} since {start_str} → Phase {phase}")
    return phase


def commit_dashboard_data():
    """Commit updated jobs_data.json to GitHub so dashboard auto-updates."""
    try:
        subprocess.run(["git", "config", "user.email", "agent@naukri-bot.com"], check=True)
        subprocess.run(["git", "config", "user.name",  "Naukri Agent"],         check=True)

        # Make sure docs folder and file exist
        os.makedirs("docs", exist_ok=True)
        if not os.path.exists("docs/jobs_data.json"):
            logger.warning("📊 docs/jobs_data.json not found — skipping commit")
            return

        subprocess.run(["git", "add", "docs/jobs_data.json"], check=True)

        result = subprocess.run(
            ["git", "commit", "-m",
             f"📊 Dashboard update — {datetime.now().strftime('%d %b %Y %H:%M')}"],
            capture_output=True, text=True
        )

        stdout = result.stdout + result.stderr
        logger.info(f"📊 Git commit output: {stdout.strip()[:200]}")

        if "nothing to commit" in stdout:
            logger.info("📊 Dashboard: no new jobs to commit.")
            return

        # Push with verbose output
        push_result = subprocess.run(
            ["git", "push"],
            capture_output=True, text=True
        )
        push_out = push_result.stdout + push_result.stderr
        logger.info(f"📊 Git push output: {push_out.strip()[:200]}")

        if push_result.returncode == 0:
            logger.info("📊 ✅ Dashboard updated on GitHub Pages!")
        else:
            logger.error(f"📊 ❌ Push failed: {push_out[:300]}")

    except subprocess.CalledProcessError as e:
        logger.error(f"📊 Git command failed: {e}")
        logger.error(f"   stdout: {e.stdout}")
        logger.error(f"   stderr: {e.stderr}")
    except Exception as e:
        logger.error(f"📊 Dashboard commit error: {e}")


def run():
    logger.info("=" * 55)
    logger.info("  🚀 Naukri AI Agent — Daily Run")
    logger.info(f"  📅 {datetime.now().strftime('%A, %d %B %Y — %I:%M %p')} UTC")
    logger.info("=" * 55)

    config = load_config()
    phase  = get_phase(config)

    report = {
        "phase":           phase,
        "login_success":   False,
        "profile_updated": False,
        "resume_loaded":   False,
        "jobs_found":      0,
        "jobs_matched":    0,
        "matched_jobs":    [],
        "total_applied":   0,
        "applied_jobs":    [],
        "manual_jobs":     [],
        "failed_jobs":     [],
        "errors":          []
    }

    # ── Load Resume ──────────────────────────────────────────────────
    resume_text = ""
    try:
        resume_text = extract_resume_text(config.get("resume_path", "resume.pdf"))
        report["resume_loaded"] = True
    except Exception as e:
        report["errors"].append(f"Resume: {e}")
        logger.error(f"❌ Resume error: {e}")

    # ── Browser ──────────────────────────────────────────────────────
    browser = NaukriBrowser(config, headless=True)

    try:
        # ════════════════════════════════════════════════════
        # PHASE 1 — Login + Profile Update
        # ════════════════════════════════════════════════════
        logger.info("\n── PHASE 1: Login & Profile Update ─────────")
        login_ok = browser.login()
        report["login_success"] = login_ok

        if not login_ok:
            report["errors"].append("Login failed — check GitHub Secrets.")
            return

        profile_ok = browser.update_profile_timestamp()
        report["profile_updated"] = profile_ok
        logger.info("✅ Phase 1 done.")

        # ════════════════════════════════════════════════════
        # PHASE 2 — Job Search + AI Matching
        # ════════════════════════════════════════════════════
        if phase >= 2:
            logger.info("\n── PHASE 2: Job Search & AI Matching ───────")
            jobs = search_jobs(browser.driver, config)
            report["jobs_found"] = len(jobs)

            if jobs and resume_text:
                matched = score_jobs(jobs, resume_text, config)
                report["jobs_matched"] = len(matched)
                report["matched_jobs"] = matched
            logger.info("✅ Phase 2 done.")
        else:
            start_str = os.environ.get("AGENT_START_DATE") or config.get("agent_start_date", date.today().isoformat())
            try:
                remain = 8 - (date.today() - date.fromisoformat(start_str)).days
                logger.info(f"⏳ Phase 2 starts in {remain} day(s).")
            except Exception:
                logger.info("⏳ Phase 2 starts in a few days.")

        # ════════════════════════════════════════════════════
        # PHASE 3 — Auto Apply + Manual Detection
        # ════════════════════════════════════════════════════
        if phase >= 3:
            logger.info("\n── PHASE 3: Auto Apply ──────────────────────")
            if report["matched_jobs"]:
                res = apply_to_jobs(browser.driver, report["matched_jobs"], config)
                report["total_applied"] = res["total_applied"]
                report["applied_jobs"]  = res["applied"]
                report["manual_jobs"]   = res["manual"]
                report["failed_jobs"]   = res["failed"]
                logger.info(
                    f"✅ Phase 3 done — "
                    f"Applied: {res['total_applied']}, "
                    f"Manual: {len(res['manual'])}"
                )
            else:
                logger.info("   No matched jobs to apply.")
        elif phase == 2:
            start_str = os.environ.get("AGENT_START_DATE") or config.get("agent_start_date", date.today().isoformat())
            try:
                remain = 15 - (date.today() - date.fromisoformat(start_str)).days
                logger.info(f"⏳ Phase 3 starts in {remain} day(s).")
            except Exception:
                logger.info("⏳ Phase 3 starts soon.")

        logger.info("\n🎉 All phases complete!")

    except Exception as e:
        report["errors"].append(str(e))
        logger.error(f"❌ Error: {e}")

    finally:
        browser.close()

        # ── Update dashboard ─────────────────────────────────
        commit_dashboard_data()

        # ── Send email report ────────────────────────────────
        try:
            send_daily_report(config, report)
        except Exception as e:
            logger.error(f"Email failed: {e}")

        logger.info("=" * 55)
        logger.info("  Next run: tomorrow 9:00 AM IST")
        logger.info("=" * 55)


if __name__ == "__main__":
    run()
