# 🤖 Naukri AI Agent — Complete Setup Guide

Runs every day at **9:00 AM IST** automatically on GitHub cloud.
No laptop needed after setup. You only need your phone to check results.

---

## 📁 Project Files

```
naukri-agent/
├── .github/
│   └── workflows/
│       └── naukri_agent.yml   ← GitHub Actions schedule
├── agent_cloud.py             ← Main runner (all 3 phases)
├── browser.py                 ← Naukri login & profile update
├── job_search.py              ← Search jobs on Naukri
├── job_matcher.py             ← AI scoring using Claude
├── job_apply.py               ← Auto-apply logic
├── notifier.py                ← Daily email report
├── resume_parser.py           ← Read your PDF resume
├── encode_resume.py           ← Run once to encode PDF
├── requirements.txt           ← Python packages
├── config.json                ← Placeholders only (real values go in GitHub Secrets)
└── .gitignore                 ← Keeps your secrets off GitHub
```

---

## 🗓️ How Phases Activate (Automatic)

| Days Since Start | Phase | What Happens |
|---|---|---|
| Days 1–7   | Phase 1 | Login + Update Naukri profile daily |
| Days 8–14  | Phase 2 | + Search jobs + AI match vs your resume (emailed to you) |
| Day 15+    | Phase 3 | + Auto-apply to top 15 matched jobs per day |

---

## 🔑 STEP 1 — Get an Anthropic API Key (FREE)

You need this for AI job matching (Phase 2).

1. Go to https://console.anthropic.com
2. Sign up for a free account
3. Click **API Keys** → **Create Key**
4. Copy and save the key (starts with `sk-ant-...`)

---

## 📄 STEP 2 — Encode Your Resume PDF

Run this once on your PC before pushing to GitHub:

```cmd
python encode_resume.py
```

This creates `resume_base64.txt` — keep it open, you'll need it in Step 5.

---

## 📂 STEP 3 — Create a Private GitHub Repository

1. Go to https://github.com → Sign in
2. Click **"+"** (top right) → **"New repository"**
3. Name: `naukri-agent`
4. Set to **Private** ← IMPORTANT
5. Click **"Create repository"**

---

## 💻 STEP 4 — Push Code to GitHub

Open Command Prompt in your project folder:

```cmd
git init
git config --global user.email "your@email.com"
git config --global user.name "Your Name"
git add .
git commit -m "Naukri Agent - All Phases"
git branch -M main
git remote add origin https://github.com/YOUR_USERNAME/naukri-agent.git
git push -u origin main
```

When asked for password → use your **GitHub Personal Access Token**:
- GitHub → Profile → Settings → Developer settings
- Personal access tokens → Tokens (classic) → Generate new token
- Check `repo` → Generate → Copy token → paste as password

---

## 🔐 STEP 5 — Add Secrets to GitHub

Go to your repo → **Settings** → **Secrets and variables** → **Actions**

Click **"New repository secret"** and add all 6:

| Secret Name | What to put |
|---|---|
| `NAUKRI_EMAIL` | Your Naukri.com login email |
| `NAUKRI_PASSWORD` | Your Naukri.com password |
| `NOTIFY_EMAIL` | Gmail address for daily reports |
| `GMAIL_APP_PASSWORD` | 16-char Gmail App Password (see below) |
| `RESUME_PDF_BASE64` | Full contents of `resume_base64.txt` |
| `ANTHROPIC_API_KEY` | Your key from console.anthropic.com |

### How to get Gmail App Password:
1. Go to myaccount.google.com → Security
2. Enable **2-Step Verification**
3. Search **"App Passwords"**
4. Create one → App: Mail → Device: Windows
5. Copy the 16-character password shown

---

## ▶️ STEP 6 — Test It Manually

1. Go to your GitHub repo → **Actions** tab
2. Click **"Naukri Daily Agent 🤖"**
3. Click **"Run workflow"** → **"Run workflow"**
4. Watch it run — takes 2–3 minutes
5. ✅ Green = working!

---

## 📱 Checking Results From Your Phone

After setup, you only need your phone:

**Option 1 — Email:** Get daily report at 9 AM IST automatically.

**Option 2 — GitHub App:**
- Install "GitHub" app on your phone
- Go to your repo → Actions tab
- See ✅ or ❌ for each day's run
- Tap any run → download logs artifact

---

## 📧 What Your Daily Email Looks Like

```
NAUKRI AGENT — DAILY REPORT
Saturday, 05 April 2026

── PHASE 1: Login & Profile ─────────
  Login          : ✅
  Profile Update : ✅
  Resume Loaded  : ✅

── PHASE 2: Job Search & AI Match ───
  Jobs Found     : 47
  Good Matches   : 8 (score ≥ 70/100)

  Matched Jobs:
  [ 92/100] Python Developer @ TCS Chennai
             Reason: Strong Python/Django match, exp aligns
             Link: https://naukri.com/...

  [ 85/100] Backend Engineer @ Infosys
             ...

── PHASE 3: Auto Apply ──────────────
  Applied Today  : 8 jobs
  ✅ Python Developer @ TCS
  ✅ Backend Engineer @ Infosys
  ...
```

---

## 🔄 If You Need to Update Anything Later

From any PC (not just your laptop):

```cmd
git add .
git commit -m "Update config"
git push
```

---

## ❓ Troubleshooting

| Problem | Fix |
|---|---|
| Action fails on Chrome install | Re-run workflow — temporary GitHub issue |
| Login fails | Check `NAUKRI_EMAIL` and `NAUKRI_PASSWORD` secrets |
| No email received | Check `GMAIL_APP_PASSWORD`, check spam folder |
| AI matching not working | Check `ANTHROPIC_API_KEY` secret |
| Resume not loading | Check `RESUME_PDF_BASE64` secret (re-run encode_resume.py) |
| git push rejected | Run `git pull origin main --rebase` then push again |

---

## 🛡️ Security Reminders

- ✅ Repository is **Private** — only you can see it
- ✅ All passwords stored as **GitHub Secrets** — encrypted
- ✅ `.gitignore` prevents `config.json` and PDFs from being committed
- ❌ Never paste your real passwords into `config.json`
- ❌ Never make the repository Public
