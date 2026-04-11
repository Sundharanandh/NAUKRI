"""
job_search.py — Phase 2: Search Naukri for matching jobs
"""

import time
import logging
from selenium.webdriver.common.by import By
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC
from selenium.common.exceptions import TimeoutException

logger = logging.getLogger(__name__)


def search_jobs(driver, config: dict) -> list:
    job_search = config["job_search"]
    keywords   = job_search["keywords"]
    location   = config["profile"]["preferred_locations"][0]
    exp_min    = job_search["experience_min"]
    exp_max    = job_search["experience_max"]

    all_jobs = []
    for keyword in keywords[:3]:
        logger.info(f"🔍 Searching: '{keyword}' in {location}...")
        jobs = _search_keyword(driver, keyword, location, exp_min, exp_max)
        logger.info(f"   Found {len(jobs)} jobs")
        all_jobs.extend(jobs)
        time.sleep(3)

    # Deduplicate
    seen, unique = set(), []
    for job in all_jobs:
        if job["url"] not in seen:
            seen.add(job["url"])
            unique.append(job)

    logger.info(f"📋 Total unique jobs: {len(unique)}")
    return unique


def _search_keyword(driver, keyword, location, exp_min, exp_max) -> list:
    jobs = []
    try:
        kw  = keyword.lower().replace(" ", "-")
        loc = location.lower().replace(" ", "-")
        url = (f"https://www.naukri.com/{kw}-jobs-in-{loc}"
               f"?experience={exp_min}&to={exp_max}&jobAge=1")
        driver.get(url)
        time.sleep(4)

        try:
            WebDriverWait(driver, 15).until(
                EC.presence_of_element_located(
                    (By.CSS_SELECTOR, ".srp-jobtuple-wrapper, .jobTuple"))
            )
        except TimeoutException:
            logger.warning(f"   ⚠️  No job cards for '{keyword}'")
            return []

        cards = driver.find_elements(
            By.CSS_SELECTOR, ".srp-jobtuple-wrapper, article.jobTuple")[:15]

        for card in cards:
            try:
                job = _extract_job(card)
                if job:
                    jobs.append(job)
            except Exception:
                continue

    except Exception as e:
        logger.error(f"   ❌ Search error: {e}")

    return jobs


def _extract_job(card) -> dict:
    def safe_text(sel):
        try:
            return card.find_element(By.CSS_SELECTOR, sel).text.strip()
        except Exception:
            return ""

    def safe_href(sel):
        try:
            href = card.find_element(By.CSS_SELECTOR, sel).get_attribute("href")
            return href if href and href.startswith("http") else ""
        except Exception:
            return ""

    title   = safe_text(".title, .jobTitle, a.title")
    company = safe_text(".comp-name, .companyName, .subTitle")
    exp     = safe_text(".exp-wrap li, .experience")
    salary  = safe_text(".sal-wrap li, .salary")
    loc     = safe_text(".loc-wrap li, .location")
    skills  = safe_text(".tags-gt, .skill-list, .techSkill")
    url     = safe_href(".title, a.title") or safe_href("a")

    if not title or not url:
        return None

    return {
        "title": title, "company": company, "exp": exp,
        "salary": salary, "location": loc, "skills": skills,
        "url": url, "applied": False, "score": 0
    }
