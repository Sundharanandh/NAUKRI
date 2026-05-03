"""
job_apply.py — Phase 3: Auto-apply with chatbot questionnaire handling
Handles:
  1. Direct Naukri apply (no questions) → "applied"
  2. Naukri chatbot questions (CTC, experience, skills) → answers using AI → "applied"
  3. Company portal redirect/new tab → "manual"
  4. No apply button → "failed"
"""

import time
import logging
import json
import os
import random
import re
import urllib.request
from datetime import datetime
from selenium.webdriver.common.by import By
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC
from selenium.common.exceptions import TimeoutException

logger = logging.getLogger(__name__)
APPLIED_LOG = "logs/applied_jobs.json"

EXTERNAL_DOMAINS = [
    "greenhouse.io", "lever.co", "workday.com", "taleo.net",
    "successfactors.com", "icims.com", "jobvite.com", "smartrecruiters.com",
    "myworkdayjobs.com", "linkedin.com", "instahyre.com", "hirist.com",
    "freshteam.com", "zohorecruit.com", "keka.com", "darwinbox.com",
    "bamboohr.com", "recruitcrm.io", "peoplestrong.com",
]

GEMINI_URL = "https://generativelanguage.googleapis.com/v1beta/models/gemini-2.0-flash:generateContent"


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
    daily_cap    = config["job_search"].get("max_applications_per_day", 20)
    already_done = load_applied_jobs()

    try:
        from job_tracker import load_jobs_data
        jd = load_jobs_data()
        already_done |= set(j["url"] for j in jd.get("manual_jobs", []))
    except Exception:
        pass

    new_jobs = [j for j in matched_jobs if j["url"] not in already_done]
    results  = {"applied": [], "manual": [], "failed": [], "total_applied": 0}

    logger.info(f"📨 {len(new_jobs)} new jobs to process (cap: {daily_cap})")

    for job in new_jobs:
        if results["total_applied"] >= daily_cap:
            logger.info("   ⏹️  Daily cap reached.")
            break

        title, company = job["title"], job["company"]
        logger.info(f"\n   📩 {title} @ {company}")

        result = _apply_single(driver, job, config)

        if result == "applied":
            save_applied_job(job["url"], title, company)
            results["applied"].append(job)
            results["total_applied"] += 1
            logger.info(f"   ✅ Applied on Naukri directly!")

        elif result == "manual":
            try:
                from job_tracker import save_manual_job
                save_manual_job(job)
            except Exception:
                pass
            results["manual"].append(job)
            logger.info(f"   📌 Manual apply needed (company portal)")

        else:
            results["failed"].append(job)
            logger.warning(f"   ❌ Failed")

        time.sleep(random.uniform(6, 12))

    logger.info(
        f"\n📊 Summary — Applied: {results['total_applied']}, "
        f"Manual: {len(results['manual'])}, Failed: {len(results['failed'])}"
    )
    return results


def _apply_single(driver, job: dict, config: dict) -> str:
    """Apply to one job. Returns 'applied' | 'manual' | 'failed'"""
    url = job["url"]
    try:
        driver.get(url)
        time.sleep(3)

        original_url  = driver.current_url
        original_tabs = set(driver.window_handles)

        # ── Find Apply button ──────────────────────────────────────
        apply_btn = _find_apply_button(driver)

        if not apply_btn:
            logger.info(f"   No Apply button found")
            return "failed"

        if "applied" in (apply_btn.text or "").lower():
            logger.info(f"   Already applied on Naukri")
            return "applied"

        # ── Click Apply ────────────────────────────────────────────
        driver.execute_script("arguments[0].click();", apply_btn)
        time.sleep(4)

        # ── Check what happened after clicking ─────────────────────
        current_url  = driver.current_url
        current_tabs = set(driver.window_handles)
        new_tabs     = current_tabs - original_tabs

        # Case 1: New tab → company portal
        if new_tabs:
            try:
                driver.switch_to.window(list(new_tabs)[0])
                portal_url = driver.current_url
                job["company_portal_url"] = portal_url
                logger.info(f"   🔗 Company portal opened in new tab: {portal_url[:50]}")
                driver.close()
                driver.switch_to.window(list(original_tabs)[0])
            except Exception:
                pass
            return "manual"

        # Case 2: Redirected away from Naukri → company portal
        if _is_external_redirect(original_url, current_url):
            job["company_portal_url"] = current_url
            logger.info(f"   🔗 Redirected to company portal: {current_url[:50]}")
            try:
                driver.back()
                time.sleep(2)
            except Exception:
                pass
            return "manual"

        # Case 3: Chatbot questionnaire appeared on Naukri
        if _has_chatbot(driver):
            logger.info(f"   🤖 Naukri chatbot detected — answering questions...")
            success = _answer_chatbot(driver, job, config)
            if success:
                return "applied"
            else:
                logger.warning(f"   Could not complete chatbot — marking as manual")
                job["company_portal_url"] = current_url
                job["note"] = "Naukri chatbot questions — apply manually"
                return "manual"

        # Case 4: Confirmation popup on Naukri
        _handle_popup(driver)
        time.sleep(2)

        # Case 5: Check for success message
        page_source = driver.page_source.lower()
        success_phrases = [
            "successfully applied", "application submitted",
            "applied successfully", "thank you for applying",
            "your application has been", "application received",
            "you have applied"
        ]
        if any(p in page_source for p in success_phrases):
            return "applied"

        # Check if button now shows "Applied"
        try:
            refreshed_btn = _find_apply_button(driver)
            if refreshed_btn and "applied" in (refreshed_btn.text or "").lower():
                return "applied"
        except Exception:
            pass

        # Still on Naukri with no error → applied
        if "naukri.com" in current_url and "login" not in current_url:
            return "applied"

        return "failed"

    except Exception as e:
        logger.error(f"   Error: {e}")
        return "failed"


def _find_apply_button(driver):
    for by, loc in [
        (By.XPATH, '//button[contains(text(),"Apply")]'),
        (By.XPATH, '//a[contains(text(),"Apply")]'),
        (By.CSS_SELECTOR, 'button.apply-button'),
        (By.CSS_SELECTOR, '#apply-button'),
        (By.XPATH, '//button[contains(text(),"Easy Apply")]'),
        (By.XPATH, '//button[contains(@class,"apply")]'),
    ]:
        try:
            return WebDriverWait(driver, 5).until(
                EC.element_to_be_clickable((by, loc)))
        except Exception:
            continue
    return None


def _has_chatbot(driver) -> bool:
    """Check if Naukri's chatbot questionnaire is visible."""
    try:
        chatbot_signals = [
            '[class*="chatbot"]',
            '[class*="chat-bot"]',
            '[class*="questionnaire"]',
            'div[class*="chat"] input[type="text"]',
            'div[class*="chat"] textarea',
            '.chatbot-container',
            '[placeholder*="Type here"]',
            '[placeholder*="type here"]',
        ]
        for sel in chatbot_signals:
            els = driver.find_elements(By.CSS_SELECTOR, sel)
            if els:
                logger.info(f"   Chatbot detected via: {sel}")
                return True

        # Check page text for chatbot phrases
        page_text = driver.page_source.lower()
        chatbot_phrases = [
            "kindly answer all the recruiter",
            "answer all the questions",
            "recruiter's questions",
            "type here...",
            "which tech stack",
            "current ctc",
            "expected ctc",
            "notice period",
        ]
        for phrase in chatbot_phrases:
            if phrase in page_text:
                logger.info(f"   Chatbot detected via phrase: '{phrase}'")
                return True

        return False
    except Exception:
        return False


def _answer_chatbot(driver, job: dict, config: dict) -> bool:
    """
    Answer Naukri chatbot questions using profile data + AI.
    Returns True if completed successfully.
    """
    try:
        profile  = config["profile"]
        max_rounds = 10  # Max question rounds to answer

        for round_num in range(max_rounds):
            time.sleep(2)

            # Get current question text
            question = _get_current_question(driver)
            if not question:
                logger.info(f"   No more questions (round {round_num})")
                break

            logger.info(f"   Q{round_num+1}: {question[:80]}")

            # Generate answer
            answer = _generate_answer(question, profile, config, job)
            logger.info(f"   A{round_num+1}: {answer[:60]}")

            # Type answer into input field
            answered = _type_answer(driver, answer)
            if not answered:
                logger.warning(f"   Could not find input for question {round_num+1}")
                break

            time.sleep(1)

            # Click Save/Next/Send button
            submitted = _click_save(driver)
            if not submitted:
                logger.warning(f"   Could not find Save button for question {round_num+1}")
                break

            time.sleep(2)

            # Check if application completed
            page_text = driver.page_source.lower()
            if any(p in page_text for p in [
                "successfully applied", "application submitted",
                "thank you", "applied successfully"
            ]):
                logger.info(f"   ✅ Chatbot completed — application submitted!")
                return True

        # Final check
        page_text = driver.page_source.lower()
        return any(p in page_text for p in [
            "successfully applied", "application submitted",
            "thank you", "applied"
        ])

    except Exception as e:
        logger.error(f"   Chatbot error: {e}")
        return False


def _get_current_question(driver) -> str:
    """Get the latest question text from chatbot."""
    try:
        question_sels = [
            '[class*="chat"] [class*="message"]:last-child',
            '[class*="chatbot"] p:last-of-type',
            '[class*="question"]:last-child',
            '.chatbot-container p',
        ]
        for sel in question_sels:
            try:
                els = driver.find_elements(By.CSS_SELECTOR, sel)
                if els:
                    text = els[-1].text.strip()
                    if text and len(text) > 5:
                        return text
            except Exception:
                continue

        # Fallback: get all visible text in chat area
        try:
            chat_area = driver.find_element(By.CSS_SELECTOR,
                '[class*="chat"], [class*="questionnaire"]')
            return chat_area.text.strip()[-200:]  # Last 200 chars
        except Exception:
            pass

    except Exception:
        pass
    return ""


def _generate_answer(question: str, profile: dict, config: dict, job: dict) -> str:
    """Generate appropriate answer for the question."""
    q = question.lower()

    # ── Rule-based answers for common questions ───────────────────
    if any(w in q for w in ["current ctc", "current salary", "current package"]):
        return f"{profile.get('expected_salary_lpa', 5) - 1} LPA"

    if any(w in q for w in ["expected ctc", "expected salary", "expected package"]):
        return f"{profile.get('expected_salary_lpa', 6)} LPA"

    if any(w in q for w in ["notice period", "joining", "how soon"]):
        days = profile.get("notice_period_days", 30)
        return f"{days} days" if days > 0 else "Immediate"

    if any(w in q for w in ["experience", "years of experience", "how many years"]):
        return f"{profile.get('experience_years', 2)} years"

    if any(w in q for w in ["location", "relocate", "work from", "office"]):
        return profile.get("current_location", "Bangalore")

    if any(w in q for w in ["current company", "working at", "employer"]):
        return "Currently looking for new opportunities"

    if any(w in q for w in ["reason for change", "why looking", "why leaving"]):
        return "Looking for better growth opportunities and to work on challenging projects"

    if any(w in q for w in ["tech stack", "technologies", "skills", "familiar with"]):
        skills = config["job_search"]["skills"]
        return f"I am proficient in {', '.join(skills[:6])}. I have hands-on experience building REST APIs and microservices."

    if any(w in q for w in ["project", "built", "developed", "worked on"]):
        return (f"I have worked on microservices-based applications using Java and SpringBoot, "
                f"built REST APIs, and integrated Kafka for event-driven architecture.")

    if any(w in q for w in ["available", "when can you", "start date"]):
        return "I can join within 30 days"

    # ── AI fallback for complex questions ─────────────────────────
    api_key = os.environ.get("GEMINI_API_KEY", "")
    if api_key:
        return _ai_answer(api_key, question, profile, config)

    # Generic fallback
    return f"I have {profile.get('experience_years', 2)} years of experience with relevant skills in {', '.join(config['job_search']['skills'][:4])}."


def _ai_answer(api_key: str, question: str, profile: dict, config: dict) -> str:
    """Use Gemini to answer complex questions."""
    try:
        prompt = f"""You are answering a job application question on behalf of a candidate.

CANDIDATE PROFILE:
- Experience: {profile.get('experience_years', 2)} years
- Location: {profile.get('current_location', 'Bangalore')}
- Skills: {', '.join(config['job_search']['skills'])}
- Expected Salary: {profile.get('expected_salary_lpa', 6)} LPA
- Notice Period: {profile.get('notice_period_days', 30)} days

QUESTION: {question}

Answer in 1-2 sentences, professionally and concisely. Be specific."""

        payload = json.dumps({
            "contents": [{"parts": [{"text": prompt}]}],
            "generationConfig": {"maxOutputTokens": 100, "temperature": 0.3}
        }).encode("utf-8")

        req = urllib.request.Request(
            f"{GEMINI_URL}?key={api_key}",
            data=payload,
            headers={"Content-Type": "application/json"},
            method="POST"
        )
        with urllib.request.urlopen(req, timeout=10) as resp:
            data = json.loads(resp.read().decode("utf-8"))
            return data["candidates"][0]["content"]["parts"][0]["text"].strip()
    except Exception as e:
        logger.warning(f"   AI answer failed: {e}")
        return f"I have {profile.get('experience_years', 2)} years of relevant experience and am excited about this opportunity."


def _type_answer(driver, answer: str) -> bool:
    """Type answer into the chatbot input field."""
    input_sels = [
        'input[placeholder*="Type here"]',
        'input[placeholder*="type here"]',
        'textarea[placeholder*="Type here"]',
        'textarea[placeholder*="type here"]',
        '[class*="chat"] input[type="text"]',
        '[class*="chat"] textarea',
        '[class*="chatbot"] input',
        '[class*="chatbot"] textarea',
        'input[type="text"]:last-of-type',
        'textarea:last-of-type',
    ]
    for sel in input_sels:
        try:
            inp = WebDriverWait(driver, 5).until(
                EC.element_to_be_clickable((By.CSS_SELECTOR, sel)))
            inp.clear()
            inp.send_keys(answer)
            return True
        except Exception:
            continue
    return False


def _click_save(driver) -> bool:
    """Click the Save/Next/Send button."""
    for by, loc in [
        (By.XPATH, '//button[contains(text(),"Save")]'),
        (By.XPATH, '//button[contains(text(),"Next")]'),
        (By.XPATH, '//button[contains(text(),"Send")]'),
        (By.XPATH, '//button[contains(text(),"Submit")]'),
        (By.CSS_SELECTOR, 'button[type="submit"]'),
        (By.XPATH, '//button[@class and contains(@class,"send")]'),
    ]:
        try:
            btn = WebDriverWait(driver, 5).until(
                EC.element_to_be_clickable((by, loc)))
            driver.execute_script("arguments[0].click();", btn)
            return True
        except Exception:
            continue
    return False


def _is_external_redirect(original_url: str, current_url: str) -> bool:
    if "naukri.com" not in current_url:
        return True
    for domain in EXTERNAL_DOMAINS:
        if domain in current_url:
            return True
    return False


def _handle_popup(driver):
    for by, loc in [
        (By.XPATH, '//button[contains(text(),"Apply")]'),
        (By.XPATH, '//button[contains(text(),"Confirm")]'),
        (By.XPATH, '//button[contains(text(),"Submit")]'),
    ]:
        try:
            btn = WebDriverWait(driver, 3).until(
                EC.element_to_be_clickable((by, loc)))
            driver.execute_script("arguments[0].click();", btn)
            time.sleep(2)
            break
        except Exception:
            continue
