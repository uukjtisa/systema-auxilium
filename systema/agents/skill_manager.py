"""
core/skill_manager.py
SkillManager — watches the skills/ folder every 0.5 s, parses SKILL.md
frontmatter, emits Qt signals on changes, and persists loaded-state to
skills/skills_state.json.
"""
import json
import re
import shutil
from pathlib import Path

from PyQt6.QtCore import QObject, QTimer, pyqtSignal

from systema.common.logger import _make_logger, _NoOpLogger

_verbose = True
log = _make_logger("SkillManager") if _verbose else _NoOpLogger()

_STATE_FILE = "skills_state.json"  # relative to skills_dir


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

        # Load persisted state from disk
        self._load_state()

    # ── State persistence ──────────────────────────────────────────────────────

    @property
    def _state_path(self) -> Path:
        return self.skills_dir / _STATE_FILE

    def _load_state(self):
        """Read skills_state.json and restore loaded skills."""
        if not self._state_path.exists():
            log.info("[SkillManager._load_state] No state file — starting fresh")
            return
        try:
            data = json.loads(self._state_path.read_text(encoding='utf-8'))
            loaded_names: list[str] = data.get('loaded', [])
            log.info(f"[SkillManager._load_state] Restoring {len(loaded_names)} skill(s): {loaded_names}")
            for name in loaded_names:
                # We load lazily — content will be fetched when first needed
                # but we mark the name in _loaded_skills now using actual file
                ok, _ = self.load_skill(name)
                if not ok:
                    log.warning(f"[SkillManager._load_state] Could not restore '{name}' — skipping")
        except Exception as exc:
            log.warning(f"[SkillManager._load_state] Failed to read state: {exc}")

    def _save_state(self):
        """Write currently loaded skill names to skills_state.json."""
        try:
            data = {'loaded': list(self._loaded_skills.keys())}
            self._state_path.write_text(json.dumps(data, indent=2), encoding='utf-8')
            log.debug(f"[SkillManager._save_state] Saved state: {data['loaded']}")
        except Exception as exc:
            log.warning(f"[SkillManager._save_state] Failed to save state: {exc}")



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
                self._save_state()
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
                self._save_state()
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
            self._save_state()
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
        """
        Parse YAML-ish frontmatter from a SKILL.md file.

        Handles all common description styles:
          description: single line value
          description: "quoted single line"
          description: 'quoted single line'
          description: >
            folded block scalar (YAML '>') — newlines become spaces
          description: |
            literal block scalar (YAML '|') — newlines preserved
          description: >-
            strip trailing newline variant
        """
        try:
            text = path.read_text(encoding='utf-8')
        except OSError:
            return {'name': path.parent.name, 'description': ''}

        match = re.match(r'^---\r?\n(.*?)\r?\n---', text, re.DOTALL)
        if not match:
            return {'name': path.parent.name, 'description': ''}

        raw = match.group(1)

        # ── name (always a simple single-line value) ───────────────────────────
        name_m = re.search(r'^name:\s*(.+)$', raw, re.MULTILINE)
        name = name_m.group(1).strip().strip('"\'') if name_m else path.parent.name

        # ── description ────────────────────────────────────────────────────────
        # Find where description: starts (line index inside `raw`)
        lines = raw.splitlines()
        desc_line_idx = None
        for i, line in enumerate(lines):
            if re.match(r'^description\s*:', line):
                desc_line_idx = i
                break

        description = ''
        if desc_line_idx is not None:
            header_line = lines[desc_line_idx]
            # Strip "description:" prefix and optional whitespace
            inline_value = re.sub(r'^description\s*:\s*', '', header_line).strip()

            if inline_value in ('>', '|', '>-', '|-', '>+', '|+'):
                # Block scalar — collect subsequent indented lines
                block_lines = []
                for j in range(desc_line_idx + 1, len(lines)):
                    bl = lines[j]
                    # Stop at the next non-indented key (e.g. "name:", or end of block)
                    if bl and not bl[0].isspace():
                        break
                    block_lines.append(bl.strip())
                # Drop leading/trailing blank lines
                while block_lines and not block_lines[0]:
                    block_lines.pop(0)
                while block_lines and not block_lines[-1]:
                    block_lines.pop()
                if inline_value.startswith('>'):
                    # Folded: join with space, but blank lines become newlines
                    result_parts = []
                    for bl in block_lines:
                        if bl:
                            result_parts.append(bl)
                        else:
                            result_parts.append('\n')
                    description = ' '.join(result_parts).replace(' \n ', '\n').strip()
                else:
                    # Literal: preserve newlines
                    description = '\n'.join(block_lines)
            else:
                # Inline value — strip surrounding quotes if present
                if (inline_value.startswith('"') and inline_value.endswith('"')) or \
                   (inline_value.startswith("'") and inline_value.endswith("'")):
                    inline_value = inline_value[1:-1]
                description = inline_value.strip()

        return {'name': name, 'description': description}

    @staticmethod
    def strip_frontmatter(content: str) -> str:
        """Return SKILL.md content with the YAML frontmatter block removed."""
        stripped = re.sub(r'^---\r?\n.*?\r?\n---\r?\n?', '', content, count=1, flags=re.DOTALL)
        return stripped.strip()
