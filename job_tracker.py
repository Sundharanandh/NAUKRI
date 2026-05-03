"""
job_tracker.py — Saves all job activity to jobs_data.json
This file is committed to GitHub and read by the dashboard.
"""

import json
import os
import logging
from datetime import datetime, date

logger = logging.getLogger(__name__)

JOBS_DATA_FILE = "docs/jobs_data.json"


def load_jobs_data() -> dict:
    """Load existing jobs data or create fresh structure."""
    os.makedirs("docs", exist_ok=True)
    if os.path.exists(JOBS_DATA_FILE):
        try:
            with open(JOBS_DATA_FILE, "r", encoding="utf-8") as f:
                return json.load(f)
        except Exception:
            pass
    return {
        "last_updated": "",
        "total_applied": 0,
        "total_manual": 0,
        "applied_jobs": [],
        "manual_jobs": []
    }


def save_applied_job(job: dict):
    """Save a successfully auto-applied job."""
    data = load_jobs_data()
    today = date.today().isoformat()

    # Avoid duplicates
    existing_urls = [j["url"] for j in data["applied_jobs"]]
    if job["url"] in existing_urls:
        return

    data["applied_jobs"].append({
        "id":          len(data["applied_jobs"]) + 1,
        "title":       job.get("title", ""),
        "company":     job.get("company", ""),
        "location":    job.get("location", ""),
        "salary":      job.get("salary", "Not disclosed"),
        "experience":  job.get("exp", ""),
        "skills":      job.get("skills", ""),
        "score":       job.get("score", 0),
        "reason":      job.get("reason", ""),
        "url":         job.get("url", ""),
        "applied_on":  today,
        "applied_at":  datetime.now().strftime("%Y-%m-%d %H:%M"),
        "status":      "Applied"
    })

    data["total_applied"] = len(data["applied_jobs"])
    data["last_updated"]  = datetime.now().strftime("%Y-%m-%d %H:%M")
    _save(data)
    logger.info(f"💾 Saved applied job: {job.get('title')} @ {job.get('company')}")


def save_manual_job(job: dict):
    """Save a job that requires manual application (company portal redirect)."""
    data = load_jobs_data()
    today = date.today().isoformat()

    existing_urls = [j["url"] for j in data["manual_jobs"]]
    if job["url"] in existing_urls:
        return

    data["manual_jobs"].append({
        "id":          len(data["manual_jobs"]) + 1,
        "title":       job.get("title", ""),
        "company":     job.get("company", ""),
        "location":    job.get("location", ""),
        "salary":      job.get("salary", "Not disclosed"),
        "experience":  job.get("exp", ""),
        "skills":      job.get("skills", ""),
        "score":       job.get("score", 0),
        "reason":      job.get("reason", ""),
        "url":         job.get("url", ""),
        "found_on":    today,
        "found_at":    datetime.now().strftime("%Y-%m-%d %H:%M"),
        "status":      "Manual Apply Needed",
        "note":        "This job redirects to company portal — apply manually"
    })

    data["total_manual"] = len(data["manual_jobs"])
    data["last_updated"] = datetime.now().strftime("%Y-%m-%d %H:%M")
    _save(data)
    logger.info(f"📌 Saved manual job: {job.get('title')} @ {job.get('company')}")


def _save(data: dict):
    os.makedirs("docs", exist_ok=True)
    with open(JOBS_DATA_FILE, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2, ensure_ascii=False)
