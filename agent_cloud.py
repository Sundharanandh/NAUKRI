"""
agent_cloud.py — Naukri Agent (All 3 Phases)
Runs on GitHub Actions every day at 9:00 AM IST.

Phase activation (automatic, based on start date):
  Days  1–7  → Phase 1: Login + Profile Update
  Days  8–14 → Phase 2: + Job Search + AI Matching (email results, no apply)
  Days 15+   → Phase 3: + Auto Apply (up to daily cap)
"""

import json
import logging
import os
import sys
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
    # ── TEST MODE: forces all 3 phases at once ───────────────────────
    if os.environ.get("TEST_ALL_PHASES", "").lower() == "true":
        logger.info("🧪 TEST MODE — Running all 3 phases!")
        return 3

    start_str = config.get("agent_start_date")
    if not start_str:
        today = date.today().isoformat()
        config["agent_start_date"] = today
        save_config(config)
        logger.info(f"📅 First run! Start date: {today}")
        return 1
    start = date.fromisoformat(start_str)
    days  = (date.today() - start).days + 1
    phase = 1 if days <= 7 else (2 if days <= 14 else 3)
    logger.info(f"📅 Day {days} since {start_str} → Phase {phase}")
    return phase


def run():
    logger.info("=" * 55)
    logger.info("  🚀 Naukri AI Agent — Daily Run")
    logger.info(f"  📅 {datetime.now().strftime('%A, %d %B %Y — %I:%M %p')} UTC")
    logger.info("=" * 55)

    config = load_config()
    phase  = get_phase(config)

    report = {
        "phase": phase,
        "login_success":   False,
        "profile_updated": False,
        "resume_loaded":   False,
        "jobs_found":      0,
        "jobs_matched":    0,
        "matched_jobs":    [],
        "total_applied":   0,
        "applied_jobs":    [],
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
        # PHASE 1 — Login + Profile Update  (runs every day)
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
        # PHASE 2 — Job Search + AI Matching  (from Day 8)
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
            start  = date.fromisoformat(config["agent_start_date"])
            remain = 8 - (date.today() - start).days
            logger.info(f"⏳ Phase 2 starts in {remain} day(s).")

        # ════════════════════════════════════════════════════
        # PHASE 3 — Auto Apply  (from Day 15)
        # ════════════════════════════════════════════════════
        if phase >= 3:
            logger.info("\n── PHASE 3: Auto Apply ──────────────────────")
            if report["matched_jobs"]:
                res = apply_to_jobs(browser.driver, report["matched_jobs"], config)
                report["total_applied"] = res["total_applied"]
                report["applied_jobs"]  = res["applied"]
                report["failed_jobs"]   = res["failed"]
            else:
                logger.info("   No matched jobs to apply.")
            logger.info("✅ Phase 3 done.")
        elif phase == 2:
            start  = date.fromisoformat(config["agent_start_date"])
            remain = 15 - (date.today() - start).days
            logger.info(f"⏳ Phase 3 (auto-apply) starts in {remain} day(s).")

        logger.info("\n🎉 All phases complete!")

    except Exception as e:
        report["errors"].append(str(e))
        logger.error(f"❌ Error: {e}")

    finally:
        browser.close()
        try:
            send_daily_report(config, report)
        except Exception as e:
            logger.error(f"Email failed: {e}")
        logger.info("=" * 55)
        logger.info("  Next run: tomorrow 9:00 AM IST")
        logger.info("=" * 55)


if __name__ == "__main__":
    run()
