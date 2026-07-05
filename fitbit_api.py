"""
Standalone Google Health API extractor — SHARED DEVICE VERSION

Assumes ONE physical Fitbit + ONE Google Health account (so ONE auth token in config/tokens/google_health_client.json). 
The device is, physically handed to a different study participant for each overnight session.

What makes data "belong" to a participant is the DATE RANGE they had the device,
which must be recorded in config/schedule.csv. 

This script reads that schedule, pulls data for each participant's exact window, and writes to an output directory per participant.

SETUP (one-time):
    1. Google Cloud project with Google Health API enabled.
    2. OAuth 2.0 Client ID, "Web application" type.
    3. Add BOTH of these under Authorized redirect URIs:
        -   https://www.google.com   (kept as fallback)
        -   http://localhost:8765    (used by this script — must match
            AUTH_PORT below exactly, including the port number)
    4. Download the Client ID JSON, save as
        config/google_health_client.json (gitignored — has your secret).
    5. On the OAuth consent screen's Audience page, add the ONE Google
        account email under "Test
        users" before running this the first time.

config/schedule.csv — maintain this yourself, one row per participant
    EXAMPLE:

        participant_id,start_date,end_date
        p_01,2026-06-01,2026-06-07
        p_02,2026-06-08,2026-06-14
        p_03,2026-06-15,2026-06-21

    Note:   p_** is an example of naming convention for participant IDs. Use whatever is appropriate for your study.
            Dates are inclusive, YYYY-MM-DD. Must keep periods non-overlapping.

ENV SETUP (or use whatever Python 3 env to install requests):

*Conda env:
    conda create -n api_extract python=3.11
    conda activate api_extract
    conda install requests 

*Python venv:
    python -m venv api_extract
    source api_extract/bin/activate
    pip install requests

HOW TO USE (in VSCode terminal):

    python fitbit_api.py --participant test                                 # first-run test
    python fitbit_api.py --all                                  # <-- pull every row in schedule.csv
    python fitbit_api.py --participant p_01                     # <-- pull just one participant's window
    python fitbit_api.py --start-date 2026-06-01 --end-date 2026-06-07 --participant p_01   # <-- manual override, ignores schedule.csv

First run ever: opens the browser for the ONE shared Google account to
sign in and approve access, then saves the token to
config/tokens/google_health_shared.json. Every later run is silent —
refreshes automatically, no browser step, regardless of which
participant's window you're pulling.

OUTPUT:
    output/<participant_id>_<start_date>_<end_date>/steps.csv
    output/<participant_id>_<start_date>_<end_date>/distance.csv
    output/<participant_id>_<start_date>_<end_date>/sleep.csv
    output/<participant_id>_<start_date>_<end_date>/profile.csv
"""

import argparse
import csv
import json
import threading
import webbrowser
from datetime import datetime, timedelta
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path
from urllib.parse import urlencode, urlparse, parse_qs

import requests

# ============================================================================
# Config

CLIENT_SECRETS_FILE = Path("config/google_health_client.json")
TOKEN_FILE = Path("config/tokens/google_health_shared.json")  # ONE token, no participant name
SCHEDULE_FILE = Path("config/schedule.csv")
OUTPUT_DIR = Path("output")

AUTH_PORT = 8765
REDIRECT_URI = f"http://localhost:{AUTH_PORT}"  # must EXACTLY match a registered redirect URI
AUTH_ENDPOINT = "https://accounts.google.com/o/oauth2/v2/auth"
TOKEN_ENDPOINT = "https://oauth2.googleapis.com/token"
BASE_URL = "https://health.googleapis.com/v4"

SCOPES = [
    "https://www.googleapis.com/auth/googlehealth.activity_and_fitness.readonly",  # steps, distance
    "https://www.googleapis.com/auth/googlehealth.health_metrics_and_measurements.readonly",  # heart-rate
    "https://www.googleapis.com/auth/googlehealth.sleep.readonly",
    "https://www.googleapis.com/auth/googlehealth.profile.readonly",
]

DATA_TYPES = ["steps", "heart-rate", "sleep", "distance"]  # profile is handled separately below, it's not a time-series dataType


def _load_client_secrets():
    raw = json.loads(CLIENT_SECRETS_FILE.read_text())
    web = raw["web"]
    return web["client_id"], web["client_secret"]


# ============================================================================
# Local server: catches the redirect, grabs ?code=..., then shuts itself down


class _CallbackHandler(BaseHTTPRequestHandler):
    code = None  # class-level, set once the redirect arrives

    def do_GET(self):
        params = parse_qs(urlparse(self.path).query)
        _CallbackHandler.code = params.get("code", [None])[0]

        self.send_response(200)
        self.send_header("Content-type", "text/html")
        self.end_headers()
        if _CallbackHandler.code:
            self.wfile.write(b"<html><body>Authorized. You can close this tab.</body></html>")
        else:
            error = params.get("error", ["unknown error"])[0]
            self.wfile.write(f"<html><body>Authorization failed: {error}</body></html>".encode())

    def log_message(self, format, *args):
        pass  # suppress default request logging to stdout


def _wait_for_code() -> str:
    server = HTTPServer(("localhost", AUTH_PORT), _CallbackHandler)
    thread = threading.Thread(target=server.handle_request)  # serves exactly ONE request, then stops
    thread.start()
    thread.join(timeout=180)  # 3 min to complete the browser approval

    if _CallbackHandler.code is None:
        raise TimeoutError(
            "No redirect received within 3 minutes — did you approve in "
            "the browser, or close the tab without approving?"
        )
    return _CallbackHandler.code


# ============================================================================
# Auth: one-time interactive grant for the shared account, then silent refresh

def get_access_token() -> str:
    if TOKEN_FILE.exists():
        tokens = json.loads(TOKEN_FILE.read_text())
        return _refresh_access_token(tokens)
    return _run_interactive_auth()


def _run_interactive_auth() -> str:
    client_id, client_secret = _load_client_secrets()

    params = {
        "client_id": client_id,
        "redirect_uri": REDIRECT_URI,
        "response_type": "code",
        "scope": " ".join(SCOPES),
        "access_type": "offline",   # required to get a refresh_token back
        "prompt": "consent",
    }
    auth_url = f"{AUTH_ENDPOINT}?{urlencode(params)}"

    print("Opening browser to sign in to the shared study Google account and approve access...")
    webbrowser.open(auth_url)

    code = _wait_for_code()

    resp = requests.post(TOKEN_ENDPOINT, data={
        "client_id": client_id,
        "client_secret": client_secret,
        "code": code,
        "grant_type": "authorization_code",
        "redirect_uri": REDIRECT_URI,
    })
    resp.raise_for_status()
    tokens = resp.json()

    TOKEN_FILE.parent.mkdir(parents=True, exist_ok=True)
    TOKEN_FILE.write_text(json.dumps(tokens, indent=2))
    print("✅ Authorized and saved the shared token.")
    return tokens["access_token"]


def _refresh_access_token(tokens: dict) -> str:
    client_id, client_secret = _load_client_secrets()
    resp = requests.post(TOKEN_ENDPOINT, data={
        "client_id": client_id,
        "client_secret": client_secret,
        "refresh_token": tokens["refresh_token"],
        "grant_type": "refresh_token",
    })
    resp.raise_for_status()
    new_tokens = resp.json()
    new_tokens.setdefault("refresh_token", tokens["refresh_token"])
    TOKEN_FILE.write_text(json.dumps(new_tokens, indent=2))
    return new_tokens["access_token"]


# ============================================================================
# Schedule handling


def _valid_date(value: str) -> str:
    try:
        datetime.strptime(value, "%Y-%m-%d")
    except ValueError:
        raise argparse.ArgumentTypeError(f"'{value}' is not a valid date, use YYYY-MM-DD")
    return value


def load_schedule() -> list[dict]:
    if not SCHEDULE_FILE.exists():
        raise FileNotFoundError(
            f"No schedule file found at {SCHEDULE_FILE}. Create it with columns: "
            "participant_id,start_date,end_date"
        )
    rows = []
    with SCHEDULE_FILE.open(newline="") as f:
        reader = csv.DictReader(f)
        for i, row in enumerate(reader, start=2):  # start=2: header is line 1
            for field in ("participant_id", "start_date", "end_date"):
                if not row.get(field):
                    raise ValueError(f"schedule.csv line {i}: missing '{field}'")
            _valid_date(row["start_date"])
            _valid_date(row["end_date"])
            if row["end_date"] < row["start_date"]:
                raise ValueError(
                    f"schedule.csv line {i}: end_date before start_date for "
                    f"participant '{row['participant_id']}'"
                )
            rows.append(row)

    # Warn on overlapping windows — usually a handoff mistake, not intentional
    sorted_rows = sorted(rows, key=lambda r: r["start_date"])
    for a, b in zip(sorted_rows, sorted_rows[1:]):
        if b["start_date"] <= a["end_date"]:
            print(
                f"⚠️  Warning: '{a['participant_id']}' ({a['start_date']}–{a['end_date']}) "
                f"overlaps with '{b['participant_id']}' ({b['start_date']}–{b['end_date']}). "
                "Double check schedule.csv."
            )
    return rows


# ============================================================================
# Data pull


# The filter field differs per data type — confirmed against Google's own
# codelab/reference examples, but NOT yet verified against a live response
# for every type here (heart-rate and sleep examples come straight from
# Google's docs; distance is inferred by analogy to steps since it's the
# same "interval of time" shape — double check this one if it 400s).
FILTER_FIELDS = {
    "steps": "steps.interval.start_time",
    "distance": "distance.interval.start_time",
    "sleep": "sleep.interval.end_time",
    "heart-rate": "heart_rate.sample_time.physical_time",
}


def get_data_points(access_token: str, data_type: str, start_date: str, end_date: str) -> dict:
    field = FILTER_FIELDS.get(data_type)
    if field is None:
        raise ValueError(f"No filter field configured for data type '{data_type}'")

    # Exclusive upper bound (day AFTER end_date) avoids ambiguity from
    # using 23:59:59Z, which can miss the last fraction of a day.
    end_exclusive = (datetime.strptime(end_date, "%Y-%m-%d") + timedelta(days=1)).strftime("%Y-%m-%d")

    filter_expr = f'{field} >= "{start_date}T00:00:00Z" AND {field} < "{end_exclusive}T00:00:00Z"'

    resp = requests.get(
        f"{BASE_URL}/users/me/dataTypes/{data_type}/dataPoints",
        headers={
            "Authorization": f"Bearer {access_token}",
            "Accept": "application/json",
        },
        params={"filter": filter_expr},
    )
    resp.raise_for_status()
    return resp.json()


def get_profile(access_token: str) -> dict:
    # Profile is account-level info, not a time-series dataType — no date range applies.
    resp = requests.get(
        f"{BASE_URL}/users/me/profile",
        headers={
            "Authorization": f"Bearer {access_token}",
            "Accept": "application/json",
        },
    )
    resp.raise_for_status()
    return resp.json()


def _flatten(record: dict, prefix: str = "") -> dict:
    """Flatten a nested dict into dotted-key columns, e.g. {'a': {'b': 1}} -> {'a.b': 1}."""
    flat = {}
    for key, value in record.items():
        full_key = f"{prefix}{key}" if not prefix else f"{prefix}.{key}"
        if isinstance(value, dict):
            flat.update(_flatten(value, full_key))
        elif isinstance(value, list):
            flat[full_key] = json.dumps(value)  # keep lists as a JSON string in one cell
        else:
            flat[full_key] = value
    return flat


def _extract_records(payload: dict) -> list[dict]:
    """
    Find the list of individual data points inside an API response and flatten each one.
    'dataPoints' is the confirmed wrapper key per Google's docs. The other keys are kept
    as a defensive fallback in case a specific endpoint ever differs.
    """
    for key in ("dataPoints", "point", "data", "results"):
        if isinstance(payload.get(key), list):
            return [_flatten(r) for r in payload[key]]
    return [_flatten(payload)]  # fallback: no known list key found, flatten as-is


def _write_csv(records: list[dict], out_file: Path):
    if not records:
        out_file.write_text("")  # no data for this range — empty file, not skipped silently
        return
    # Union of all keys across records, in case some data points have extra/missing fields
    fieldnames = sorted({key for r in records for key in r.keys()})
    with out_file.open("w", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(records)


def pull_for_participant(access_token: str, participant_id: str, start_date: str, end_date: str):
    out_dir = OUTPUT_DIR / f"{participant_id}_{start_date}_{end_date}"
    out_dir.mkdir(parents=True, exist_ok=True)

    print(f"\n=== {participant_id}: {start_date} to {end_date} ===")

    for dt in DATA_TYPES:
        print(f"  Fetching '{dt}'...")
        try:
            payload = get_data_points(access_token, dt, start_date, end_date)
            records = _extract_records(payload)
            out_file = out_dir / f"{dt}.csv"
            _write_csv(records, out_file)
            print(f"    saved -> {out_file} ({len(records)} row(s))")
        except requests.HTTPError as e:
            print(f"    ⚠️ failed: {e}")

    print("  Fetching 'profile'...")
    try:
        profile = get_profile(access_token)
        out_file = out_dir / "profile.csv"
        _write_csv([_flatten(profile)], out_file)
        print(f"    saved -> {out_file}")
    except requests.HTTPError as e:
        print(f"    ⚠️ failed: {e}")


# ============================================================================
# Run


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--all", action="store_true",
                        help="Pull every participant window listed in config/schedule.csv")
    parser.add_argument("--participant",
                        help="Pull just this participant. Uses their window from "
                            "schedule.csv unless --start-date/--end-date are also given.")
    parser.add_argument("--start-date", type=_valid_date,
                        help="Manual override start date, YYYY-MM-DD (requires --participant)")
    parser.add_argument("--end-date", type=_valid_date,
                        help="Manual override end date, YYYY-MM-DD (requires --participant)")
    args = parser.parse_args()

    if not args.all and not args.participant:
        parser.error("specify either --all or --participant")
    if (args.start_date or args.end_date) and not args.participant:
        parser.error("--start-date/--end-date require --participant")
    if bool(args.start_date) != bool(args.end_date):
        parser.error("--start-date and --end-date must be given together")

    access_token = get_access_token()

    if args.all:
        for row in load_schedule():
            pull_for_participant(access_token, row["participant_id"], row["start_date"], row["end_date"])

    elif args.start_date:
        # Manual override — skips schedule.csv entirely
        if args.end_date < args.start_date:
            parser.error("--end-date must be on or after --start-date")
        pull_for_participant(access_token, args.participant, args.start_date, args.end_date)

    else:
        # Look up this one participant's window from schedule.csv
        matches = [r for r in load_schedule() if r["participant_id"] == args.participant]
        if not matches:
            parser.error(f"'{args.participant}' not found in {SCHEDULE_FILE}")
        for row in matches:
            pull_for_participant(access_token, row["participant_id"], row["start_date"], row["end_date"])