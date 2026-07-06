# Google Health API Extractor (for a shared device)

Pulls Fitbit sleep/activity data via the Google Health API for a study using **one shared physical Fitbit and one Google Health account**, handed to a different participant each week/session. 

Data is attributed to participants by date range (see `config/schedule.csv`), not by account — there's only ever one account.

## Setup

1. Fork this repo to create a private repo.

2. Get access to the shared credentials (ask whoever set this up).
   - `config/google_health_client.json` — OAuth Client ID/Secret
   - `config/tokens/google_health_shared.json` — the live token (gets generate after the first-run, which needs browser authentication)

   Neither are in this repo — they're gitignored. **Never commit to a public repo.**

3. Update the `config/schedule.csv` to track the start and end dates of each participant's sleep session.

*Example*
   ```csv
   participant_id,start_date,end_date
   p_01,2026-06-01,2026-06-07
   p_02,2026-06-08,2026-06-14
   ```
   Dates are inclusive, `YYYY-MM-DD`. Must keep periods non-overlapping.
   Assumes one study session will be overnight, so one participant per date period.

4. Establish a virtual environment to run the script inside.

*Conda env*
```shell
conda create -n api_extract python=3.11
conda activate api_extract
conda install requests
```
OR
*Python venv*
```shell
python -m venv api_extract
source api_extract/bin/activate
pip install requests
```

## Usage

In VSCode terminal:

```shell
python fitbit_api.py --all                     # pull every row in schedule.csv
python fitbit_api.py --participant p_01        # pull just one participant's window
python fitbit_api.py --participant p_01 --start-date 2026-06-01 --end-date 2026-06-07
                                                # manual override, ignores schedule.csv
```

First-run (no live token) opens a browser for the one shared Google account to sign
in and approve access, then saves the token locally. Every later run is
silent (auto-refreshes).

## Output

```shell
output/<participant_id>_<start_date>_<end_date>/steps.csv
output/<participant_id>_<start_date>_<end_date>/heart-rate.csv
output/<participant_id>_<start_date>_<end_date>/sleep.csv
output/<participant_id>_<start_date>_<end_date>/distance.csv
output/<participant_id>_<start_date>_<end_date>/profile.csv
```

Nested JSON fields are flattened into dotted columns (e.g. `profile.name`, `profile.address.city`). 
After running this script, `output/` will be where the raw participant data.


## Notes
 
### How to add more variables
 
The script only pulls the data types listed in `DATA_TYPES`, and each data
type requires a matching scope in `SCOPES`. To pull more variables:
 
1. Add the scope to `SCOPES` in `fitbit_api.py`.
2. Add the matching data type identifier to `DATA_TYPES`, which is [available here](https://developers.google.com/health/reference/rest/v4/users.dataTypes.dataPoints)
3. Delete `config/tokens/google_health_shared.json` and re-run the script —
   this forces a fresh browser consent, since the old token doesn't cover
   the new scope. (Skipping this step causes silent 403s on the new data
   type while everything else keeps working.)

Full list of Google Health scopes available on this project:
 
| Scope suffix | Covers | Currently used? |
|---|---|---|
| `activity_and_fitness.readonly` | steps, distance, floors, active minutes, etc. | ✅ |
| `health_metrics_and_measurements.readonly` | heart rate, weight, body fat, SpO2, etc. | ✅ |
| `sleep.readonly` | sleep stages, sleep summary | ✅ |
| `profile.readonly` | account holder's name/demographic info | ✅ |
| `irn.readonly` | read below` | no |
| `ecg.readonly` | read below | no |
| `location.readonly` | GPS coordinates recorded during exercise | no |
| `nutrition.readonly` | logged food/water entries, calories, macros | no |
 
*What IRN and ECG actually are*:

- **IRN (Irregular Rhythm Notification)** — passively watches pulse-rate data while the wearer is still/resting, and flags an irregular rhythm that could indicate atrial fibrillation(AFib). It's a notification/flag, not a continuous waveform for raw cardiac data.

- **ECG (Electrocardiogram)** — an on-demand, ~30-second recording using electrical sensors (not the optical pulse sensor IRN uses). The wearer
  actively triggers it. It produces an actual rhythm classification, qualitatively similar to a single-lead clinical ECG, and can distinguish AFib from normal sinus rhythm.

If either is needed: add `ecg.readonly` / `irn.readonly` to `SCOPES` and `electrocardiogram` / `irregular-rhythm-notification` to `DATA_TYPES` (the confirmed endpoint identifiers from Google's docs).

 
### How to edit OAuth authentication 
 
1. Go to [console.cloud.google.com](https://console.cloud.google.com) and confirm **Fitbit Direct User Dev** is the selected project (top-left project picker — easy to accidentally edit the wrong project).
2. Left sidebar → **APIs & Services** → **Google Auth Platform**.
3. Three relevant tabs:

   **Audience** 
    - Add/remove *Test users* here (each participant or teammate's Google account must be listed here before they can authorize, since the app isn't published/verified). 
    - Also shows Testing vs Production publishing status, and the 100-test-user cap.

    **Data Access** 
    - Add/remove *scopes* here. 
    - Click *"Add or remove scopes"*, check the ones you need and then *Update*.
    - This is separate from editing `SCOPES` in the script — both need to match, or you'll get an error when running the script.
    
   **Clients**
   - This is where the OAuth *Client ID + Secret* live (what's saved locally as `config/google_health_client.json`)
   - Always confirm the redirect URIs here still list `http://localhost:8765` exactly. But the script does fallback on `http://google.com`
 