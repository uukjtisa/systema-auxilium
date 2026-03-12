"""
SkillManager — watches the skills/ folder every 0.5 s, parses SKILL.md
frontmatter, and emits a Qt signal whenever anything changes.
"""

import re
import shutil
from pathlib import Path

from PyQt6.QtCore import QObject, QTimer, pyqtSignal

from core.logger import _make_logger, _NoOpLogger

_verbose = True
log = _make_logger("SkillManager") if _verbose else _NoOpLogger()


class SkillManager(QObject):
    """Watches the skills directory and provides skill data to the rest of the app."""

    skills_changed = pyqtSignal()          # emitted on ANY change to the skills folder
    loaded_skills_changed = pyqtSignal()   # emitted when a skill is loaded or unloaded

    # ──────────────────────────────────────────────────────────────────────────
    def __init__(self, skills_dir: Path):
        super().__init__()
        self.skills_dir = skills_dir
        self.skills_dir.mkdir(parents=True, exist_ok=True)
        log.info(f"[SkillManager.__init__] skills_dir='{self.skills_dir}'")

        self._snapshot: dict[str, float] = {}  # {folder_name: mtime}
        self._timer = QTimer(self)
        self._timer.setInterval(500)
        self._timer.timeout.connect(self._poll)

        # Tracks which skills are currently loaded (name → content)
        self._loaded_skills: dict[str, str] = {}

    # ── Public API ─────────────────────────────────────────────────────────────

    def start_watching(self):
        """Start the 0.5 s poll timer."""
        self._snapshot = self._build_snapshot()
        self._timer.start()
        log.info("[SkillManager.start_watching] Watcher started (500 ms interval)")

    def get_skills(self) -> list[dict]:
        """
        Return a list of skill dicts:
          {name, description, path, files, is_loaded}
        Skips folders without a SKILL.md.
        """
        skills = []
        seen_names: dict[str, str] = {}  # name → folder for duplicate detection

        for folder in sorted(self.skills_dir.iterdir()):
            if not folder.is_dir() or folder.name.startswith('.'):
                continue
            skill_md = folder / "SKILL.md"
            if not skill_md.exists():
                log.debug(f"[SkillManager.get_skills] Skipping '{folder.name}' — no SKILL.md")
                continue
            meta = self._parse_frontmatter(skill_md)
            name = meta['name']
            if name in seen_names:
                log.warning(
                    f"[SkillManager.get_skills] Duplicate skill name '{name}' "
                    f"in '{folder.name}' and '{seen_names[name]}' — last wins"
                )
            seen_names[name] = folder.name

            # Collect non-hidden files relative to the skill folder
            files = [
                str(f.relative_to(folder))
                for f in sorted(folder.rglob("*"))
                if f.is_file() and not f.name.startswith('.')
            ]
            skills.append({
                'name': name,
                'description': meta['description'],
                'path': folder,
                'files': files,
                'is_loaded': name in self._loaded_skills,
            })
        log.debug(f"[SkillManager.get_skills] Returning {len(skills)} skill(s)")
        return skills

    def get_skill_content(self, name: str) -> str:
        """
        Read and return the full SKILL.md content for the skill identified by
        *name* (matches the frontmatter 'name' field, case-insensitive).
        Returns an error string if not found.
        """
        name_lower = name.strip().lower()
        for skill in self.get_skills():
            if skill['name'].lower() == name_lower:
                skill_md = skill['path'] / "SKILL.md"
                content = skill_md.read_text(encoding='utf-8')
                log.info(f"[SkillManager.get_skill_content] Loaded '{skill['name']}' "
                         f"({len(content)} chars)")
                return content
        log.warning(f"[SkillManager.get_skill_content] Skill '{name}' not found")
        return f"ERROR: Skill '{name}' not found. Available skills: " + \
               ", ".join(s['name'] for s in self.get_skills())

    def load_skill(self, name: str) -> tuple[bool, str]:
        """
        Load a skill into the active loaded set.
        Returns (success, message).
        """
        name_lower = name.strip().lower()
        for skill in self.get_skills():
            if skill['name'].lower() == name_lower:
                canonical = skill['name']
                if canonical in self._loaded_skills:
                    log.warning(f"[SkillManager.load_skill] Skill '{canonical}' is ALREADY loaded — skipping")
                    return False, f"Skill '{canonical}' is already loaded."
                content = (skill['path'] / 'SKILL.md').read_text(encoding='utf-8')
                self._loaded_skills[canonical] = content
                log.info(f"[SkillManager.load_skill] Loaded '{canonical}' | "
                         f"total loaded: {list(self._loaded_skills.keys())}")
                self.loaded_skills_changed.emit()
                return True, f"Skill '{canonical}' loaded."
        log.warning(f"[SkillManager.load_skill] Skill '{name}' not found")
        return False, f"Skill '{name}' not found."

    def unload_skill(self, name: str) -> tuple[bool, str]:
        """
        Unload a skill from the active loaded set.
        Returns (success, message).
        """
        name_lower = name.strip().lower()
        for canonical in list(self._loaded_skills.keys()):
            if canonical.lower() == name_lower:
                del self._loaded_skills[canonical]
                log.info(f"[SkillManager.unload_skill] Unloaded '{canonical}' | "
                         f"remaining: {list(self._loaded_skills.keys())}")
                self.loaded_skills_changed.emit()
                return True, f"Skill '{canonical}' unloaded."
        log.warning(f"[SkillManager.unload_skill] Skill '{name}' is not loaded — cannot unload")
        return False, f"Skill '{name}' is not currently loaded."

    def is_loaded(self, name: str) -> bool:
        """Return True if the skill with this name is currently loaded."""
        return name in self._loaded_skills

    def get_loaded_skills(self) -> dict[str, str]:
        """Return a copy of the currently loaded skills dict {name: content}."""
        return dict(self._loaded_skills)

    def clear_loaded_skills(self):
        """Unload all skills at once (kept for compatibility but no longer called automatically)."""
        if self._loaded_skills:
            self._loaded_skills.clear()
            log.info("[SkillManager.clear_loaded_skills] All skills cleared")
            self.loaded_skills_changed.emit()

    def create_skill_template(self, name: str):
        """Create a new skill folder with placeholder files."""
        name = name.strip()
        if not name:
            log.warning("[SkillManager.create_skill_template] Empty name — ignored")
            return

        folder = self.skills_dir / name
        folder.mkdir(parents=True, exist_ok=True)
        (folder / "scripts").mkdir(exist_ok=True)
        (folder / "templates").mkdir(exist_ok=True)
        (folder / "scripts" / ".gitkeep").touch()
        (folder / "templates" / ".gitkeep").touch()

        skill_md = folder / "SKILL.md"
        if not skill_md.exists():
            skill_md.write_text(
                f'---\n'
                f'name: {name}\n'
                f'description: "Describe when the AI should use this skill. Include trigger keywords."\n'
                f'---\n\n'
                f'# {name} Skill\n\n'
                f'## Overview\n'
                f'What this skill does.\n\n'
                f'## Instructions\n'
                f'Step-by-step instructions for the AI.\n\n'
                f'## Examples\n'
                f'Show the AI how to use this skill.\n',
                encoding='utf-8'
            )
        log.info(f"[SkillManager.create_skill_template] Created template for '{name}' at '{folder}'")

    def delete_skill(self, name: str):
        """Move the skill folder to OS trash (or a .trash sub-folder if send2trash unavailable)."""
        for skill in self.get_skills():
            if skill['name'] == name:
                folder: Path = skill['path']
                try:
                    from send2trash import send2trash
                    send2trash(str(folder))
                    log.info(f"[SkillManager.delete_skill] '{name}' sent to OS trash")
                except ImportError:
                    trash_dir = self.skills_dir / ".trash"
                    trash_dir.mkdir(exist_ok=True)
                    dest = trash_dir / folder.name
                    shutil.move(str(folder), str(dest))
                    log.info(f"[SkillManager.delete_skill] '{name}' moved to .trash/")
                return
        log.warning(f"[SkillManager.delete_skill] Skill '{name}' not found")

    # ── Internal ───────────────────────────────────────────────────────────────

    def _poll(self):
        """Called every 500 ms. Compare snapshot; emit signal if anything changed."""
        new_snapshot = self._build_snapshot()
        if new_snapshot != self._snapshot:
            log.debug(f"[SkillManager._poll] Change detected — emitting skills_changed")
            self._snapshot = new_snapshot
            self.skills_changed.emit()

    def _build_snapshot(self) -> dict[str, float]:
        """Build {folder_name: latest_mtime} for all skill folders."""
        snapshot: dict[str, float] = {}
        if not self.skills_dir.exists():
            return snapshot
        for folder in self.skills_dir.iterdir():
            if not folder.is_dir() or folder.name.startswith('.'):
                continue
            # Use the max mtime of the folder itself + all files inside
            mtimes = [folder.stat().st_mtime]
            for f in folder.rglob("*"):
                try:
                    mtimes.append(f.stat().st_mtime)
                except OSError:
                    pass
            snapshot[folder.name] = max(mtimes)
        return snapshot

    def _parse_frontmatter(self, path: Path) -> dict:
        """Parse YAML-ish frontmatter from a SKILL.md file."""
        try:
            text = path.read_text(encoding='utf-8')
        except OSError:
            return {'name': path.parent.name, 'description': ''}

        match = re.match(r'^---\n(.*?)\n---', text, re.DOTALL)
        if not match:
            return {'name': path.parent.name, 'description': ''}

        raw = match.group(1)
        name_m = re.search(r'^name:\s*(.+)$', raw, re.MULTILINE)
        desc_m = re.search(r'^description:\s*["\']?(.*?)["\']?\s*$', raw, re.MULTILINE | re.DOTALL)
        return {
            'name': name_m.group(1).strip() if name_m else path.parent.name,
            'description': desc_m.group(1).strip() if desc_m else '',
        }
