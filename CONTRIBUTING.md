# Contributing

Thanks for taking a look. This is a small project, so the process is short.

## Getting set up

```powershell
git clone https://github.com/hehehegood6723/WTToolbox.git
cd WTToolbox
python -m venv .venv
.venv\Scripts\pip install -r requirements.txt -r requirements-dev.txt
.venv\Scripts\python src\main.py
```

`build.ps1` picks up `.venv` automatically. If your interpreter lives somewhere
else, set `WTTOOLBOX_PYTHON` (and optionally `WTTOOLBOX_BUILD` for the
PyInstaller scratch trees).

## Running the tests

```powershell
# all six suites
Get-ChildItem tests\test_*.py | ForEach-Object { .venv\Scripts\python $_.FullName }
```

They work with or without War Thunder installed:

* **with a game** - the integration checks really run (byte-exact `config.blk`
  round-trip, reading `sound\`, live path detection). Point the suites at a
  specific install with `WTTOOLBOX_GAME=D:\WarThunder`.
* **without one** - those checks report as skipped and everything else still
  runs. That is the mode CI uses.

Please keep it that way: a new check that needs a game install should go behind
`HAVE_GAME` (see `tests/_game.py`) rather than failing on a machine that has
never had the game.

## Ground rules that keep this honest

These are the constraints the whole project is built around:

1. **No cheats, no injection, no memory reading.** Nothing that could affect a
   match. The app reads and writes ordinary files in the game folder and
   nothing else.
2. **Never invent data.** If a value cannot be read, show that it could not be
   read. Do not guess, interpolate, or fill in a plausible number. This applies
   to the vehicle comparison (a missing axis is dropped, not estimated), the
   statistics page (the official API is behind a 403 anti-bot page, so the app
   opens the real page in a browser instead of scraping it) and local stats.
3. **Anything destructive is reversible.** Configuration edits back the file up
   first, sound-mod installs record a rollback set, deletions go to a trash
   folder. If you add a feature that changes user files, it needs the same
   treatment.
4. **Say what did not work.** The README has a "Known limitations" section and
   the bug list records what was broken and why. Keeping an accurate record is
   part of the work, not a chore after it.

## Pull requests

* One topic per PR.
* Run the suites before opening it and mention the counts you saw.
* If you change `config.blk` handling, `test_blk.py` must still prove a
  byte-exact round-trip on a real file.
* ASCII-only in `.ps1` files: Windows PowerShell 5.1 reads them as ANSI and
  mangles anything else.
* Commit messages: a short imperative subject, and a body explaining *why* if
  the change is not obvious.

## Regenerating the bundled data

```powershell
# vehicle index (needs network; writes src/wttoolbox/assets/vehicles.json)
.venv\Scripts\python tools\build_vehicle_index.py

# icon.ico / banner.png / icon_256.png (needs Pillow)
.venv\Scripts\python tools\make_assets.py
```

## Reporting a bug

Include: Windows version, the game version if relevant, which of the two
builds you ran (folder or single-file), and the report produced by

```powershell
WTToolbox.exe --selftest
Get-Content "$env:APPDATA\WTToolbox\logs\selftest.txt"
```

That report covers the 35 module imports and the smoke checks, so it usually
identifies the problem immediately.
