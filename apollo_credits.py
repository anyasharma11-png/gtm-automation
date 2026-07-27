"""
apollo_credits.py

A local credit-spend ledger for Apollo API calls. Apollo doesn't expose a
"credits remaining" endpoint on most plans, so this tracks spend ourselves
in a local JSON file and refuses to spend past limits YOU set.

Ledger file: apollo_usage.json (git-ignored -- local only, not shared).

Limits are read from .env:
  APOLLO_DAILY_CREDIT_LIMIT   (default 10)
  APOLLO_MONTHLY_CREDIT_LIMIT (default 50 -- matches a typical trial's total)
"""

import os
import json
import datetime

LEDGER_FILE = "apollo_usage.json"

DAILY_LIMIT = int(os.getenv("APOLLO_DAILY_CREDIT_LIMIT", "10"))
MONTHLY_LIMIT = int(os.getenv("APOLLO_MONTHLY_CREDIT_LIMIT", "50"))


def _load_ledger():
    if not os.path.exists(LEDGER_FILE):
        return {}
    with open(LEDGER_FILE, "r") as f:
        try:
            return json.load(f)
        except json.JSONDecodeError:
            return {}


def _save_ledger(ledger):
    with open(LEDGER_FILE, "w") as f:
        json.dump(ledger, f, indent=2)


def _today_str():
    return datetime.date.today().isoformat()


def _month_str():
    return datetime.date.today().strftime("%Y-%m")


def get_spent_today():
    ledger = _load_ledger()
    return ledger.get(_today_str(), 0)


def get_spent_this_month():
    ledger = _load_ledger()
    month = _month_str()
    return sum(v for k, v in ledger.items() if k.startswith(month))


def check_budget(credits_needed=1):
    """
    Raises RuntimeError if spending `credits_needed` more would exceed
    either the daily or monthly limit. Call this BEFORE making an Apollo
    call that will spend credits.
    """
    spent_today = get_spent_today()
    spent_month = get_spent_this_month()

    if spent_today + credits_needed > DAILY_LIMIT:
        raise RuntimeError(
            f"Apollo daily credit limit would be exceeded: {spent_today} spent today, "
            f"limit is {DAILY_LIMIT}. Raise APOLLO_DAILY_CREDIT_LIMIT in .env if this is intentional."
        )
    if spent_month + credits_needed > MONTHLY_LIMIT:
        raise RuntimeError(
            f"Apollo monthly credit limit would be exceeded: {spent_month} spent this month, "
            f"limit is {MONTHLY_LIMIT}. Raise APOLLO_MONTHLY_CREDIT_LIMIT in .env if this is intentional."
        )


def record_spend(credits_used=1):
    """Call this AFTER a successful Apollo call that spent credits."""
    ledger = _load_ledger()
    today = _today_str()
    ledger[today] = ledger.get(today, 0) + credits_used
    _save_ledger(ledger)


def print_status():
    print(f"Apollo credits: {get_spent_today()}/{DAILY_LIMIT} spent today, "
          f"{get_spent_this_month()}/{MONTHLY_LIMIT} spent this month.")
