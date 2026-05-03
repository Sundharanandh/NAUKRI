"""
browser.py — Handles all Naukri.com browser automation
Phase 1: Login + Daily Profile Update
"""

import time
import logging
from datetime import datetime, timezone
from selenium import webdriver
from selenium.webdriver.common.by import By
from selenium.webdriver.common.keys import Keys
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC
from selenium.webdriver.chrome.options import Options
from selenium.common.exceptions import TimeoutException, NoSuchElementException

logger = logging.getLogger(__name__)


class NaukriBrowser:
    NAUKRI_LOGIN   = "https://www.naukri.com/nlogin/login"
    NAUKRI_PROFILE = "https://www.naukri.com/mnjuser/profile"

    def __init__(self, config: dict, headless: bool = True):
        self.config   = config
        self.email    = config["naukri_email"]
        self.password = config["naukri_password"]
        self.driver   = self._init_driver(headless)
        self.wait     = WebDriverWait(self.driver, 15)

    # ------------------------------------------------------------------ #
    #  Driver Setup                                                        #
    # ------------------------------------------------------------------ #
    def _init_driver(self, headless: bool):
        options = Options()
        if headless:
            options.add_argument("--headless=new")
        options.add_argument("--no-sandbox")
        options.add_argument("--disable-dev-shm-usage")
        options.add_argument("--disable-blink-features=AutomationControlled")
        options.add_experimental_option("excludeSwitches", ["enable-automation"])
        options.add_experimental_option("useAutomationExtension", False)
        options.add_argument(
            "user-agent=Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
            "AppleWebKit/537.36 (KHTML, like Gecko) "
            "Chrome/124.0.0.0 Safari/537.36"
        )
        driver = webdriver.Chrome(options=options)
        driver.execute_script(
            "Object.defineProperty(navigator, 'webdriver', {get: () => undefined})"
        )
        driver.maximize_window()
        return driver

    # ------------------------------------------------------------------ #
    #  Helper — try multiple locators                                      #
    # ------------------------------------------------------------------ #
    def _find_element_any(self, locators: list, timeout: int = 20):
        for by, value in locators:
            try:
                el = WebDriverWait(self.driver, timeout).until(
                    EC.presence_of_element_located((by, value))
                )
                return el
            except TimeoutException:
                continue
        return None

    def _save_screenshot(self, filename: str):
        try:
            import os
            os.makedirs("logs", exist_ok=True)
            path = f"logs/{filename}"
            self.driver.save_screenshot(path)
            logger.info(f"   📸 Screenshot saved: {path}")
        except Exception:
            pass

    # ------------------------------------------------------------------ #
    #  Login                                                               #
    # ------------------------------------------------------------------ #
    def login(self) -> bool:
        logger.info("🔐 Attempting Naukri login...")
        try:
            self.driver.get(self.NAUKRI_LOGIN)
            logger.info("   Page loading — waiting up to 20 seconds...")
            time.sleep(4)

            # Email field
            email_field = self._find_element_any([
                (By.ID,    "usernameField"),
                (By.ID,    "emailTxt"),
                (By.NAME,  "email"),
                (By.XPATH, '//input[@type="email"]'),
                (By.XPATH, '//input[contains(@placeholder,"Email")]'),
                (By.XPATH, '//input[contains(@placeholder,"email")]'),
                (By.XPATH, '//input[contains(@placeholder,"Username")]'),
            ], timeout=20)

            if not email_field:
                logger.error("❌ Could not find email field.")
                self._save_screenshot("login_email_not_found.png")
                return False

            email_field.clear()
            email_field.send_keys(self.email)
            logger.info("   ✔ Email entered")
            time.sleep(1)

            # Password field
            pwd_field = self._find_element_any([
                (By.ID,    "passwordField"),
                (By.ID,    "pwd1"),
                (By.NAME,  "password"),
                (By.XPATH, '//input[@type="password"]'),
                (By.XPATH, '//input[contains(@placeholder,"password")]'),
                (By.XPATH, '//input[contains(@placeholder,"Password")]'),
            ], timeout=10)

            if not pwd_field:
                logger.error("❌ Could not find password field.")
                self._save_screenshot("login_password_not_found.png")
                return False

            pwd_field.clear()
            pwd_field.send_keys(self.password)
            logger.info("   ✔ Password entered")
            time.sleep(1)

            # Login button
            login_btn = self._find_element_any([
                (By.XPATH, '//*[@type="submit"]'),
                (By.XPATH, '//button[contains(text(),"Login")]'),
                (By.XPATH, '//button[contains(text(),"login")]'),
                (By.XPATH, '//button[contains(text(),"Sign in")]'),
                (By.XPATH, '//*[@value="Login"]'),
                (By.CSS_SELECTOR, 'button[type="submit"]'),
            ], timeout=10)

            if not login_btn:
                logger.error("❌ Could not find login button.")
                self._save_screenshot("login_button_not_found.png")
                return False

            login_btn.click()
            logger.info("   ✔ Login button clicked — waiting for redirect...")
            login_triggered_at = datetime.now(timezone.utc)   # record exact time
            time.sleep(5)

            # ── Check if Naukri is asking for OTP ───────────────────
            if self._is_otp_page():
                logger.info("   📱 Naukri asked for OTP — fetching from Gmail...")
                otp = self._get_otp_from_gmail(login_triggered_at)
                if not otp:
                    logger.error("❌ Could not get OTP from Gmail.")
                    self._save_screenshot("otp_not_found.png")
                    return False
                if not self._enter_otp(otp):
                    logger.error("❌ Failed to enter OTP.")
                    return False
                time.sleep(4)

            current_url = self.driver.current_url
            page_title  = self.driver.title.lower()

            if "login" not in current_url and "naukri.com" in current_url:
                logger.info(f"✅ Login successful! Landed on: {current_url}")
                return True
            elif "naukri" in page_title and "login" not in page_title:
                logger.info("✅ Login successful! (verified via page title)")
                return True
            else:
                logger.error(f"❌ Login failed. Current URL: {current_url}")
                self._save_screenshot("login_failed.png")
                return False

        except Exception as e:
            logger.error(f"❌ Unexpected login error: {e}")
            self._save_screenshot("login_exception.png")
            return False

    def _is_otp_page(self) -> bool:
        """Check if Naukri is showing an OTP verification screen."""
        try:
            page_source = self.driver.page_source.lower()
            otp_signals = ["otp", "one time", "verify", "verification code", "enter code"]
            return any(signal in page_source for signal in otp_signals)
        except Exception:
            return False

    def _get_otp_from_gmail(self, login_triggered_at=None) -> str:
        """Fetch OTP from Gmail — only accepts emails after login was triggered."""
        try:
            from otp_reader import fetch_naukri_otp
            gmail   = self.config["notifications"]["email"]
            app_pwd = self.config["notifications"]["gmail_app_password"]
            return fetch_naukri_otp(gmail, app_pwd,
                                    max_wait_seconds=90,
                                    login_triggered_at=login_triggered_at)
        except Exception as e:
            logger.error(f"   OTP fetch error: {e}")
            return ""

    def _enter_otp(self, otp: str) -> bool:
        """
        Enter OTP into Naukri's verification field.
        Handles both: single input field AND 6 separate boxes.
        """
        try:
            time.sleep(2)  # Let OTP page fully render

            # ── Try 6 separate boxes first (current Naukri UI) ───────
            otp_boxes = self.driver.find_elements(
                By.XPATH, '//input[@type="tel" or @type="number" or @maxlength="1"]'
            )

            if len(otp_boxes) >= 4:
                logger.info(f"   📦 Found {len(otp_boxes)} OTP boxes — entering digit by digit")
                for i, digit in enumerate(otp[:len(otp_boxes)]):
                    otp_boxes[i].clear()
                    otp_boxes[i].send_keys(digit)
                    time.sleep(0.2)
                logger.info(f"   ✔ OTP entered digit by digit: {otp}")

            else:
                # ── Single input field fallback ───────────────────────
                otp_field = self._find_element_any([
                    (By.XPATH, '//input[@type="tel"]'),
                    (By.XPATH, '//input[@type="number"]'),
                    (By.XPATH, '//input[contains(@placeholder,"OTP")]'),
                    (By.XPATH, '//input[contains(@placeholder,"otp")]'),
                    (By.XPATH, '//input[contains(@placeholder,"code")]'),
                    (By.XPATH, '//input[contains(@name,"otp")]'),
                    (By.XPATH, '//input[contains(@id,"otp")]'),
                ], timeout=10)

                if not otp_field:
                    logger.error("   Could not find OTP input field.")
                    self._save_screenshot("otp_field_not_found.png")
                    return False

                otp_field.clear()
                otp_field.send_keys(otp)
                logger.info(f"   ✔ OTP entered in single field: {otp}")

            time.sleep(1)

            # ── Click Verify button ───────────────────────────────────
            verify_btn = self._find_element_any([
                (By.XPATH, '//button[contains(text(),"Verify")]'),
                (By.XPATH, '//button[contains(text(),"Submit")]'),
                (By.XPATH, '//button[contains(text(),"Confirm")]'),
                (By.XPATH, '//*[@type="submit"]'),
            ], timeout=5)

            if verify_btn:
                self.driver.execute_script("arguments[0].click();", verify_btn)
                logger.info("   ✔ OTP submitted — waiting for login...")
                time.sleep(5)  # Wait longer after OTP submission
                return True

            logger.error("   Could not find Verify button")
            return False

        except Exception as e:
            logger.error(f"   OTP entry error: {e}")
            self._save_screenshot("otp_entry_error.png")
            return False

    # ------------------------------------------------------------------ #
    #  Daily Profile Update                                                #
    # ------------------------------------------------------------------ #
    def update_profile_timestamp(self) -> bool:
        logger.info("🔄 Updating profile timestamp...")
        try:
            self.driver.get(self.NAUKRI_PROFILE)
            time.sleep(5)

            headline_edit_locators = [
                (By.XPATH, '//div[@id="lazyResumeHead"]//span[contains(@class,"edit")]'),
                (By.XPATH, '//div[@id="lazyResumeHead"]//span[@class="edit icon"]'),
                (By.XPATH, '//*[contains(@class,"resumeHeadline")]//*[contains(@class,"edit")]'),
                (By.XPATH, '//*[contains(@class,"headline")]//*[contains(@class,"edit")]'),
                (By.XPATH, '//section[contains(@id,"resumeHeadline")]//span[contains(@class,"edit")]'),
                (By.XPATH, '(//span[contains(@class,"edit icon")])[1]'),
                (By.XPATH, '(//span[@class="edit icon"])[1]'),
            ]

            edit_clicked = False
            for by, locator in headline_edit_locators:
                try:
                    el = WebDriverWait(self.driver, 5).until(
                        EC.element_to_be_clickable((by, locator))
                    )
                    self.driver.execute_script("arguments[0].scrollIntoView(true);", el)
                    time.sleep(0.5)
                    self.driver.execute_script("arguments[0].click();", el)
                    logger.info(f"   ✔ Headline edit clicked")
                    edit_clicked = True
                    time.sleep(2)
                    break
                except Exception:
                    continue

            if not edit_clicked:
                logger.warning("   ⚠️  Headline edit not found — trying resume upload fallback...")
                return self._upload_resume_fallback()

            textarea_locators = [
                (By.ID,    "resumeHeadlineTxt"),
                (By.XPATH, '//textarea[@id="resumeHeadlineTxt"]'),
                (By.XPATH, '//textarea[contains(@placeholder,"headline")]'),
                (By.XPATH, '//textarea[contains(@placeholder,"Headline")]'),
                (By.XPATH, '//div[contains(@class,"editHeadline")]//textarea'),
            ]

            textarea = None
            for by, locator in textarea_locators:
                try:
                    textarea = WebDriverWait(self.driver, 8).until(
                        EC.presence_of_element_located((by, locator))
                    )
                    break
                except Exception:
                    continue

            if not textarea:
                logger.warning("   ⚠️  Textarea not found.")
                self._save_screenshot("profile_textarea_not_found.png")
                return False

            current_text = textarea.get_attribute("value").strip()
            textarea.send_keys(Keys.CONTROL + Keys.END)
            if current_text.endswith(" "):
                textarea.send_keys(Keys.BACKSPACE)
            else:
                textarea.send_keys(" ")
            time.sleep(1)

            save_locators = [
                (By.XPATH, '//button[@type="submit" and contains(@class,"btn-dark-ot")]'),
                (By.XPATH, '//button[contains(text(),"Save")]'),
                (By.XPATH, '//button[text()="Save"]'),
                (By.CSS_SELECTOR, 'button[type="submit"]'),
            ]

            for by, locator in save_locators:
                try:
                    save_btn = WebDriverWait(self.driver, 5).until(
                        EC.element_to_be_clickable((by, locator))
                    )
                    self.driver.execute_script("arguments[0].click();", save_btn)
                    logger.info("   ✔ Save clicked")
                    break
                except Exception:
                    continue

            time.sleep(3)
            logger.info("✅ Profile timestamp refreshed!")
            return True

        except Exception as e:
            logger.error(f"❌ Profile update error: {e}")
            self._save_screenshot("profile_update_error.png")
            return False

    def _upload_resume_fallback(self) -> bool:
        import os
        resume_path = self.config.get("resume_path", "resume.pdf")
        abs_path    = os.path.abspath(resume_path)
        if not os.path.exists(abs_path):
            logger.warning(f"   ⚠️  Resume not found at {abs_path}")
            return False
        try:
            for by, locator in [
                (By.ID,    "attachCV"),
                (By.XPATH, '//input[@id="attachCV"]'),
                (By.XPATH, '//input[@type="file"]'),
            ]:
                try:
                    el = WebDriverWait(self.driver, 10).until(
                        EC.presence_of_element_located((by, locator))
                    )
                    el.send_keys(abs_path)
                    time.sleep(5)
                    logger.info("✅ Resume re-uploaded — timestamp refreshed!")
                    return True
                except Exception:
                    continue
            return False
        except Exception as e:
            logger.error(f"❌ Upload fallback failed: {e}")
            return False

    # ------------------------------------------------------------------ #
    #  Cleanup                                                             #
    # ------------------------------------------------------------------ #
    def close(self):
        try:
            self.driver.quit()
            logger.info("🔒 Browser closed.")
        except Exception:
            pass
