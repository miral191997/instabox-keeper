# InstaBox keeper

Wakes paused/stopped machines and (optionally) pauses+resumes them just before InstaBox's 8-running-hours pause, so the scrape
never waits for a human. It runs OUTSIDE the machines. It never deletes, stops or creates anything.

## Put it on GitHub Actions (free for PUBLIC repos; a private repo would use up the free minutes)
1. Create a new PUBLIC GitHub repo (e.g. `instabox-keeper`) and add only these files at its root: `instabox_keeper.py` and `.github/workflows/instabox-keeper.yml`.
   (The repo holds no secrets. Do NOT add your API key or machine list to any file.)
2. InstaBox dashboard -> **API keys** -> create a key (restricted to machines if the form offers scopes).
   Never paste the key into a chat or a VM session: session exports go to Drive.
3. Repo -> Settings -> Secrets and variables -> Actions:
   - Secret `INSTABOX_API_KEY` = the key
   - Secret `INSTABOX_MACHINES` = `vm1,vm2,google-maps-scrapper-3` (exact machine names from the dashboard)
   - Variable `CYCLE_HOURS` = leave empty at first.
4. Actions tab -> instabox-keeper -> **Run workflow** once; open the log: every machine should show its state and `running_h`.
5. Pause a machine by hand, wait one cycle (<= ~15 min): it should be resumed by the keeper (log line action=resume).

## Turn on the 8-hour cycle (only after you checked the clock restarts on resume)
Variable `CYCLE_HOURS` = `7.25`. A machine that has run 7.25 h since its last resume gets paused and resumed at once (a few seconds;
memory and processes are kept). Test it on one machine first: put just that machine in INSTABOX_MACHINES.

## Or run it on your Mac (only works while the Mac is awake)
`INSTABOX_API_KEY=... INSTABOX_MACHINES=vm1,vm2,google-maps-scrapper-3 python3 instabox_keeper.py --cycle-hours 7.25`   (add `--dry-run` to only print)
Test it offline: `python3 -I tests/test_keeper.py`.
