"""
job_apply.py — Phase 3: Auto-apply to matched jobs
"""

import time
import logging
import json
import os
import random
from datetime import datetime
from selenium.webdriver.common.by import By
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC
from selenium.common.exceptions import TimeoutException

logger = logging.getLogger(__name__)
APPLIED_LOG = "logs/applied_jobs.json"


def load_applied_jobs() -> set:
    if os.path.exists(APPLIED_LOG):
        try:
            with open(APPLIED_LOG, "r") as f:
                return set(json.load(f).get("urls", []))
        except Exception:
            return set()
    return set()


def save_applied_job(url, title, company):
    os.makedirs("logs", exist_ok=True)
    data = {}
    if os.path.exists(APPLIED_LOG):
        try:
            with open(APPLIED_LOG, "r") as f:
                data = json.load(f)
        except Exception:
            data = {}

    urls    = data.get("urls", [])
    details = data.get("details", [])

    if url not in urls:
        urls.append(url)
        details.append({
            "title": title, "company": company, "url": url,
            "applied_on": datetime.now().strftime("%Y-%m-%d %H:%M")
        })

    with open(APPLIED_LOG, "w") as f:
        json.dump({"urls": urls, "details": details}, f, indent=2)


def apply_to_jobs(driver, matched_jobs: list, config: dict) -> dict:
    daily_cap       = config["job_search"].get("max_applications_per_day", 15)
    already_applied = load_applied_jobs()
    new_jobs        = [j for j in matched_jobs if j["url"] not in already_applied]

    results = {"applied": [], "failed": [], "total_applied": 0}
    logger.info(f"📨 {len(new_jobs)} new jobs to apply (cap: {daily_cap})")

    for job in new_jobs:
        if results["total_applied"] >= daily_cap:
            logger.info(f"   ⏹️  Daily cap reached.")
            break

        title, company, url = job["title"], job["company"], job["url"]
        logger.info(f"   📩 Applying: {title} @ {company} (score: {job['score']})")

        if _apply_single(driver, url):
            save_applied_job(url, title, company)
            results["applied"].append({"title": title, "company": company, "score": job["score"], "url": url})
            results["total_applied"] += 1
            logger.info(f"   ✅ Applied!")
        else:
            results["failed"].append({"title": title, "company": company})
            logger.warning(f"   ❌ Failed")

        time.sleep(random.uniform(8, 15))   # human-like delay

    logger.info(f"✅ Applied to {results['total_applied']} jobs today")
    return results


def _apply_single(driver, url: str) -> bool:
    try:
        driver.get(url)
        time.sleep(3)

        apply_btn = None
        for by, loc in [
            (By.XPATH, '//button[contains(text(),"Apply")]'),
            (By.XPATH, '//a[contains(text(),"Apply")]'),
            (By.CSS_SELECTOR, 'button.apply-button'),
            (By.CSS_SELECTOR, '#apply-button'),
            (By.XPATH, '//button[contains(text(),"Easy Apply")]'),
        ]:
            try:
                apply_btn = WebDriverWait(driver, 5).until(EC.element_to_be_clickable((by, loc)))
                break
            except Exception:
                continue

        if not apply_btn:
            return False

        if "applied" in apply_btn.text.lower():
            return True   # Already applied

        driver.execute_script("arguments[0].click();", apply_btn)
        time.sleep(3)
        _handle_popup(driver)
        return True

    except Exception as e:
        logger.error(f"      Error: {e}")
        return False


def _handle_popup(driver):
    for by, loc in [
        (By.XPATH, '//button[contains(text(),"Apply")]'),
        (By.XPATH, '//button[contains(text(),"Confirm")]'),
        (By.XPATH, '//button[contains(text(),"Submit")]'),
    ]:
        try:
            btn = WebDriverWait(driver, 3).until(EC.element_to_be_clickable((by, loc)))
            driver.execute_script("arguments[0].click();", btn)
            time.sleep(2)
            break
        except Exception:
            continue
