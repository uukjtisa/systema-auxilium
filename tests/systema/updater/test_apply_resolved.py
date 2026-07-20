"""
Tests that UpdaterService.apply(resolved=...) rewrites the plan's write-ops so a
user-resolved conflict is written verbatim through the ONE apply path (the fix
for "took the update but nothing applied + tag stayed"). Pure logic — a stub
plan with FileOp objects, no GitHub, no threads.
"""
import types


class _Op:
    def __init__(self, relpath, kind, src=None, text=None):
        self.relpath = relpath
        self.kind = kind
        self.src = src
        self.text = text


def _make_service(monkeypatch, tmp_path, ops):
    from systema.updater import service as svc_mod
    svc = svc_mod.UpdaterService.__new__(svc_mod.UpdaterService)   # skip __init__/Qt
    plan = types.SimpleNamespace(_ops=ops, file_changes=[], target_version="v2")
    svc._plan = plan
    svc._updater = object()
    svc._branch = "main"          # apply() reads _branch first; saved_branch (a
                                  # read-only property) is never reached here.
    captured = {}

    def _fake_worker(fn):
        # Run the apply body synchronously and capture the ops it would execute.
        captured["ops"] = [(o.relpath, o.kind, o.text) for o in plan._ops]
        w = types.SimpleNamespace(ok=types.SimpleNamespace(connect=lambda *_: None),
                                  err=types.SimpleNamespace(connect=lambda *_: None),
                                  start=lambda: None)
        return w

    monkeypatch.setattr(svc_mod, "_FnWorker", _fake_worker)
    svc.apply_started = types.SimpleNamespace(emit=lambda *a, **k: None)
    svc.apply_failed = types.SimpleNamespace(emit=lambda *a, **k: None)
    return svc, plan, captured


def test_resolved_rewrites_write_op(monkeypatch, tmp_path):
    ops = [_Op("engine/core.py", "write", text="<<<<<<< markers >>>>>>>"),
           _Op("ui/other.py", "copy", src="somewhere")]
    svc, plan, captured = _make_service(monkeypatch, tmp_path, ops)

    svc.apply(only=["engine/core.py", "ui/other.py"],
              resolved={"engine/core.py": "RESOLVED CONTENT\n"})

    core_op = next(o for o in plan._ops if o.relpath == "engine/core.py")
    assert core_op.kind == "write"
    assert core_op.text == "RESOLVED CONTENT\n"
    assert core_op.src is None
    # The untouched op is unchanged.
    other = next(o for o in plan._ops if o.relpath == "ui/other.py")
    assert other.kind == "copy"


def test_copy_op_becomes_write_when_resolved(monkeypatch, tmp_path):
    # Even a REMOTE-policy conflict (copy op) is coerced to a write of the
    # resolved text so the user's merge always wins.
    ops = [_Op("providers/x.py", "copy", src="payload/x.py")]
    svc, plan, captured = _make_service(monkeypatch, tmp_path, ops)
    svc.apply(only=["providers/x.py"], resolved={"providers/x.py": "MINE\n"})
    op = plan._ops[0]
    assert (op.kind, op.text, op.src) == ("write", "MINE\n", None)
