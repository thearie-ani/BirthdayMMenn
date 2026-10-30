# BirthdayMMenn (BirthdayMate) 🎂🤖

An AI-powered Telegram birthday assistant that helps you remember birthdays, receive automated notifications, and generate thoughtful birthday wishes.

---

## 🛠️ Technology Stack

- **Language:** Python 3.10+ (tested on Python 3.14)
- **Telegram Framework:** [`python-telegram-bot`](https://github.com/python-telegram-bot/python-telegram-bot)
- **Database & ORM:** SQLite with [SQLAlchemy](https://www.sqlalchemy.org/) (`aiosqlite` for asynchronous support)
- **Task Scheduling:** [APScheduler](https://apscheduler.readthedocs.io/)
- **Configuration Management:** [python-dotenv](https://github.com/theskumar/python-dotenv)
- **Testing:** [pytest](https://docs.pytest.org/)
- **Version Control:** Git

---

## 📁 Project Structure

```text
BDTelegramBot/
├── .env.example         # Template for environment variables (safe to commit)
├── .gitignore            # Git ignore configuration (.env, .venv, cache, SQLite DB)
├── README.md             # Project documentation and setup instructions
├── requirements.txt      # Project dependencies
└── app/
    ├── main.py           # Application entry point
    ├── ai/               # AI wish generation prompts and clients
    ├── bot/              # Telegram bot handlers, keyboards, and states
    │   ├── handlers/     # Command and message handlers
    │   ├── keyboards/    # Interactive reply and inline keyboards
    │   └── states/       # Conversation states for bot flows
    ├── config/           # Application configuration and settings loader
    ├── db/               # Database connection, models, and repositories
    │   ├── models/       # SQLAlchemy database models
    │   └── repositories/ # Data access layer
    ├── scheduler/        # APScheduler jobs for birthday notifications
    ├── services/         # Core business logic services
    ├── tests/            # Automated test suite (pytest)
    └── utils/            # Helper utilities (dates, timezone, validation)
```

---

## 🚀 Getting Started

Follow these step-by-step instructions to set up the project on your machine.

### 1. Prerequisites

- Python 3.10 or higher installed on your computer.
- Git installed.

### 2. Create and Activate a Virtual Environment

Open a terminal (such as PowerShell on Windows) in the project directory:

```powershell
# Create a virtual environment named .venv
python -m venv .venv

# Activate the virtual environment (Windows PowerShell)
.\.venv\Scripts\Activate.ps1

# (Alternative for macOS/Linux)
# source .venv/bin/activate
```

> **Tip for Windows PowerShell users:** If script execution is restricted, run:  
> `Set-ExecutionPolicy -ExecutionPolicy RemoteSigned -Scope Process`

### 3. Install Dependencies

Install the required Python packages:

```bash
pip install -r requirements.txt
```

### 4. Configure Environment Variables

Create your local `.env` configuration file from the template:

```powershell
# Windows PowerShell
Copy-Item .env.example .env

# macOS / Linux
# cp .env.example .env
```

Open `.env` and fill in your configuration:
- `TELEGRAM_BOT_TOKEN`: Your Telegram bot token from [@BotFather](https://t.me/BotFather).
- `DATABASE_URL`: The SQLite database connection string (default: `sqlite+aiosqlite:///birthday_mate.db`).

> ⚠️ **Security Notice:** Never commit your `.env` file to Git. It is excluded by `.gitignore` to protect your tokens.

### 5. Verify the Setup

You can verify your Python environment and packages by checking installed versions:

```powershell
python --version
pip list
```

---

## 📌 Milestones Roadmap

- [x] **Milestone 1:** Project foundation (Configuration, `.gitignore`, `.env.example`, dependencies, documentation)
- [ ] **Milestone 2:** Telegram bot basic setup & commands (`/start`, menus, navigation)
- [ ] **Milestone 3:** Database models & repository layer (SQLite with SQLAlchemy)
- [ ] **Milestone 4:** Friend & birthday management logic
- [ ] **Milestone 5:** Scheduler & automated reminder notifications (APScheduler)
- [ ] **Milestone 6:** AI-powered personalized birthday wish generator
- [ ] **Milestone 7:** Testing, error handling & deployment

---

## 🤖 AI-Assisted Development Journey

BirthdayMate was built not only as a practical Telegram bot, but also as a hands-on learning journey where I explored AI-assisted software development using **Google Antigravity** as my AI coding partner.

As a student, my goal was to use AI as an interactive learning and development tool rather than simply having an assistant generate code without understanding it. Throughout the project, I remained actively involved in directing requirements, reviewing changes, running tests, and understanding how each component works together.

### My Development Approach

I followed an iterative development cycle for each stage of the project:

```text
Explore → Plan → Understand → Build → Test → Review → Improve
```

### How I Used Google Antigravity Throughout the Project:

- **Idea Exploration & MVP Planning:** Discussing feature ideas, refining requirements, and breaking the project into manageable milestones from MVP to production readiness.
- **System Architecture & Database Design:** Exploring relational database schemas with SQLite and SQLAlchemy, establishing foreign key constraints, and designing a clean service/repository layer.
- **Learning Technologies in Context:** Using Antigravity to explore asynchronous Python (`asyncio`), `python-telegram-bot` conversation flows, APScheduler background jobs, and the Google Gemini API.
- **Collaborative Implementation:** Prompting Antigravity for feature implementations, studying the generated code, and understanding the logic behind different modules before integrating them.
- **Testing, Debugging & Error Resolution:** Building automated test suites with `pytest` (growing the test suite to 313 passing tests), reproducing bugs, and learning how to diagnose and resolve runtime errors.
- **Security & Reliability Hardening:** Identifying edge cases, validating Telegram callback payloads, implementing error handling with secret redaction, and designing resilient model fallback for the AI client.
- **Containerization & Deployment Preparation:** Learning Docker and Docker Compose fundamentals, configuring persistent storage volumes for SQLite, implementing container health checks, and designing automated database backup routines.

This project gave me valuable practical experience in modern software development workflows—showing how human oversight, systematic testing, and AI assistance can work together effectively.

