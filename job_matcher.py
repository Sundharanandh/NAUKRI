"""
job_matcher.py — Phase 2: AI job matching using FREE Google Gemini API
No credit card needed. Free tier: 15 requests/min, 1500 requests/day.
"""

import json
import logging
import time
import os
import urllib.request

logger = logging.getLogger(__name__)

MATCH_THRESHOLD = 70
GEMINI_API_URL  = "https://generativelanguage.googleapis.com/v1beta/models/gemini-1.5-flash:generateContent"


def score_jobs(jobs: list, resume_text: str, config: dict) -> list:
    if not jobs:
        return []

    api_key = os.environ.get("GEMINI_API_KEY", "")
    if not api_key:
        logger.error("❌ GEMINI_API_KEY not set in GitHub Secrets!")
        return []

    logger.info(f"🤖 AI scoring {len(jobs)} jobs (free Gemini API)...")
    scored = []

    for i, job in enumerate(jobs):
        try:
            score, reason = _score_job(api_key, job, resume_text, config)
            job["score"]  = score
            job["reason"] = reason
            scored.append(job)
            logger.info(f"   [{i+1}/{len(jobs)}] {job['title']} @ {job['company']} → {score}/100")
            time.sleep(2)   # Stay within free tier rate limit (15 req/min)
        except Exception as e:
            logger.warning(f"   ⚠️  Scoring failed for '{job['title']}': {e}")
            job["score"]  = 0
            job["reason"] = "Scoring failed"
            scored.append(job)

    scored.sort(key=lambda x: x["score"], reverse=True)
    good = [j for j in scored if j["score"] >= MATCH_THRESHOLD]
    logger.info(f"✅ {len(good)} good matches (score ≥ {MATCH_THRESHOLD})")
    return good


def _score_job(api_key: str, job: dict, resume_text: str, config: dict) -> tuple:
    profile = config["profile"]
    skills  = config["job_search"]["skills"]

    prompt = f"""You are a job matching assistant. Score this job vs the candidate.

JOB:
Title   : {job['title']}
Company : {job['company']}
Location: {job['location']}
Exp Req : {job['exp']}
Salary  : {job['salary']}
Skills  : {job['skills']}

CANDIDATE:
Experience : {profile['experience_years']} years
Location   : {profile['current_location']}
Skills     : {', '.join(skills)}
Expected   : {profile['expected_salary_lpa']} LPA

RESUME (first 1500 chars):
{resume_text[:1500]}

Respond ONLY in JSON (no extra text, no markdown):
{{"score": <0-100>, "reason": "<one sentence>"}}

Guide: 90-100=perfect match, 70-89=good match, 50-69=partial, 0-49=poor"""

    payload = json.dumps({
        "contents": [{"parts": [{"text": prompt}]}],
        "generationConfig": {"maxOutputTokens": 150, "temperature": 0.1}
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
        return int(parsed.get("score", 0)), str(parsed.get("reason", ""))
    except Exception:
        import re
        m = re.search(r'"score"\r*:\s*(\d+)', raw)
        return (int(m.group(1)) if m else 0), "Parse error"
