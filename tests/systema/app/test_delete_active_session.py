"""
tests/systema/app/test_delete_active_session.py

Deleting the session you are currently in replaces it with a fresh one — and
that replacement must look exactly like any other new session.

It did not. `delete_session` hand-rolled its own copy of the new-session flow,
so it drifted from `create_new_session`:

  * no greeting banner (it still posted the retired "Session Deleted" grey line
    that the banner replaced everywhere else), and
  * `clear_history()` empties the transcript but does NOT reset
    `session_has_messages`, so `_create_new_session()` believed the session
    still had messages, called `_auto_save_session()`, and wrote the
    JUST-DELETED session id back to disk with the now-empty history — the
    deleted session reappeared in the sidebar as an empty one.

Both entry points share `_present_new_session()` now.
"""

from systema.app.controller import AssistantController


class _Chat:
    """Records every chat call by name."""
    def __init__(self):
        self.calls = []

    def __getattr__(self, name):
        def _record(*a, **k):
            self.calls.append((name, a))
        return _record


class _AI:
    def __init__(self):
        self.cleared = False
        self.conversation_history = []

    def clear_history(self):
        self.cleared = True
        self.conversation_history = []


class _SessionManager:
    def __init__(self):
        self.deleted = []
        self._n = 0

    def delete_session(self, sid):
        self.deleted.append(sid)

    def create_session(self):
        self._n += 1
        return f"NEW_{self._n}"


class _UI:
    """`_chat` is a read-only property reading ui.chat_window."""
    def __init__(self, chat):
        self.chat_window = chat
        self.android_bridge = None


def _controller(active="OLD", has_messages=True):
    c = AssistantController.__new__(AssistantController)
    c.current_session_id = active
    c.session_has_messages = has_messages
    c.ui = _UI(_Chat())
    c.ai = _AI()
    c.session_manager = _SessionManager()
    c.saves = []
    c.log = lambda *a, **k: None
    c._auto_save_session = lambda: c.saves.append(c.current_session_id)
    for name in ("delete_session", "_create_new_session", "_present_new_session"):
        setattr(c, name, getattr(AssistantController, name).__get__(c, AssistantController))
    return c


# ── the reported bug ─────────────────────────────────────────────────────────

def test_deleting_the_active_session_shows_the_greeting_banner():
    c = _controller()

    c.delete_session("OLD")

    assert "add_greeting_banner" in [n for n, _ in c.ui.chat_window.calls]


def test_it_no_longer_posts_the_retired_session_deleted_line():
    c = _controller()

    c.delete_session("OLD")

    systems = [a[0] for n, a in c.ui.chat_window.calls if n == "add_system_message" and a]
    assert not any("Session Deleted" in str(s) for s in systems)


def test_the_replacement_looks_like_any_other_new_session():
    """Same presentation path, so the two cannot drift again."""
    c = _controller()

    c.delete_session("OLD")

    names = [n for n, _ in c.ui.chat_window.calls]
    for expected in ("clear_chat_silent", "clear_pinned_images",
                     "refresh_session_list", "add_greeting_banner",
                     "warn_loaded_skills_if_any"):
        assert expected in names, f"{expected} missing from the delete path"


# ── the resurrection bug ─────────────────────────────────────────────────────

def test_a_deleted_session_is_not_written_back_to_disk():
    c = _controller(has_messages=True)

    c.delete_session("OLD")

    assert c.saves == [], f"re-saved the deleted session: {c.saves}"
    assert c.session_manager.deleted == ["OLD"]


def test_a_fresh_session_id_is_active_afterwards():
    c = _controller()

    c.delete_session("OLD")

    assert c.current_session_id != "OLD"
    assert c.session_has_messages is False


# ── the non-active branch is untouched ───────────────────────────────────────

def test_deleting_some_other_session_leaves_the_current_one_alone():
    c = _controller(active="OLD")

    c.delete_session("SOMETHING_ELSE")

    assert c.current_session_id == "OLD"
    assert c.ai.cleared is False
    assert c.session_manager.deleted == ["SOMETHING_ELSE"]
    assert "add_greeting_banner" not in [n for n, _ in c.ui.chat_window.calls]
