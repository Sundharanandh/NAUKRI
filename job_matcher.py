"""
job_matcher.py — Phase 2: AI job matching using FREE Google Gemini API
With detailed error logging to diagnose failures.
"""

import json
import logging
import time
import os
import urllib.request
import urllib.error

logger = logging.getLogger(__name__)

MATCH_THRESHOLD = 70
GEMINI_API_URL  = "https://generativelanguage.googleapis.com/v1beta/models/gemini-2.0-flash:generateContent"


def score_jobs(jobs: list, resume_text: str, config: dict) -> list:
    if not jobs:
        return []

    api_key = os.environ.get("GEMINI_API_KEY", "").strip()

    if not api_key:
        logger.error("❌ GEMINI_API_KEY not set in GitHub Secrets!")
        return _keyword_match(jobs, config)

    logger.info(f"🤖 AI scoring with Gemini (free tier: 15 req/min)...")
    logger.info(f"   API key starts with: {api_key[:8]}...")

    # ── Test API first ────────────────────────────────────────────
    test_ok, test_err = _test_api(api_key)
    if not test_ok:
        logger.error(f"❌ Gemini API test failed: {test_err}")
        logger.warning("⚠️  Falling back to keyword-based matching")
        return _keyword_match(jobs, config)

    logger.info("   ✅ Gemini API connection OK")

    # ── Limit to top 15 jobs to stay within free tier quota ──────
    # Score only top 15 — keyword match first to pick best candidates
    kw_sorted = sorted(
        jobs,
        key=lambda j: sum(1 for s in config["job_search"]["skills"]
                         if s.lower() in f"{j.get('title','')} {j.get('skills','')}".lower()),
        reverse=True
    )
    jobs_to_score = kw_sorted[:15]
    logger.info(f"   Scoring top {len(jobs_to_score)} jobs (of {len(jobs)} found)")

    # ── Score with retry on rate limit ───────────────────────────
    scored = []
    for i, job in enumerate(jobs_to_score):
        score, reason = _score_with_retry(api_key, job, resume_text, config)
        job["score"]  = score
        job["reason"] = reason
        scored.append(job)
        logger.info(f"   [{i+1}/{len(jobs_to_score)}] {job['title'][:40]} → {score}/100")
        time.sleep(5)  # 5s delay = max 12 req/min — safely under 15/min limit

    scored.sort(key=lambda x: x["score"], reverse=True)
    good = [j for j in scored if j["score"] >= MATCH_THRESHOLD]
    logger.info(f"✅ {len(good)} good matches (≥{MATCH_THRESHOLD}) out of {len(scored)} scored")
    return good


def _score_with_retry(api_key, job, resume_text, config, max_retries=3) -> tuple:
    """Score a single job with automatic retry on rate limit."""
    for attempt in range(max_retries):
        try:
            return _score_job(api_key, job, resume_text, config)
        except urllib.error.HTTPError as e:
            body = e.read().decode("utf-8", errors="ignore")
            if e.code == 429:
                wait = 30 * (attempt + 1)  # 30s, 60s, 90s
                logger.warning(f"   ⏳ Rate limited — waiting {wait}s (attempt {attempt+1}/{max_retries})")
                time.sleep(wait)
            elif e.code in (400, 403):
                logger.error(f"   ❌ API key error (HTTP {e.code}): {body[:100]}")
                return 0, f"API error {e.code}"
            else:
                logger.warning(f"   HTTP {e.code}: {body[:100]}")
                return 0, f"HTTP error {e.code}"
        except Exception as e:
            logger.warning(f"   Attempt {attempt+1} failed: {e}")
            time.sleep(5)

    # All retries failed — use keyword score as fallback
    logger.warning(f"   All retries failed for '{job['title']}' — using keyword score")
    skills = [s.lower() for s in config["job_search"]["skills"]]
    job_text = f"{job.get('title','')} {job.get('skills','')}".lower()
    hits  = sum(1 for s in skills if s in job_text)
    score = min(50 + hits * 5, 75)
    return score, f"Keyword fallback: {hits} skills matched"


def _test_api(api_key: str) -> tuple:
    """Quick API test — returns (success, error_message)."""
    try:
        payload = json.dumps({
            "contents": [{"parts": [{"text": "Reply with exactly: {\"score\": 85, \"reason\": \"test ok\"}"}]}],
            "generationConfig": {"maxOutputTokens": 50}
        }).encode("utf-8")

        req = urllib.request.Request(
            f"{GEMINI_API_URL}?key={api_key}",
            data=payload,
            headers={"Content-Type": "application/json"},
            method="POST"
        )
        with urllib.request.urlopen(req, timeout=15) as resp:
            data = json.loads(resp.read().decode("utf-8"))
            if "candidates" in data:
                return True, ""
            return False, f"Unexpected response: {str(data)[:100]}"
    except urllib.error.HTTPError as e:
        body = e.read().decode("utf-8", errors="ignore")
        return False, f"HTTP {e.code}: {body[:200]}"
    except Exception as e:
        return False, str(e)


def _score_job(api_key: str, job: dict, resume_text: str, config: dict) -> tuple:
    profile = config["profile"]
    skills  = config["job_search"]["skills"]

    prompt = f"""You are a job matching assistant. Score this job vs the candidate.

JOB:
Title   : {job['title']}
Company : {job['company']}
Location: {job['location']}
Exp Req : {job['exp']}
Skills  : {job['skills']}

CANDIDATE:
Experience : {profile['experience_years']} years
Location   : {profile['current_location']}
Skills     : {', '.join(skills)}
Expected   : {profile['expected_salary_lpa']} LPA

RESUME:
{resume_text[:1200]}

Respond ONLY in JSON (no markdown, no extra text):
{{"score": <number 0-100>, "reason": "<one sentence why>"}}

Scoring: 90-100=perfect, 70-89=good, 50-69=partial, 0-49=poor"""

    payload = json.dumps({
        "contents": [{"parts": [{"text": prompt}]}],
        "generationConfig": {"maxOutputTokens": 100, "temperature": 0.1}
    }).encode("utf-8")

    req = urllib.request.Request(
        f"{GEMINI_API_URL}?key={api_key}",
        data=payload,
        headers={"Content-Type": "application/json"},
        method="POST"
    )

    with urllib.request.urlopen(req, timeout=30) as resp:
        data = json.loads(resp.read().decode("utf-8"))

    raw = data["candidates"][0]["content"]["parts"][0]["text"].strip()
    raw = raw.replace("```json", "").replace("```", "").strip()

    try:
        parsed = json.loads(raw)
        score  = int(parsed.get("score", 0))
        reason = str(parsed.get("reason", ""))
        return score, reason
    except Exception:
        import re
        m = re.search(r'"score"\s*:\s*(\d+)', raw)
        return (int(m.group(1)) if m else 0), raw[:100]


def _keyword_match(jobs: list, config: dict) -> list:
    """
    Fallback: simple keyword-based matching when Gemini API is unavailable.
    Scores jobs based on skill overlap with candidate profile.
    """
    logger.info("🔤 Using keyword-based matching (Gemini unavailable)...")
    skills = [s.lower() for s in config["job_search"]["skills"]]
    location = config["profile"]["current_location"].lower()
    matched = []

    for job in jobs:
        score  = 50  # Base score
        reason = ""

        job_text = (
            f"{job.get('title','')} {job.get('skills','')} "
            f"{job.get('company','')} {job.get('location','')}"
        ).lower()

        # +5 per matching skill
        skill_hits = sum(1 for s in skills if s in job_text)
        score += skill_hits * 5

        # +10 if location matches
        if location in job_text:
            score += 10

        # +5 for exact title match
        title_lower = job.get('title', '').lower()
        if any(k.lower() in title_lower for k in config["job_search"]["keywords"]):
            score += 5

        score  = min(score, 95)
        reason = f"Keyword match: {skill_hits} skills matched"

        job["score"]  = score
        job["reason"] = reason

        if score >= MATCH_THRESHOLD:
            matched.append(job)
            logger.info(f"   ✅ {job['title'][:40]} → {score}/100 ({reason})")

    matched.sort(key=lambda x: x["score"], reverse=True)
    logger.info(f"   {len(matched)} keyword matches (≥{MATCH_THRESHOLD})")
    return matched
