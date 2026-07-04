# Software Updates

Systema Auxilium can update itself from GitHub, in-app, with a full review step —
no git knowledge required. Open it under **Settings → System** (full controls) or
**Settings → General → Check for Updates** (shortcut).

## What you get

- **Review before applying.** See every changed file and its exact diff. Files
  with real textual changes are highlighted and pre-selected; you choose what to
  apply (whole update or a subset of files).
- **Preserves your local edits.** A 3-way merge folds upstream changes into files
  you have modified. Genuine conflicts are marked for you to resolve by hand
  rather than silently overwritten.
- **Dependency diff.** Newly required or version-bumped Python packages are
  detected and installed automatically on apply; removed packages are reported
  (never auto-uninstalled). See below.
- **Backup and revert.** A snapshot is taken before anything changes, so the whole
  update reverts with one click. Snapshot history is kept, never pruned.
- **Your data is never touched.** Settings and the `data/` folder are excluded
  from updates.
- **Protected files.** Files you own and configure — your provider scripts under
  `providers/**` and your `skills/**` — stay visible in a plan but are
  auto-unselected and flagged `PROTECTED`; applying a change to one takes an
  explicit opt-in and a warning, so an update can never silently overwrite your
  API keys or your work. (A brand-new file in those folders is only additive and
  is treated normally.)
- **Startup check.** If a new version is available, the app can offer to open the
  updater on launch (toggleable).

## How dependencies are detected

The updater compares your installed `requirements.txt` against the incoming one
and reports a **diff**:

- **added** — a package the new version needs that you do not have → installed on
  apply;
- **changed** — a version specifier that moved (e.g. `>=0.6.0 → >=0.7.0`) →
  installed on apply;
- **removed** — a package dropped upstream → reported only, never uninstalled.

Only added + changed packages are installed. The update window shows a summary
like `New: 2  ·  Changed: 1  ·  Removed: 0`.

## Baseline / first-run

Three-way merge needs a common ancestor. The app can **seed a baseline** (fetch
the pristine upstream snapshot and record it without touching your files) so that,
from then on, any way your files differ from that baseline is correctly
attributed to *your* edits instead of shown as an update change. If no baseline
exists yet, the review falls back to a simple two-way, update-origin diff.

## Powered by updater-gitplucker

The whole update engine is the open-source library
[**updater-gitplucker**](https://github.com/uukjtisa/updater-gitplucker)
(`pip install updater-gitplucker`), reusable in any Python project. It provides
allowlisted repos, three channels (release / source / python-source with 3-way
merge and requirements-diff dependency detection), selective apply, rollback, and
the diff/review machinery the update window renders.
