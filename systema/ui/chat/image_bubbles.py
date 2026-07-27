"""
systema/ui/chat/image_bubbles.py

Images as first-class chat citizens: the ChatWindow mixin that puts attached
pictures INTO the transcript, numbered, with two kinds of detach.

WHAT THIS REPLACES
------------------
A floating "pinned image" card overlaid above the input box. That card was pure
UI state — nothing about an image was ever written to the session, so a reload
lost every picture, and the model only received them because send_message()
re-supplied the whole pinned list on each send. The moment a turn did not go
through that path (any work-mode continuation), the images were gone.

THE MODEL
---------
Each picture is an ImageRef (`systema.common.image_cache`) living on the
history entry it belongs to, under `_images`. This mixin renders those entries
and edits them; `systema.common.image_refs` owns the lookup/mutation rules and
`ai_engine` turns them into provider payload.

Two detach levels, deliberately different:

  * DETACH FROM CONTEXT — reversible. The picture stays in the transcript as a
    dimmed marker, the cached file stays on disk, and the model is told in one
    line that it can no longer see it. One click puts it back.
  * DELETE — permanent. Gone from the transcript, gone from the cache. Its
    number is RETIRED, never reissued, so an earlier "image 3 shows the header"
    in the transcript can never come to mean a different picture.

RE-RENDER STRATEGY
------------------
Split by what actually changed, because the two cases are not alike:

  * DETACH / RE-ATTACH is a FLAG FLIP on one ref. Nothing enters or leaves the
    transcript, so nothing is rebuilt — the affected bubbles are restyled in
    place. This used to replay the whole transcript through
    render_loaded_messages(), which tears down and recreates every widget in
    the chat: the view jumped, the scroll position moved out from under the
    pointer, and the neighbouring picture you were about to click was no longer
    where you were clicking. A checkbox must not cost a rebuild.
  * DELETE is STRUCTURAL — a ref leaves history, and a collage may lose a tile
    or a bubble may disappear entirely. That does replay the transcript, but it
    restores the scroll offset afterwards so the view stays where you left it.

The rule that keeps the in-place path honest: a tile reads its attach state
LIVE from its ref inside _restyle() and never captures it at build time. Every
piece of a tile that varies with that state — the eye's checked look, its
tooltip, the click handler, the dimming — is therefore correct after a plain
restyle, which is the same call Ctrl+scroll already makes.
"""

import os

from PyQt6.QtCore import Qt, QTimer
from PyQt6.QtGui import QPixmap
from PyQt6.QtWidgets import (QFrame, QGridLayout, QHBoxLayout, QLabel,
                             QVBoxLayout, QWidget)

from systema.common.logger import _make_logger

log = _make_logger("ImageBubbles")

# Thumbnails per row in a collage. Four across reads as a grid without the
# individual pictures becoming unrecognisable.
COLLAGE_COLS = 2
# Above this many, the extra ones collapse behind a "+N" tile.
COLLAGE_MAX_VISIBLE = 4


class ImageBubblesMixin:
    """Attach, render, detach and delete chat images."""

    # ── attaching ────────────────────────────────────────────────────────────

    def attach_images(self, paths, origin: str = 'user', annotation: str = ''):
        """THE entry point for putting images into the conversation.

        Caches each file, allocates its session-global number, appends a
        history entry, and renders the bubble IMMEDIATELY — there is no
        "attached, pending send" state any more. The picture is in the
        conversation the moment you pick it, which is what the old one-time
        send mechanism got wrong.

        `origin='agent'` is attach_image_to_chat(): the assistant showing the
        user something. Both origins feed ONE counter, so "image 3" is
        unambiguous regardless of who added it.
        """
        paths = [p for p in (paths or []) if p]
        if not paths:
            return []

        ai = getattr(self.controller, 'ai', None)
        if ai is None:
            return []

        rejected = self._reject_unsupported(paths)
        paths = [p for p in paths if p not in rejected]
        if not paths:
            return []

        entry = {'role': 'user', 'content': '',
                 '_images': []} if origin == 'user' else {
            'role': 'ui_event', '_type': 'image_attach',
            'content': f'Attached image(s)', '_annotation': annotation,
            '_images': []}

        refs = ai.attach_images(paths, origin=origin, entry=entry)
        if not refs:
            return []

        ai.conversation_history.append(entry)
        self.add_image_bubble(refs, origin=origin, annotation=annotation)
        self._warn_if_provider_is_blind(refs)
        try:
            self.controller._auto_save_session()
        except Exception:
            pass
        self._invalidate_token_estimate()
        return refs

    def _reject_unsupported(self, paths) -> set:
        """Formats the ACTIVE provider cannot read, reported once, up front.

        Failing at attach time with a named reason beats failing inside a
        base64 encoder mid-request, which is what used to happen.
        """
        from systema.engine import provider_contract as pc
        from systema.common import image_cache
        bad = set()
        try:
            caps = pc.image_capabilities(self.controller.ai._load_provider_module())
        except Exception:
            return bad
        for p in paths:
            ext = os.path.splitext(p)[1].lower().lstrip('.')
            if ext not in {e.lstrip('.') for e in image_cache.SUPPORTED_EXTS}:
                bad.add(p)
            elif caps.vision and not caps.accepts(ext):
                bad.add(p)
        if bad:
            names = ', '.join(os.path.basename(p) for p in sorted(bad))
            self.add_system_message(
                f"Could not attach {names} — the active provider does not "
                f"accept that image format.")
        return bad

    def _warn_if_provider_is_blind(self, refs):
        """Say so when images were attached to a text-only provider.

        They are still kept: the provider can be switched later and the
        pictures are already in the transcript. But the user should not be left
        wondering why the assistant never mentions them.
        """
        from systema.engine import provider_contract as pc
        try:
            caps = pc.image_capabilities(self.controller.ai._load_provider_module())
        except Exception:
            return
        if caps.vision:
            return
        ns = ', '.join(f"image {r['n']}" for r in refs)
        self.add_system_message(
            f"Attached {ns}, but the active provider has no vision support — "
            f"the assistant is told the image exists and that it cannot see it. "
            f"Switch to a vision-capable provider to have it looked at.")

    # ── rendering ────────────────────────────────────────────────────────────

    def add_image_bubble(self, refs, origin: str = 'user', annotation: str = '',
                         save_to_history: bool = False):
        """Render one bubble holding `refs`.

        `save_to_history` is False by default because attach_images() has
        already written the entry — this is also the reload path, where the
        entry obviously exists already.
        """
        refs = [r for r in (refs or []) if isinstance(r, dict)]
        if not refs:
            return None

        if origin == 'user':
            self._end_ai_turn_group()

        message_widget = QFrame()
        message_widget.setObjectName("chatRow")
        message_widget.setStyleSheet("QFrame#chatRow { background: transparent; }")
        row = QHBoxLayout(message_widget)
        row.setContentsMargins(16, 6, 16, 6)
        row.setSpacing(8)

        if origin == 'user':
            row.addStretch()

        bubble = QFrame()
        bubble.setObjectName("imgBubble")
        col = QVBoxLayout(bubble)
        col.setContentsMargins(8, 8, 8, 6)
        col.setSpacing(6)

        grid_host = QWidget()
        grid = QGridLayout(grid_host)
        grid.setContentsMargins(0, 0, 0, 0)
        grid.setSpacing(4)
        col.addWidget(grid_host)

        visible = refs[:COLLAGE_MAX_VISIBLE]
        hidden = refs[COLLAGE_MAX_VISIBLE:]
        cols = 1 if len(refs) == 1 else COLLAGE_COLS
        tiles = []
        for i, ref in enumerate(visible):
            extra = len(hidden) if (hidden and i == len(visible) - 1) else 0
            tile = self._build_image_tile(ref, single=(len(refs) == 1),
                                          more=extra)
            grid.addWidget(tile, i // cols, i % cols)
            tiles.append(tile)

        caption = QLabel()
        caption.setTextFormat(Qt.TextFormat.PlainText)
        caption.setWordWrap(True)
        col.addWidget(caption)

        def _restyle():
            t = self._t()
            z = self._card_z
            bubble.setStyleSheet(
                f"QFrame#imgBubble {{ background: {t['input_card']}; "
                f"border: 1px solid {t['input_card_border']}; "
                f"border-radius: 12px; }}")
            live = [r for r in refs if r.get('attached')]
            nums = ', '.join(str(r.get('n')) for r in refs)
            label = f"Image {nums}" if len(refs) == 1 else f"Images {nums}"
            state = (f"{len(live)} of {len(refs)} in context"
                     if len(live) != len(refs) else "in context")
            if not live:
                state = "detached"
            if annotation:
                label = f"{annotation} · {label}"
            caption.setText(f"{label} · {state}")
            caption.setStyleSheet(
                f"QLabel {{ color: #8B949E; font-size: {z(10)}px; "
                f"background: transparent; }}")
            for tl in tiles:
                fn = getattr(tl, '_restyle', None)
                if fn:
                    try:
                        fn()
                    except RuntimeError:
                        pass
        _restyle()

        bubble.setMaximumWidth(self._bubble_max_width())
        row.addWidget(bubble)
        if origin != 'user':
            row.addStretch()

        # `content` feeds the right-edge message navigator, which lists user
        # turns by their opening words — an image bubble has no text, so it
        # showed up as a blank rail entry you could not tell apart.
        nums = ', '.join(str(r.get('n')) for r in refs)
        self.message_widgets.append({
            'widget': message_widget,
            'role': 'user' if origin == 'user' else 'assistant',
            'content': (f"{annotation} · " if annotation else "")
                       + (f"Image {nums}" if len(refs) == 1 else f"Images {nums}"),
            'content_wrapper': bubble,
            'zoom_restyle': _restyle,
            '_image_refs': refs,
        })

        if origin == 'user':
            # Through the one door: an attached image is content arriving with
            # no user message and no assistant turn, which is exactly how it
            # used to slide in under a greeting banner that never dismissed.
            self._insert_chat_row(message_widget)
            self._animate_message_in(
                message_widget,
                on_settled=lambda: self.scroll_to_widget(message_widget))
        else:
            # Agent-side images belong INSIDE the merged assistant turn shell,
            # like every other card the assistant produces.
            self._ensure_ai_turn_group()
            self._insert_turn_segment(message_widget)
        return message_widget

    # Thumbnail size as a FRACTION of the bubble width, then clamped. Fractions
    # rather than fixed pixels because a picture has to read as one element of
    # a conversation, not as the conversation: a fixed 320px tile dwarfed the
    # text around it on a normal window and swallowed a portrait photo whole.
    SINGLE_FRAC, SINGLE_MIN, SINGLE_MAX = 0.40, 150, 260
    TILE_FRAC, TILE_MIN, TILE_MAX = 0.20, 90, 140
    # Aspect ratio past which an image counts as a strip (a wordmark logo is
    # about 5:1), and how much extra long-edge room it gets so it stays legible.
    THIN_RATIO, THIN_BOOST = 2.2, 1.5

    def _tile_side(self, single: bool) -> int:
        """Longest edge for a thumbnail, scaled to the bubble AND to the zoom.

        Runs inside _restyle so Ctrl+scroll resizes pictures exactly like it
        resizes text — a card that ignores the zoom silently stops matching
        everything around it.
        """
        try:
            bubble_w = max(240, self._bubble_max_width())
        except Exception:
            bubble_w = 620
        if single:
            base = min(self.SINGLE_MAX, max(self.SINGLE_MIN,
                                            bubble_w * self.SINGLE_FRAC))
        else:
            base = min(self.TILE_MAX, max(self.TILE_MIN,
                                          bubble_w * self.TILE_FRAC))
        return int(self._card_z(base))

    def _build_image_tile(self, ref, single: bool = False, more: int = 0):
        """One thumbnail with its two buttons.

        Detach is a TWIN button: it reads "detach from context" while the image
        is live and flips to "re-attach" once it is not, because those are the
        same affordance in two states rather than two controls.
        """
        from systema.common import image_cache
        from systema.ui.widgets.painted_icons import CloseButton, ContextEyeButton

        tile = QFrame()
        tile.setObjectName("imgTile")
        lay = QVBoxLayout(tile)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(0)

        holder = QWidget()
        thumb = QLabel(holder)
        thumb.setAlignment(Qt.AlignmentFlag.AlignCenter)
        thumb.setScaledContents(False)

        # NOTE: `attached` is deliberately NOT captured here. It changes while
        # this tile is alive, and _restyle() below re-reads it from the ref on
        # every pass — that is what lets a detach update in place instead of
        # rebuilding the transcript. `missing`/`source` are read once because
        # the cached bytes cannot change under a live tile.
        missing = not image_cache.exists(ref)
        source = None
        if not missing:
            pm = QPixmap(ref.get('path', ''))
            if not pm.isNull():
                source = pm
        if source is None:
            thumb.setText(f"Image {ref.get('n')}\nfile missing" if missing
                          else f"Image {ref.get('n')}")

        # Buttons ride the tile's top-right corner, on a translucent bar so
        # they stay readable over a pale image (a white logo swallowed bare
        # glyphs entirely).
        btn_row = QWidget(holder)
        btn_row.setObjectName("imgTileBtns")
        brl = QHBoxLayout(btn_row)
        brl.setContentsMargins(3, 2, 3, 2)
        brl.setSpacing(2)
        n = ref.get('n')
        # An EYE, struck through when detached — "the model can/cannot see
        # this" is exactly what detaching means. The old repeat/restart arrows
        # said nothing about visibility.
        det = ContextEyeButton(20)
        # ONE handler for both directions. Wiring detach-or-reattach at build
        # time would freeze the button into whichever state the image happened
        # to be in when the tile was created, and the tile now outlives that.
        det.clicked.connect(lambda _=False, k=n: self.toggle_image_attached(k))
        brl.addWidget(det)
        dele = CloseButton(20, tooltip=f"Delete image {n} permanently "
                                       f"(removes it from the chat and cache)",
                           pill=False)
        dele.clicked.connect(lambda _=False, k=n: self.delete_image(k))
        brl.addWidget(dele)
        btn_row.setStyleSheet(
            "QWidget#imgTileBtns { background: rgba(13,17,23,0.62);"
            " border-radius: 7px; }")
        btn_row.raise_()

        badge = QLabel(f"+{more}", holder) if more else None
        if badge is not None:
            badge.setAlignment(Qt.AlignmentFlag.AlignCenter)
            badge.raise_()

        lay.addWidget(holder)

        def _restyle():
            t = self._t()
            z = self._card_z
            side = self._tile_side(single)
            # LIVE read — this is the whole in-place detach mechanism.
            attached = bool(ref.get('attached'))
            det.setChecked(attached)
            det._fixed_tip = (
                f"Detach image {n} from the assistant's context "
                f"(stays here, reversible)" if attached
                else f"Re-attach image {n} to the assistant's context")
            det.setToolTip(det._fixed_tip)

            # Fit the picture inside `side` on its LONGEST edge, then make the
            # holder hug the result. Sizing the holder to a fixed square left a
            # portrait photo letterboxed in dead space, which is what made the
            # bubble look enormous next to the text.
            if source is not None:
                box_w, box_h = side, side
                sw, sh = max(1, source.width()), max(1, source.height())
                ratio = max(sw / sh, sh / sw)
                if ratio >= self.THIN_RATIO:
                    # A wordmark logo is ~5:1. Fitting its LONG edge to `side`
                    # leaves a strip barely tall enough to read, with nowhere
                    # for the buttons to sit — so an extreme aspect gets extra
                    # room on its long edge instead.
                    if sw >= sh:
                        box_w = int(side * self.THIN_BOOST)
                    else:
                        box_h = int(side * self.THIN_BOOST)
                scaled = source.scaled(
                    box_w, box_h, Qt.AspectRatioMode.KeepAspectRatio,
                    Qt.TransformationMode.SmoothTransformation)
                thumb.setPixmap(scaled)
                w, h = scaled.width(), scaled.height()
            else:
                w = h = max(80, side // 2)

            # The buttons must always have somewhere to sit, even on a strip.
            btn_row.adjustSize()
            w = max(w, btn_row.width() + 12)
            h = max(h, btn_row.height() + 10)

            holder.setFixedSize(w, h)
            thumb.setGeometry(0, 0, w, h)
            btn_row.move(max(0, w - btn_row.width() - 5), 5)
            btn_row.raise_()
            if badge is not None:
                badge.setGeometry(0, 0, w, h)

            tile.setStyleSheet(
                f"QFrame#imgTile {{ background: {t['base']}; "
                f"border: 1px solid {t['input_card_border']}; "
                f"border-radius: 8px; }}")
            # A detached picture is DIMMED rather than hidden: you need to see
            # what you detached in order to decide to put it back.
            thumb.setStyleSheet(
                f"QLabel {{ color: #8B949E; font-size: {z(10)}px; "
                f"background: transparent; border-radius: 8px; }}")
            thumb.setGraphicsEffect(None)
            if not attached:
                from PyQt6.QtWidgets import QGraphicsOpacityEffect
                eff = QGraphicsOpacityEffect(thumb)
                eff.setOpacity(0.35)
                thumb.setGraphicsEffect(eff)
            if badge is not None:
                badge.setStyleSheet(
                    f"QLabel {{ color: #E6EDF3; font-size: {z(20)}px; "
                    f"font-weight: 600; background: rgba(0,0,0,0.55); "
                    f"border-radius: 8px; }}")
        tile._restyle = _restyle
        _restyle()
        return tile

    # ── state changes ────────────────────────────────────────────────────────

    def _history(self):
        return getattr(getattr(self.controller, 'ai', None),
                       'conversation_history', [])

    def _save_and_retally(self):
        """Persist the session and refresh the token readout."""
        try:
            self.controller._auto_save_session()
        except Exception:
            pass
        self._invalidate_token_estimate()

    def _restyle_image_bubbles(self, ns=None) -> int:
        """Repaint image bubbles IN PLACE. No teardown, no scroll movement.

        `ns` is a set of image numbers, or None for every image bubble. Each
        bubble's registered `zoom_restyle` is the same closure Ctrl+scroll
        calls, and it cascades into its tiles — which read their attach state
        live — so one call is enough to make the eye, the dimming and the
        caption agree with the refs again.
        """
        want = None
        if ns is not None:
            want = set()
            for x in ns:
                try:
                    want.add(int(x))
                except (TypeError, ValueError):
                    continue
        done = 0
        for meta in getattr(self, 'message_widgets', []) or []:
            refs = meta.get('_image_refs')
            if not refs:
                continue
            if want is not None:
                shown = set()
                for r in refs:
                    try:
                        shown.add(int(r.get('n')))
                    except (TypeError, ValueError):
                        continue
                if not (shown & want):
                    continue
            fn = meta.get('zoom_restyle')
            if fn is None:
                continue
            try:
                fn()
                done += 1
            except RuntimeError:
                pass    # bubble already destroyed (session switched under us)
        return done

    def _after_attach_change(self, ns=None):
        """Detach / re-attach — a flag flip, so nothing is rebuilt.

        This is the fix for "detaching an image moves the whole chat": the old
        path replayed the entire transcript, which destroyed and recreated every
        widget in the view. Scroll position shifted, bubbles were re-laid out,
        and the picture next to the one you just clicked had moved by the time
        you reached for it.
        """
        self._restyle_image_bubbles(ns)
        self._save_and_retally()

    def _after_structural_change(self):
        """Delete — a ref actually left history, so the transcript is replayed.

        Unavoidable (a collage loses a tile, a bubble may vanish entirely), but
        the scroll offset is restored so the view still does not jump.
        """
        self._save_and_retally()
        self._replay_keeping_scroll()

    def _replay_keeping_scroll(self):
        """render_loaded_messages() with the viewport put back where it was."""
        area = getattr(self, 'chat_scroll_area', None)
        bar = None
        try:
            bar = area.verticalScrollBar() if area is not None else None
        except (RuntimeError, AttributeError):
            bar = None
        keep = bar.value() if bar is not None else 0
        # Someone pinned to the bottom wants to stay pinned, not to be parked at
        # whatever absolute offset the bottom used to be at.
        pinned = bar is not None and keep >= bar.maximum() - 4

        self.render_loaded_messages()

        if bar is None:
            return

        def _restore():
            try:
                target = bar.maximum() if pinned else min(keep, bar.maximum())
                from systema.ui.widgets.smooth_scroll import scroller_for
                s = scroller_for(area)
                if s is not None:
                    s.scroll_to(target, immediate=True)
                else:
                    bar.setValue(target)
            except (RuntimeError, AttributeError):
                pass

        _restore()
        # The replayed bubbles have not been laid out yet on this tick, so
        # bar.maximum() is still the OLD content height and clamps the restore
        # short on a long transcript. Settle again once layout has run.
        QTimer.singleShot(0, _restore)

    def toggle_image_attached(self, n):
        """The eye button's ONE handler — flips whichever way this image is."""
        from systema.common import image_refs
        _entry, ref = image_refs.find(self._history(), n)
        if ref is None:
            return
        if ref.get('attached'):
            self.detach_image(n)
        else:
            self.reattach_image(n)
        # The eye is a CHECKABLE button: the click already toggled its own look
        # before this ran. If the request changed nothing, that look is a lie —
        # so resync it unconditionally.
        self._restyle_image_bubbles({n})

    def detach_image(self, n):
        """Drop image `n` from the model's context, reversibly."""
        from systema.common import image_refs
        if not image_refs.set_attached(self._history(), n, False):
            return
        log.info(f"[detach_image] image {n} detached from context")
        self._after_attach_change({n})

    def reattach_image(self, n):
        from systema.common import image_refs
        if not image_refs.set_attached(self._history(), n, True):
            return
        log.info(f"[reattach_image] image {n} back in context")
        self._after_attach_change({n})

    def delete_image(self, n, confirm: bool = True):
        """Remove image `n` from the chat AND the cache. Irreversible."""
        from PyQt6.QtWidgets import QMessageBox
        from systema.common import image_cache, image_refs

        hist = self._history()
        _entry, ref = image_refs.find(hist, n)
        if ref is None:
            return
        if confirm:
            ret = QMessageBox.question(
                self, "Delete image",
                f"Delete image {n} ({ref.get('name') or 'unnamed'}) from this "
                f"chat and from the cache?\n\n"
                f"This cannot be undone, and the number {n} will not be "
                f"reused.\n\nYour original file on disk is not touched.",
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No)
            if ret != QMessageBox.StandardButton.Yes:
                return
        removed = image_refs.remove(hist, n)
        if removed is not None:
            image_cache.discard(removed,
                                still_referenced=image_refs.all_refs(hist))
        log.info(f"[delete_image] image {n} deleted; number retired")
        self._after_structural_change()

    def set_all_images_attached(self, attached: bool):
        """Bulk detach / re-attach every image in the session."""
        from systema.common import image_refs
        changed = image_refs.set_all_attached(self._history(), attached)
        if not changed:
            return 0
        self._after_attach_change()     # every image bubble, still no rebuild
        return changed

    def delete_all_images(self, confirm: bool = True):
        from PyQt6.QtWidgets import QMessageBox
        from systema.common import image_cache, image_refs

        hist = self._history()
        refs = image_refs.all_refs(hist)
        if not refs:
            return 0
        if confirm:
            ret = QMessageBox.question(
                self, "Delete all images",
                f"Delete all {len(refs)} image(s) from this chat and from the "
                f"cache?\n\nThis cannot be undone. Your original files on disk "
                f"are not touched.",
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No)
            if ret != QMessageBox.StandardButton.Yes:
                return 0
        for ref in list(refs):
            removed = image_refs.remove(hist, ref.get('n'))
            if removed is not None:
                image_cache.discard(removed,
                                    still_referenced=image_refs.all_refs(hist))
        self._after_structural_change()
        return len(refs)

    def session_image_refs(self):
        """Every ImageRef in this session, in attach order."""
        from systema.common import image_refs
        return image_refs.all_refs(self._history())

    # ── compatibility shims ──────────────────────────────────────────────────
    # The Android bridge and the controller call these by name. They now route
    # into the new pipeline instead of the retired pinned-overlay widgets.

    def _show_image_preview(self, path):
        """Legacy name — an attachment arriving from a drop/paste/phone."""
        self.attach_images([path])

    def clear_pinned_images(self):
        """Legacy name. Images live in the session now, so clearing the chat
        already clears them; nothing to tear down separately."""
        return None
