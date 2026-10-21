"""
Dialogs for the TUI: opening them, their results, and the tmux zoom around them.

Every dialog (a widget with the "modal" class) zooms the TUI pane while it
is open (_dialog_will_open) and unzooms when the last one closes
(_dialog_did_close). Here: the new-agent, rename and billing-code dialogs,
billing codes, column config, new-agent defaults, skills, tmux toggle key,
passthru keys, instruction history (#376) and the command palette (agents,
tags, sorts, commands: #420, #357, #482, #487).
"""

from __future__ import annotations

import threading
from typing import TYPE_CHECKING

from textual.css.query import NoMatches
from textual.widgets import Static

from ..launcher import AgentLauncher
from ..tui_widgets import (
    ColumnHeader,
    CommandBar,
    CommandPalette,
    InstructionHistoryModal,
    JumpCandidate,
    NewAgentDefaultsModal,
    NewAgentModal,
    PassthruConfigModal,
    AgentVisibilityModal,
    BillingCodeModal,
    BillingCodesModal,
    RenameAgentModal,
    SessionSummary,
    SkillsModal,
    SummaryConfigModal,
    SummaryPromptLab,
    NamingLab,
    TmuxConfigModal,
)

if TYPE_CHECKING:  # pragma: no cover
    from ..session_manager import Session


class DialogsMixin:
    """Mixin providing dialogs and the command palette for SupervisorTUI."""

    def _any_modal_visible(self) -> bool:
        """Check if any modal dialog is currently visible.

        Queries for widgets with both 'modal' and 'visible' CSS classes.
        New modals automatically get focus protection and key blocking
        by adding classes="modal" in compose().
        """
        return bool(self.query(".modal.visible"))

    def _any_dialog_visible(self) -> bool:
        """Check if any dialog (modal or help overlay) is visible."""
        if self._any_modal_visible():
            return True
        try:
            from ..tui_widgets import HelpOverlay
            help_overlay = self.query_one("#help-overlay", HelpOverlay)
            if help_overlay.has_class("visible"):
                return True
        except Exception:
            pass
        return False

    def _dialog_will_open(self) -> None:
        """Zoom the tmux monitor pane when a dialog opens.

        Uses tmux's pane zoom to temporarily hide the bottom (terminal)
        pane, giving the TUI the full window for rendering dialogs.
        """
        self.split.zoom()

    def _dialog_did_close(self) -> None:
        """Unzoom the tmux monitor pane when all dialogs are closed."""
        # Don't unzoom if another dialog is still visible
        if self._any_dialog_visible():
            return
        # Don't unzoom if sister view or jobs view is holding the zoom open
        if self._sister_zoom_active:
            return
        if self.tui_mode == "jobs":
            return
        self.split.unzoom()

    def on_new_agent_modal_launch_requested(self, message: NewAgentModal.LaunchRequested) -> None:
        """Handle launch from the unified new-agent modal."""
        import logging
        _log = logging.getLogger("overcode.tui.new_agent")

        name = message.name
        if not name or len(name) > 50 or ' ' in name:
            self.notify("Invalid agent name", severity="error")
            self._dialog_did_close()
            return

        if message.is_remote:
            # Launch on remote sister
            from ..config import get_sister_by_name
            sister_config = get_sister_by_name(message.host)
            if not sister_config:
                self.notify(f"Sister '{message.host}' not found", severity="error")
                self._dialog_did_close()
                return

            # Check reachability
            for state in self._sister_poller.get_sister_states():
                if state.name == message.host and not state.reachable:
                    self.notify(f"Sister '{message.host}' is unreachable", severity="warning")
                    self._dialog_did_close()
                    return

            permissions = "bypass" if message.bypass_permissions else "normal"
            if message.extra_cli_args:
                self.notify("Backend args are not yet supported for remote launches — ignored", severity="warning")
            if message.billing_code:
                self.notify(
                    f"Billing codes are not yet supported for remote launches — '{name}' "
                    f"is billed to its name (overcode rebill on {message.host} moves it)",
                    severity="warning",
                )
            from ..backends import DEFAULT_BACKEND
            if message.backend and message.backend != DEFAULT_BACKEND:
                self.notify(
                    f"Backend '{message.backend}' is not yet supported for remote "
                    f"launches — the sister will use {DEFAULT_BACKEND}",
                    severity="warning",
                )
            try:
                result = self._sister_controller.launch_agent(
                    sister_url=sister_config["url"],
                    api_key=sister_config.get("api_key", ""),
                    directory=message.directory or ".",
                    name=name,
                    permissions=permissions,
                    provider=message.provider,
                )
            except Exception as e:
                _log.error("Remote launch exception: %s", e, exc_info=True)
                self.notify(f"Remote launch failed: {e}", severity="error")
                self._dialog_did_close()
                return

            if result.ok:
                self.notify(f"Remote agent '{name}' launched on {message.host}", severity="information")
            else:
                self.notify(f"Remote launch failed: {result.error}", severity="error")
        else:
            # Launch locally
            from ..exceptions import LaunchError
            from ..name_source import DEFAULT, HUMAN
            launcher = AgentLauncher(
                tmux_session=self.tmux_session,
                session_manager=self.session_manager,
            )
            try:
                launcher.launch(
                    name=name,
                    start_directory=message.directory,
                    dangerously_skip_permissions=message.bypass_permissions,
                    agent_teams=message.agent_teams,
                    agent_persona=message.agent_persona,
                    provider=message.provider,
                    backend=message.backend,
                    wrapper=message.wrapper,
                    extra_cli_args=message.extra_cli_args or None,
                    skill_profile=message.skill_profile,
                    billing_code=message.billing_code,
                    name_source=DEFAULT if message.name_auto else HUMAN,
                )
                parts = [f"Created agent: {name}"]
                if message.wrapper:
                    parts.append(f"wrapper: {message.wrapper}")
                if message.skill_profile and message.skill_profile != "none":
                    parts.append(f"skills: {message.skill_profile}")
                if message.billing_code:
                    parts.append(f"billed to: {message.billing_code}")
                self.notify(" ".join(parts), severity="information")
                self.refresh_sessions()
            except LaunchError as e:
                self.notify(str(e), severity="error")
            except Exception as e:
                self.notify(f"Failed to create agent: {e}", severity="error")

        self._dialog_did_close()

    def on_new_agent_modal_cancelled(self, message: NewAgentModal.Cancelled) -> None:
        """Handle cancel from new-agent modal."""
        self._dialog_did_close()

    def on_rename_agent_modal_rename_requested(
        self, message: RenameAgentModal.RenameRequested,
    ) -> None:
        """Rename the agent the dialog was opened for (#478).

        Off the UI thread: tmux and sessions.json writes, and for a pre-0.6
        agent a stop, a wait (seconds) and a relaunch.
        """
        self._dialog_did_close()
        session = self.session_manager.get_session(message.session_id)
        if session is None:
            self.notify(f"Agent '{message.old_name}' no longer exists", severity="error")
            return
        source = getattr(message, "name_source", "human")
        if message.new_name == session.name:
            self._set_name_source(session, source)
            return
        self.notify(f"Renaming '{message.old_name}' → '{message.new_name}'...")
        threading.Thread(
            target=self._run_rename,
            args=(session, message.new_name, message.force, source),
            daemon=True,
        ).start()

    def _set_name_source(self, session: "Session", source: str) -> None:
        """Hand the name to overcode's namer ("default": a placeholder it may
        replace, see namer.hand_to_namer) or keep it ("human"), name unchanged (#542)."""
        if source in ("default", "auto"):
            from ..config import get_naming_config
            from ..namer import hand_to_namer
            hand_to_namer(self.session_manager, session, self.tmux_session)
            off = "" if get_naming_config()["enabled"] else " (auto-naming is off: / Auto-naming)"
            self.notify(f"overcode may rename '{session.name}' now{off}", severity="information")
        else:
            self.session_manager.update_session(session.id, name_source=source)
            self.notify(f"'{session.name}' keeps its name", severity="information")
        self.refresh_sessions()

    def _run_rename(self, session: "Session", new_name: str, force: bool,
                    name_source: str = "human") -> None:
        """Worker thread for a TUI rename; reports back through notify."""
        from ..exceptions import AgentBusyError, InvalidSessionNameError
        from ..launcher import AgentLauncher

        old_name = session.name
        launcher = AgentLauncher(self.tmux_session, session_manager=self.session_manager)
        try:
            renamed = launcher.rename(session, new_name, force=force, source=name_source)
        except AgentBusyError as e:
            state = "could not be checked" if e.status == "unknown" else f"is busy ({e.status})"
            self.call_from_thread(
                self.notify,
                f"'{old_name}' {state} — not renamed. Try when it is idle, or turn force on.",
                severity="warning",
            )
            return
        except (InvalidSessionNameError, ValueError) as e:
            self.call_from_thread(self.notify, f"Rename failed: {e}", severity="error")
            return
        except Exception as e:  # never let a worker die silently
            self.call_from_thread(self.notify, f"Rename failed: {e}", severity="error")
            return

        current = self.session_manager.get_session(session.id)
        if current is not None and current.name == new_name:
            # A person chose it: locked, unless they handed it to overcode
            if name_source in ("default", "auto"):
                from ..namer import hand_to_namer
                hand_to_namer(self.session_manager, current, self.tmux_session)
            else:
                self.session_manager.update_session(session.id, name_source=name_source)
        if renamed:
            self.call_from_thread(
                self.notify, f"Renamed '{old_name}' → '{new_name}'", severity="information",
            )
        elif current is not None and current.name == new_name:
            self.call_from_thread(
                self.notify,
                f"Renamed to '{new_name}', but the relaunch failed — press R to restart it",
                severity="warning",
            )
        else:
            self.call_from_thread(
                self.notify, f"tmux refused the rename; '{old_name}' keeps its name",
                severity="error",
            )
        self.call_from_thread(self.refresh_sessions)

    def on_billing_code_modal_rebill_requested(
        self, message: BillingCodeModal.RebillRequested,
    ) -> None:
        """Bill the agent's new spend to the chosen code: ``overcode rebill``'s path."""
        self._dialog_did_close()
        try:
            old = self.session_manager.rebill(message.session_id, message.new_code)
        except ValueError as e:
            self.notify(str(e), severity="error")
            return
        if old is None:
            self.notify(f"Agent '{message.name}' no longer exists", severity="error")
            return
        # The code the name resolved to (a previous name reads as the current one)
        after = self.session_manager.get_session(message.session_id)
        new = getattr(after, "billing_code", None) or message.new_code.strip()
        if old == new:
            self.notify(f"'{message.name}' is already billed to {old}")
            return
        self.notify(f"'{message.name}' billed to {new} from now on "
                    f"(its spend so far stays on {old})")
        self.refresh_sessions()

    def on_billing_code_modal_cancelled(self, message: BillingCodeModal.Cancelled) -> None:
        """Handle cancel from the billing-code modal."""
        self._dialog_did_close()

    def action_open_billing_codes(self) -> None:
        """Open the billing codes dialog (m; palette: Billing codes & spend…); the ledger is read in a worker."""
        try:
            modal = self.query_one("#billing-codes-modal", BillingCodesModal)
        except NoMatches:
            return
        self._dialog_will_open()
        modal.show(app_ref=self)
        self._load_billing_codes()

    def _load_billing_codes(self, message: str | None = None, style: str | None = None) -> None:
        manager = self.session_manager

        def work() -> None:
            from ..config import get_billing_defaults
            from ..tui_widgets.billing_codes_modal import load_code_rows

            try:
                rows = load_code_rows(manager.ledger)
                settings = get_billing_defaults()
                default_id = manager.ledger.codes.find(settings["default_code"])
            except Exception as e:  # a broken ledger must not take the TUI down
                self.call_from_thread(self._billing_codes_modal_say,
                                      f"could not read the ledger: {e}", "error")
                return
            self.call_from_thread(self._billing_codes_loaded, rows, message, style,
                                  settings, default_id)

        self.run_worker(work, thread=True, group="billing_codes", exclusive=True)

    def _billing_codes_loaded(self, rows, message, style, settings=None, default_id=None) -> None:
        from ..tui_widgets import dialog_style as ds

        modal = self.query_one("#billing-codes-modal", BillingCodesModal)
        modal.set_rows(rows, message, style or ds.MUTED, settings=settings,
                       default_id=default_id)

    def _billing_codes_modal_say(self, text: str, severity: str = "information") -> None:
        from ..tui_widgets import dialog_style as ds

        style = {"error": ds.ERROR, "warning": ds.WARN}.get(severity, ds.MUTED)
        try:
            self.query_one("#billing-codes-modal", BillingCodesModal).say(text, style)
        except NoMatches:
            pass

    def _billing_codes_changed(self, manager) -> None:
        """After a rename or merge: the agents' name copies now, and the engine told.

        ``overcode billing rename``'s refresh (the daemon's housekeeping
        would, later); the activity signal wakes the engine for a full tick,
        which publishes the new names to every view.
        """
        from ..layout_migration import fill_billing_codes
        from ..settings import signal_activity

        try:
            fill_billing_codes(manager)
        except Exception:  # the registry is right; the daemon catches the copies up
            pass
        signal_activity(self.tmux_session)

    def on_billing_codes_modal_rename_requested(
        self, message: BillingCodesModal.RenameRequested,
    ) -> None:
        """Rename a code: ``overcode billing rename``'s path, off the UI thread."""
        manager = self.session_manager

        def work() -> None:
            from ..codes import CodeError

            codes = manager.ledger.codes
            try:
                code_id, _ = codes.rename(message.code_id, message.new)
            except CodeError as e:
                self.call_from_thread(self._billing_codes_modal_say, str(e), "error")
                return
            entry = codes.get(code_id) or {}
            self._billing_codes_changed(manager)
            self.call_from_thread(self._billing_code_renamed, code_id, entry, message.old)

        self.run_worker(work, thread=True, group="billing_codes_edit", exclusive=False)

    def _billing_code_renamed(self, code_id: str, entry: dict, old: str) -> None:
        from ..tui_widgets import dialog_style as ds

        modal = self.query_one("#billing-codes-modal", BillingCodesModal)
        name = entry.get("name", "?")
        modal.renamed(code_id, name, list(entry.get("previous_names") or ()))
        modal.say(f"✓ {old} is now {name} in every report", ds.STATE_ON)
        self.refresh_sessions()

    def on_billing_codes_modal_merge_requested(
        self, message: BillingCodesModal.MergeRequested,
    ) -> None:
        """Fold codes into another: ``overcode billing merge``'s path (one write); the figures are read again."""
        from ..tui_widgets import dialog_style as ds

        manager = self.session_manager

        def work() -> None:
            from ..codes import CodeError

            try:
                _, folded = manager.ledger.codes.merge_many(message.code_ids, message.into_id)
            except CodeError as e:
                self.call_from_thread(self._billing_codes_modal_say, str(e), "error")
                return
            self._billing_codes_changed(manager)
            what = folded[0][1] if len(folded) == 1 else f"{len(folded)} codes"
            self.call_from_thread(self._load_billing_codes,
                                  f"✓ merged {what} into {message.into_name} · undo: m then u",
                                  ds.STATE_ON)
            self.call_from_thread(self.refresh_sessions)

        self.run_worker(work, thread=True, group="billing_codes_edit", exclusive=False)

    def on_billing_codes_modal_collapse_requested(
        self, message: BillingCodesModal.CollapseRequested,
    ) -> None:
        """Collapse codes into the default one: ``overcode billing collapse``'s path."""
        from ..tui_widgets import dialog_style as ds

        manager = self.session_manager

        def work() -> None:
            from ..codes import CodeError
            from ..config import get_billing_defaults

            default = get_billing_defaults()["default_code"]
            try:
                _, folded = manager.ledger.codes.collapse(message.code_ids, default)
            except CodeError as e:
                self.call_from_thread(self._billing_codes_modal_say, str(e), "error")
                return
            self._billing_codes_changed(manager)
            what = folded[0][1] if len(folded) == 1 else f"{len(folded)} codes"
            self.call_from_thread(self._load_billing_codes,
                                  f"✓ {what} collapsed into {default} · m, then u, undoes one",
                                  ds.STATE_ON)
            self.call_from_thread(self.refresh_sessions)

        self.run_worker(work, thread=True, group="billing_codes_edit", exclusive=False)

    def on_billing_codes_modal_unmerge_requested(
        self, message: BillingCodesModal.UnmergeRequested,
    ) -> None:
        """Undo a merge: ``overcode billing unmerge``'s path (live agents stay put)."""
        from ..tui_widgets import dialog_style as ds

        manager = self.session_manager

        def work() -> None:
            from ..codes import CodeError

            try:
                _, name, into = manager.ledger.codes.unmerge(message.code_id)
            except CodeError as e:
                self.call_from_thread(self._billing_codes_modal_say, str(e), "error")
                return
            self._billing_codes_changed(manager)
            self.call_from_thread(self._load_billing_codes,
                                  f"✓ {name} reports its own spend again; its agents "
                                  f"stay on {into}", ds.STATE_ON)
            self.call_from_thread(self.refresh_sessions)

        self.run_worker(work, thread=True, group="billing_codes_edit", exclusive=False)

    def on_billing_codes_modal_settings_requested(
        self, message: BillingCodesModal.SettingsRequested,
    ) -> None:
        """``s`` in the billing codes dialog: New agent defaults on its billing rows, then back."""
        self._defaults_return_to_billing = True
        self.action_open_new_agent_defaults(at_billing=True)

    def on_billing_codes_modal_closed(self, message: BillingCodesModal.Closed) -> None:
        self._dialog_did_close()

    def on_rename_agent_modal_cancelled(self, message: RenameAgentModal.Cancelled) -> None:
        """Handle cancel from the rename modal."""
        self._dialog_did_close()

    def on_summary_prompt_lab_closed(self, message: SummaryPromptLab.Closed) -> None:
        """The prompt lab closed (#491)."""
        self._dialog_did_close()

    def action_open_naming_lab(self) -> None:
        """Open the naming lab: the naming advice over the whole fleet (#542 §9)."""
        from ..config import get_naming_config
        from ..namer import make_client
        from ..naming_lab import ARCHIVED_IN_LAB, LabSpend, NamingLabRunner, load_archived, load_live

        try:
            lab = self.query_one("#naming-lab", NamingLab)
        except NoMatches:
            return
        cfg = get_naming_config()
        sm = self.session_manager
        # Results are kept between opens; the config is read again each time
        runner = getattr(self, "_naming_lab_runner", None)
        if runner is None:
            runner = self._naming_lab_runner = NamingLabRunner(make_client=make_client)
        runner.make_client = lambda: make_client(cfg.get("provider"))
        # Lab calls are the namer's spend: the ledger and the daily cap
        runner.spend = LabSpend(self.tmux_session, getattr(sm, "ledger", None), cfg)

        def pane(session) -> str:
            return self._summarizer._capture_pane(session.tmux_window) or ""

        def load(view: str) -> list:
            if view == "archived":
                return load_archived(sm, self.tmux_session, cfg, ARCHIVED_IN_LAB)
            return load_live(sm, self.tmux_session, cfg, pane=pane)

        def apply(agent, name: str) -> None:
            def work() -> None:
                self._run_rename(agent.session, name, False, "human")
                current = sm.get_session(agent.id)
                if current is not None:
                    self.call_from_thread(lab.renamed, current)
            threading.Thread(target=work, daemon=True).start()

        def apply_all(plan: list) -> None:
            def work() -> None:
                self._rename_many(plan, lab)
            threading.Thread(target=work, daemon=True).start()

        try:
            available = bool(make_client(cfg.get("provider")).available)
        except Exception:
            available = False
        self._dialog_will_open()
        from ..config import describe_auto_naming
        lab.show(runner=runner, load=load, available=available, apply=apply,
                 apply_all=apply_all, auto_naming=describe_auto_naming(cfg),
                 config_advice=cfg.get("advice") or "", max_length=cfg.get("max_length", 18),
                 app_ref=self)

    def _rename_many(self, plan: list, lab) -> None:
        """Worker thread for the lab's A (apply every pick): rename each (agent, name) in
        turn as a human choice, then one toast for the lot."""
        from ..launcher import AgentLauncher

        launcher = AgentLauncher(self.tmux_session, session_manager=self.session_manager)
        done, failed = 0, []
        for agent, name in plan:
            session = self.session_manager.get_session(agent.id)
            if session is None:
                failed.append(f"{agent.name}: gone")
                continue
            try:
                if launcher.rename(session, name, source="human"):
                    done += 1
                else:
                    failed.append(f"{session.name}: tmux refused")
            except Exception as e:
                failed.append(f"{session.name}: {e}")
            current = self.session_manager.get_session(agent.id)
            if current is not None:
                self.call_from_thread(lab.renamed, current)
        message = f"Renamed {done} of {len(plan)} agents"
        if failed:
            message += " — not renamed: " + "; ".join(failed[:3]) + (
                f" (+{len(failed) - 3} more)" if len(failed) > 3 else "")
        self.call_from_thread(self.notify, message,
                              severity="warning" if failed else "information")

    def on_naming_lab_closed(self, message: NamingLab.Closed) -> None:
        """The naming lab closed (#542)."""
        self._dialog_did_close()

    def action_open_column_config(self) -> None:
        """Open the column configuration modal (#178).

        Edits the currently active detail level. Full shows all columns
        and cannot be configured.
        """
        current_level = self.SUMMARY_LEVELS[self.summary_level_index]
        if current_level == "full":
            self.notify("Full shows all columns", severity="information")
            return
        try:
            modal = self.query_one("#summary-config-modal", SummaryConfigModal)
            overrides = self._prefs.column_config.get(current_level, {})
            self._live_column_overrides = dict(overrides)
            self._dialog_will_open()
            modal.show(current_level, overrides, self)
        except NoMatches:
            pass

    def on_summary_config_modal_config_changed(self, message: SummaryConfigModal.ConfigChanged) -> None:
        """Handle column configuration changes from modal (#178)."""
        self._apply_column_config(message.level, message.overrides)
        self.notify(f"Column config saved for {message.level}", severity="information")
        self._dialog_did_close()

    def _apply_column_config(self, level: str, overrides: dict) -> None:
        """Save one level's column overrides and redraw with them (C dialog, overcode view)."""
        if overrides:
            self._prefs.column_config[level] = overrides
        elif level in self._prefs.column_config:
            # Empty overrides after reset — remove the key
            del self._prefs.column_config[level]
        self._save_prefs()

        # Push updated overrides to all widgets
        current_level = self.SUMMARY_LEVELS[self.summary_level_index]
        new_overrides = self._prefs.column_config.get(current_level, {})
        for widget in self.query(SessionSummary):
            widget.column_overrides = new_overrides
            widget.refresh()
        self._live_column_overrides = None
        self._column_widths_dirty = True
        self._recompute_cell_column_widths()

    def on_summary_config_modal_cancelled(self, message: SummaryConfigModal.Cancelled) -> None:
        """Handle modal cancellation (#178)."""
        # Restore original overrides
        current_level = self.SUMMARY_LEVELS[self.summary_level_index]
        original_overrides = self._prefs.column_config.get(current_level, {})
        for widget in self.query(SessionSummary):
            widget.column_overrides = original_overrides
            widget.refresh()
        self._live_column_overrides = None
        self._column_widths_dirty = True
        self._recompute_cell_column_widths()
        self._dialog_did_close()

    def action_open_new_agent_defaults(self, at_billing: bool = False) -> None:
        """Open the new-agent defaults modal (``at_billing``: on its billing rows)."""
        from ..config import get_billing_defaults, get_new_agent_defaults
        try:
            modal = self.query_one("#new-agent-defaults-modal", NewAgentDefaultsModal)
            self._dialog_will_open()
            modal.show(get_new_agent_defaults(), self, get_billing_defaults(),
                       at_billing=at_billing)
        except NoMatches:
            pass

    def on_new_agent_defaults_modal_defaults_changed(self, message: NewAgentDefaultsModal.DefaultsChanged) -> None:
        """Handle new-agent defaults applied from modal."""
        from ..config import save_billing_defaults, save_new_agent_defaults
        save_new_agent_defaults(message.defaults)
        if message.billing is not None:
            save_billing_defaults(message.billing["new_agent_code"],
                                  message.billing["default_code"])
        self.notify("Defaults saved", severity="information")
        self._defaults_closed()

    def on_new_agent_defaults_modal_cancelled(self, message: NewAgentDefaultsModal.Cancelled) -> None:
        """Handle new-agent defaults modal cancellation."""
        self._defaults_closed()

    def _defaults_closed(self) -> None:
        """Back to the billing codes dialog when ``s`` there opened the defaults."""
        if getattr(self, "_defaults_return_to_billing", False):
            self._defaults_return_to_billing = False
            self.action_open_billing_codes()
        self._dialog_did_close()

    def action_open_skills(self) -> None:
        """Open the skill profiles dialog (#499), on the focused agent's profile."""
        from pathlib import Path
        from ..skill_library import skill_usage
        focused = self._get_focused_widget()
        session = focused.session if focused else None
        try:
            modal = self.query_one("#skills-modal", SkillsModal)
        except NoMatches:
            return
        self._dialog_will_open()
        modal.show(
            usage=skill_usage(self.session_manager.list_sessions()),
            profile=getattr(session, "skill_profile", None),
            folder=(session.start_directory if session else None) or str(Path.cwd()),
            app_ref=self,
        )

    def on_skills_modal_closed(self, message: SkillsModal.Closed) -> None:
        self.refresh_sessions()
        self._dialog_did_close()

    def action_open_tmux_config(self) -> None:
        """Open the tmux toggle-key modal (#442)."""
        try:
            modal = self.query_one("#tmux-config-modal", TmuxConfigModal)
            self._dialog_will_open()
            modal.show(self)
        except NoMatches:
            pass

    def on_tmux_config_modal_toggle_key_changed(
        self, message: TmuxConfigModal.ToggleKeyChanged
    ) -> None:
        """Handle new toggle key selected from modal (#442)."""
        if message.reinstalled:
            self.notify(
                f"Toggle key set to {message.label} (tmux bindings reinstalled)",
                severity="information",
            )
        else:
            self.notify(
                f"Toggle key set to {message.label} — run 'overcode tmux' to install bindings",
                severity="information",
            )
        # Refresh the TERMINAL ACTIVE banner so it shows the new key label
        try:
            banner = self.query_one("#terminal-active-banner", Static)
            banner.update(self._terminal_active_banner_text())
        except NoMatches:
            pass
        self._update_footer()  # it names the toggle key in split mode
        self._dialog_did_close()

    def on_tmux_config_modal_cancelled(self, message: TmuxConfigModal.Cancelled) -> None:
        """Handle tmux config modal cancellation (#442)."""
        self._dialog_did_close()

    def action_open_passthru_config(self) -> None:
        """Open the passthru-key configuration modal (#446)."""
        try:
            modal = self.query_one("#passthru-config-modal", PassthruConfigModal)
            self._dialog_will_open()
            modal.show(self)
        except NoMatches:
            pass

    def on_passthru_config_modal_saved(
        self, message: PassthruConfigModal.Saved
    ) -> None:
        """Handle saved passthru-key config (#446)."""
        enabled = sorted(message.mapping.keys())
        self.notify(
            f"Passthru keys saved: {', '.join(enabled) if enabled else '(none enabled)'}",
            severity="information",
        )
        self._dialog_did_close()

    def on_passthru_config_modal_cancelled(
        self, message: PassthruConfigModal.Cancelled
    ) -> None:
        """Handle passthru config modal cancellation (#446)."""
        self._dialog_did_close()

    def action_open_agent_visibility(self) -> None:
        """d: the agent visibility dialog, agents and subagents."""
        from ..config import get_idle_config, get_observability_config
        try:
            modal = self.query_one("#agent-visibility-modal", AgentVisibilityModal)
        except NoMatches:
            return
        self._dialog_will_open()
        modal.show({
            "swarm_detail": self.swarm_detail,
            "swarm_finished": self.swarm_finished,
            "finished_row_seconds": get_observability_config()["finished_row_seconds"],
            "show_done": bool(self.show_done),
            "show_terminated": bool(self.show_terminated),
            "hide_asleep": bool(self.hide_asleep),
            "idle_drowsy_after": get_idle_config()["drowsy_after"],
            "idle_sleep_after": get_idle_config()["sleep_after"],
        }, self)

    def on_agent_visibility_modal_changed(self, message: AgentVisibilityModal.Changed) -> None:
        """Apply one visibility setting as it changes."""
        setting, value = message.setting, message.value
        if setting == "swarm_detail":
            self._set_swarm_detail(value, notify=False)
        elif setting == "swarm_finished":
            self.swarm_finished = value
            self._swarm_changed()
        elif setting == "finished_row_seconds":
            from ..config import save_finished_row_seconds
            save_finished_row_seconds(value)  # the engine reads it on its next publish
        elif setting in ("idle_drowsy_after", "idle_sleep_after"):
            from ..config import save_idle_setting
            save_idle_setting(setting[len("idle_"):], value)  # rows and the engine read it
        elif setting == "show_done" and bool(self.show_done) != bool(value):
            self.action_toggle_show_done()
        elif setting == "show_terminated" and bool(self.show_terminated) != bool(value):
            self.action_toggle_show_terminated()
        elif setting == "hide_asleep" and bool(self.hide_asleep) != bool(value):
            self.action_toggle_hide_asleep()

    def on_agent_visibility_modal_closed(self, message: AgentVisibilityModal.Closed) -> None:
        self._dialog_did_close()

    def _record_instruction(self, text: str, agent_name: str) -> None:
        """Record an instruction in the history ring buffer."""
        from ..tui_widgets.instruction_history_modal import HistoryEntry, MAX_HISTORY

        self._instruction_history.insert(0, HistoryEntry(text=text, agent_name=agent_name))
        self._instruction_history = self._instruction_history[:MAX_HISTORY]

        # Update per-agent last command on the widget (#413)
        for widget in self.query(SessionSummary):
            if widget.session.name == agent_name:
                widget.last_command = text
                break

    def action_open_instruction_history(self) -> None:
        """Open the instruction history modal (#376)."""
        if not self._instruction_history:
            self.notify("No instructions sent yet — send one with 'i' first", severity="warning")
            return
        modal = self.query_one("#instruction-history-modal", InstructionHistoryModal)
        self._dialog_will_open()
        modal.show(self._instruction_history, self)

    def on_instruction_history_modal_reinject_requested(
        self, message: InstructionHistoryModal.ReinjectRequested
    ) -> None:
        """Handle reinject request from instruction history modal (#376)."""
        focused = self.focused
        if not isinstance(focused, SessionSummary):
            # No agent focused — pre-fill the command bar so user can pick a target
            self.action_focus_command_bar()
            try:
                command_bar = self.query_one("#command-bar", CommandBar)
                input_widget = command_bar.query_one("#cmd-input")
                input_widget.value = message.text
            except NoMatches:
                pass
            self._dialog_did_close()
            return

        session = focused.session
        if getattr(session, 'is_remote', False):
            if self._guard_remote(session):
                self._dialog_did_close()
                return
            result = self._sister_controller.send_instruction(
                session.source_url, session.source_api_key,
                session.name, text=message.text,
            )
            if result.ok:
                self._record_instruction(message.text, session.name)
                self.notify(f"Reinjected to remote agent {session.name}")
            else:
                self.notify(f"Remote error: {result.error}", severity="error")
            self._dialog_did_close()
            return

        launcher = AgentLauncher(
            tmux_session=self.tmux_session,
            session_manager=self.session_manager
        )
        if launcher.send_to_session_by_id(session.id, message.text):
            self._record_instruction(message.text, session.name)
            self.notify(f"Reinjected to {session.name}")
        else:
            self.notify(f"Failed to send to {session.name}", severity="error")
        self._dialog_did_close()

    def on_instruction_history_modal_cancelled(
        self, message: InstructionHistoryModal.Cancelled
    ) -> None:
        """Handle instruction history modal dismissal (#376)."""
        self._dialog_did_close()

    def action_command_palette(self) -> None:
        """Open the command palette on commands (#482)."""
        self._open_palette("commands")

    def action_jump_to_agent(self) -> None:
        """Open the palette on agents: VSCode-style jump by name (#420)."""
        if self.tui_mode == "jobs":
            return
        if not self._get_widgets_in_session_order():
            self.notify("No agents to jump to", severity="information")
            return
        self._open_palette("agents")

    def action_filter_by_tag(self) -> None:
        """Filter visible agents by tag (#357).

        Opens the palette on the distinct tags currently in use (across
        local + remote), with a `(clear filter)` row while a filter is on.
        """
        if self.tui_mode == "jobs":
            return
        if not self._palette_tags():
            self.notify(
                "No tags in use yet — apply with `overcode tag <agent> <tag>`.",
                severity="information",
            )
            return
        self._open_palette("tags")

    def _open_palette(self, mode: str) -> None:
        try:
            palette = self.query_one("#command-palette", CommandPalette)
        except NoMatches:
            return
        self._dialog_will_open()
        palette.open(
            mode,
            agents=self._palette_agents(),
            tags=self._palette_tags(),
            sorts=self._palette_sorts(),
            keymap=self._effective_keymap().keys_by_action(),
            recent=self._prefs.recent_commands,
            app_ref=self,
        )

    def _palette_agents(self) -> list:
        if self.tui_mode == "jobs":
            return []
        from ..status_constants import get_status_symbol
        candidates = []
        for w in self._get_widgets_in_session_order():
            symbol, color = get_status_symbol(w.detected_status, self.emoji_free)
            candidates.append(JumpCandidate(
                session_id=w.session.id,
                name=w.session.name,
                repo=getattr(w.session, 'repo_name', '') or '',
                branch=getattr(w.session, 'branch', '') or '',
                status=symbol,
                status_style=color,
            ))
        return candidates

    def _palette_sorts(self) -> list:
        from ..command_palette import sort_choices
        return sort_choices(self._prefs.sort_mode, self._prefs.sort_reversed)

    def _palette_tags(self) -> list:
        # Count tags over all sessions (not just visible) so the user can
        # filter back in to a tag they just filtered out.
        tag_counts: dict[str, int] = {}
        for s in self.sessions:
            for t in (getattr(s, 'tags', None) or []):
                tag_counts[t] = tag_counts.get(t, 0) + 1
        candidates: list[JumpCandidate] = []
        if self.tag_filter:
            candidates.append(JumpCandidate(
                session_id="", name="(clear filter)", repo=f"current: {self.tag_filter}",
            ))
        for tag in sorted(tag_counts):
            n = tag_counts[tag]
            candidates.append(JumpCandidate(
                session_id=tag, name=tag, repo=f"{n} agent{'s' if n != 1 else ''}",
            ))
        return candidates

    def on_command_palette_agent_chosen(self, message: CommandPalette.AgentChosen) -> None:
        for i, w in enumerate(self._get_widgets_in_session_order()):
            if w.session.id == message.session_id:
                self._user_navigated = True
                self.focused_session_index = i
                break
        self._dialog_did_close()

    def on_command_palette_tag_chosen(self, message: CommandPalette.TagChosen) -> None:
        self.tag_filter = message.tag
        if message.tag is None:
            self.notify("Tag filter cleared", severity="information")
        else:
            self.notify(f"Filtering by tag: {message.tag}", severity="information")
        self._dialog_did_close()
        self.update_session_widgets()

    def on_command_palette_command_chosen(self, message: CommandPalette.CommandChosen) -> None:
        """Run a command picked in the palette.

        Arrives after the palette handed focus back, so actions that read
        the focused agent see the right one. Called directly rather than
        through run_action: check_action blocks every action while a modal
        is visible, which the palette still is when kept open (Tab).
        """
        self.record_activity("action", action=message.action, via="palette",
                             q=message.query or None, rank=message.rank)
        if self._activity.active:
            self.note_recent_action(message.action, "palette")
        self.mentor_saw_action(message.action, "palette")
        recent = [message.action] + [a for a in self._prefs.recent_commands if a != message.action]
        self._prefs.recent_commands = recent[:10]
        self._save_prefs()
        if not message.keep_open:
            self._dialog_did_close()
        method = getattr(self, f"action_{message.action}", None)
        if method is None:
            return
        target = message.focus_target
        self.screen.set_focus(target if target is not None and target.is_attached else None)
        method()
        if message.keep_open:
            try:
                palette = self.query_one("#command-palette", CommandPalette)
            except NoMatches:
                return
            self.screen.set_focus(palette)
            palette.refresh()

    def on_command_palette_sort_chosen(self, message: CommandPalette.SortChosen) -> None:
        """A sort picked in the S picker (#487). With Tab the palette stays
        open, and its rows are refreshed so the lit row and arrow follow."""
        if not message.keep_open:
            self._dialog_did_close()
        self.set_sort_mode(message.mode)
        if message.keep_open:
            try:
                palette = self.query_one("#command-palette", CommandPalette)
            except NoMatches:
                return
            palette.update_sorts(self._palette_sorts())

    def on_column_header_clicked(self, message: ColumnHeader.Clicked) -> None:
        """Click a header to sort by it; click it again to reverse (#487)."""
        from ..summary_columns import COLUMNS_BY_ID
        from ..tui_logic import sort_mode_for_column
        self.record_activity("action", action="sort_by_column", via="click",
                             column=message.column_id)
        col = COLUMNS_BY_ID.get(message.column_id)
        if col is None or col.sort_key is None:
            name = col.name if col is not None else message.column_id
            self.notify(f"{name} is not sortable", severity="information")
            return
        self.set_sort_mode(sort_mode_for_column(message.column_id))

    def on_command_palette_closed(self, message: CommandPalette.Closed) -> None:
        self._dialog_did_close()
