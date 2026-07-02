"""
Self-update check tool for Systema Auxilium (used by the self-knowledge skill).

Run:  python skills/self-knowledge/self_update_check.py
Read-only: checks GitHub for a newer version and prints a concise report.
It NEVER applies anything. In the developer working directory it reports that
auto-update is disabled (so the dev copy's divergence is not treated as an update).
"""

import sys
from pathlib import Path

# Make the app package importable when run directly as a script.
_ROOT = Path(__file__).resolve().parents[2]   # skills/self-knowledge/ -> app root
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))


def _saved_branch(default: str) -> str:
    try:
        import json
        s = json.loads((_ROOT / "assistant_settings.json").read_text(encoding="utf-8"))
        b = s.get("update_branch")
        if b in ("main", "unstable"):
            return b
    except Exception:
        pass
    return default


def main() -> None:
    try:
        from systema.app.updater_service import (DEFAULT_BRANCH, REPO,
                                                  in_dev_environment, make_updater)
    except Exception as e:
        print(f"[self-update] updater unavailable in this environment: {e}")
        return

    branch = _saved_branch(DEFAULT_BRANCH)
    print(f"Systema Auxilium - self-update check   (repo {REPO}, branch '{branch}')")

    if in_dev_environment():
        print("Environment: DEVELOPER WORKING DIRECTORY - auto-update is disabled here.")
        print("This copy intentionally diverges from the released repo, so its "
              "differences are not a real 'update'.")
        return

    try:
        updater = make_updater(branch)
        plan = updater.check_repo(REPO, branch)
    except Exception as e:
        print(f"Could not check for updates: {e}")
        return

    changed = [fc for fc in plan.file_changes if fc.change.value != "unchanged"]
    print(f"Installed version: {plan.current_version or '(baseline not set yet)'}")
    print(f"Latest available : {plan.target_version}")
    if plan.has_update and (changed or plan.dependency_changes):
        print(f"UPDATE AVAILABLE - {len(changed)} changed file(s), "
              f"{len(plan.dependency_changes)} new dependency(ies), "
              f"{len(plan.conflicts)} conflict(s).")
        print("Apply it in Settings > System (or General) > Check for Updates.")
    else:
        print("You are up to date.")
    updater.discard(plan)


if __name__ == "__main__":
    main()
