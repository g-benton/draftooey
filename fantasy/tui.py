from __future__ import annotations

from pathlib import Path
import textwrap
from typing import Any

from rich.text import Text
from textual import work
from textual.app import App, ComposeResult
from textual.binding import Binding
from textual.containers import Vertical
from textual.widgets import DataTable, Footer, RichLog, Select, Static, TabbedContent, TabPane
from watchfiles import awatch

from .assessment import build_assessment, proposal_player_details
from .config import load_config
from .sleeper import refresh
from .store import advice_path, outlooks_path, read_json


BOARD_PLAYER_WIDTH = 22


def meter(value: float, width: int = 10) -> str:
    filled = max(0, min(width, round(value / 100 * width)))
    return "█" * filled + "░" * (width - filled)


class DraftDeck(App[None]):
    CSS_PATH = "tui.tcss"
    TITLE = "THE GNARBLER // DRAFT DECK"
    SUB_TITLE = "HALF-PPR"

    BINDINGS = [
        Binding("r", "refresh_live", "Refresh", priority=True),
        Binding("0", "position_all", "All", priority=True),
        Binding("1", "position_qb", "QB", priority=True),
        Binding("2", "position_rb", "RB", priority=True),
        Binding("3", "position_wr", "WR", priority=True),
        Binding("4", "position_te", "TE", priority=True),
        Binding("b", "show_board", "Board", priority=True),
        Binding("t", "show_team", "Team", priority=True),
        Binding("c", "show_comms", "Comms", priority=True),
        Binding("n", "show_news", "News", priority=True),
        Binding("enter", "toggle_summary", "Summary", priority=True),
        Binding("q", "quit", "Quit", priority=True),
    ]

    def __init__(self, *, live_refresh: bool = False) -> None:
        super().__init__(ansi_color=True)
        self.position_filter: str | None = None
        self.live_refresh = live_refresh
        self.assessment: dict[str, Any] = {}
        self.latest_advice: dict[str, Any] = {}
        self._advice_mtime: int | None = None
        self.news_checks: list[dict[str, Any]] = []
        self._news_mtime: int | None = None
        self.expanded_summaries: set[str] = set()
        self._summary_subject_by_row: dict[str, str] = {}
        self._player_by_row: dict[str, dict[str, Any]] = {}

    def compose(self) -> ComposeResult:
        yield Static("INITIALIZING DRAFT SIGNAL…", id="status")
        yield Static("NO CODEX TRANSMISSION YET", id="transmission")
        with TabbedContent(initial="board-tab", id="workspace"):
            with TabPane("BOARD", id="board-tab"):
                with Vertical():
                    yield Static(id="needs-summary")
                    yield Static(id="pair-plans")
                    yield DataTable(id="candidate-table", zebra_stripes=True, cursor_type="row")
                    yield Static(id="player-detail")
            with TabPane("TEAM / TURNS", id="team-tab"):
                with Vertical():
                    yield Static("UPCOMING SNAKE TURNS", classes="section-title")
                    yield DataTable(id="turn-table", zebra_stripes=True, cursor_type="row")
                    yield Static("ROSTER", classes="section-title")
                    yield DataTable(id="roster-table", zebra_stripes=True, cursor_type="row")
            with TabPane("TRANSMISSIONS", id="comms-tab"):
                yield RichLog(id="comms-log", wrap=True, markup=False)
            with TabPane("NEWS CHECKS", id="news-tab"):
                with Vertical():
                    yield Select([], prompt="NO NEWS CHECKS CACHED", id="news-select", allow_blank=True)
                    yield Static(id="news-detail")
        yield Footer()

    def on_mount(self) -> None:
        self.theme = "ansi-dark"
        self._configure_tables()
        self.render_assessment()
        self.load_advice(announce=True)
        self.load_news_checks()
        self.watch_advice()
        if self.live_refresh:
            self.set_interval(30.0, self.action_refresh_live)
        self.query_one("#candidate-table", DataTable).focus()

    def _configure_tables(self) -> None:
        self.query_one("#turn-table", DataTable).add_columns("TURN", "PICK", "ROUND", "STATE", "PLAYER")
        self.query_one("#roster-table", DataTable).add_columns("POS", "PLAYER", "TEAM")

    def configure_board_table(self, table: DataTable, *, player_title: str = "PLAYER") -> None:
        table.clear(columns=True)
        for label, width in (
            ("LEAN", 7),
            (player_title, BOARD_PLAYER_WIDTH),
            ("POS", 3),
            ("TEAM", 4),
            ("ECR", 3),
            ("ADP", 3),
            ("FLK", 3),
            ("TIER", 4),
            ("BYE", 3),
        ):
            table.add_column(label, width=width)

    def render_assessment(self) -> None:
        try:
            data = build_assessment(position=self.position_filter, limit=5)
        except Exception as exc:
            self.query_one("#status", Static).update(f"SIGNAL ERROR // {exc}")
            return
        self.assessment = data
        next_picks = ", ".join(str(value) for value in data["next_user_picks"]) or "—"
        filter_name = self.position_filter or "ALL"
        self.query_one("#status", Static).update(
            f"CACHED PICK {data['current_pick']}  •  NEXT {next_picks}  •  FILTER {filter_name}  •  ASK CODEX OR PRESS R TO UPDATE"
        )

        need_parts = []
        progress = data.get("starter_progress") or {}
        for position in ("QB", "RB", "WR", "TE", "FLEX", "K", "DEF"):
            slot = progress.get(position)
            if not slot:
                continue
            filled = int(slot.get("filled", 0))
            target = max(1, int(slot.get("target", 1)))
            need_parts.append(f"{position} {meter(filled / target * 100, 6)} {filled}/{target}")
        self.query_one("#needs-summary", Static).update("   ".join(need_parts))

        pair_plans = data.get("pair_plans") or []
        if pair_plans:
            lines = ["PAIR PATHS — choose the set, then pivot after the first pick if needed"]
            for plan in pair_plans:
                names = " + ".join(player.get("name") or "—" for player in plan["players"])
                lines.append(f"{plan['share']:>2}%  {names}")
            self.query_one("#pair-plans", Static).update("\n".join(lines))
        else:
            self.query_one("#pair-plans", Static).update(
                "SINGLE-PICK MODE  •  PAIR TURNS ARE PREVIEWED UNDER TEAM / TURNS"
            )

        candidates = self.query_one("#candidate-table", DataTable)
        self.configure_board_table(candidates)
        self._summary_subject_by_row.clear()
        self._player_by_row.clear()
        for player in data["candidates"]:
            player_name = player.get("name") or "Unknown"
            row_key = str(player["player_id"])
            self._player_by_row[row_key] = player
            if self.news_summary(player_name):
                self._summary_subject_by_row[row_key] = player_name
            candidates.add_row(
                f"{player.get('share', 0)}%",
                self.player_cell(player_name),
                player.get("position") or "—",
                player.get("team") or "—",
                player.get("ecr") or "—",
                player.get("adp") or "—",
                player.get("flock_rank") or "—",
                player.get("tier") or player.get("flock_tier") or "—",
                player.get("bye_week") or "—",
                key=row_key,
            )
            summary = self.board_news_summary(player_name)
            if summary and self.news_expanded(player_name):
                candidates.add_row(
                    self.news_summary_label(player_name),
                    Text(summary, style="italic"),
                    "", "", "", "", "", "", "",
                    height=summary.count("\n") + 1,
                    key=f"news-{player['player_id']}",
                )

        turns = self.query_one("#turn-table", DataTable)
        turns.clear()
        for turn in data.get("upcoming_turns", []):
            for slot in turn["slots"]:
                state = "KEEPER" if slot.get("keeper") else ("SELECTED" if slot.get("player_id") else "OPEN")
                turns.add_row(
                    turn["turn"],
                    slot["pick"],
                    slot["round"],
                    state,
                    slot.get("player_name") or "",
                    key=f"{turn['turn']}-{slot['pick']}",
                )

        roster = self.query_one("#roster-table", DataTable)
        roster.clear()
        for index, player in enumerate(data["roster"]):
            roster.add_row(
                player.get("position") or "", player.get("name") or "", player.get("team") or "", key=str(index)
            )
        if data["candidates"]:
            self.show_player_details(data["candidates"][0])
        if self.position_filter is None and self.latest_advice.get("proposals"):
            self.render_proposal_board(self.latest_advice)
        elif self.position_filter is None and self.latest_advice.get("players"):
            self.render_single_board(self.latest_advice)

    def render_single_board(self, advice: dict[str, Any]) -> None:
        names = advice.get("players") or []
        if not names:
            return
        evidence = proposal_player_details(names)
        table = self.query_one("#candidate-table", DataTable)
        self.configure_board_table(table)
        self._summary_subject_by_row.clear()
        self._player_by_row.clear()
        for index, name in enumerate(names):
            player = evidence.get(name, {})
            row_key = f"single-{index}"
            self._player_by_row[row_key] = {**player, "name": player.get("name") or name}
            if self.news_summary(name):
                self._summary_subject_by_row[row_key] = name
            table.add_row(
                "CALL" if index == 0 else f"ALT {index}",
                self.player_cell(name, bold=True),
                player.get("position") or "—",
                player.get("team") or "—",
                player.get("ecr") or "—",
                player.get("adp") or "—",
                player.get("flock_rank") or "—",
                player.get("tier") or player.get("flock_tier") or "—",
                player.get("bye_week") or "—",
                key=row_key,
            )
            summary = self.board_news_summary(name)
            if summary and self.news_expanded(name):
                table.add_row(
                    self.news_summary_label(name),
                    Text(summary, style="italic"),
                    "", "", "", "", "", "", "",
                    height=summary.count("\n") + 1,
                    key=f"{row_key}-news",
                )
        self.query_one("#pair-plans", Static).update(
            f"CODEX SINGLE BOARD  •  {advice.get('headline') or 'PICK PLAN'}  •  ENTER FOLDS NEWS"
        )
        if names:
            self.show_player_details(self._player_by_row["single-0"])

    def render_proposal_board(self, advice: dict[str, Any]) -> None:
        proposals = advice.get("proposals") or []
        if not proposals:
            return
        proposed_names = [name for proposal in proposals for name in (proposal.get("players") or [])]
        evidence = proposal_player_details(proposed_names)
        table = self.query_one("#candidate-table", DataTable)
        self.configure_board_table(table, player_title="PLAYER / WHY")
        self._summary_subject_by_row.clear()
        self._player_by_row.clear()
        for index, proposal in enumerate(proposals):
            players = proposal.get("players") or []
            for player_index, name in enumerate(players[:2], start=1):
                player = evidence.get(name, {})
                label = f"{proposal.get('share', 0)}% P1" if player_index == 1 else "    P2"
                row_key = f"proposal-{index}-p{player_index}"
                self._player_by_row[row_key] = {**player, "name": player.get("name") or name}
                if self.news_summary(name):
                    self._summary_subject_by_row[row_key] = name
                table.add_row(
                    label,
                    self.player_cell(name, bold=True),
                    player.get("position") or "—",
                    player.get("team") or "—",
                    player.get("ecr") or "—",
                    player.get("adp") or "—",
                    player.get("flock_rank") or "—",
                    player.get("tier") or player.get("flock_tier") or "—",
                    player.get("bye_week") or "—",
                    key=row_key,
                )
                summary = self.board_news_summary(name)
                if summary and self.news_expanded(name):
                    table.add_row(
                        self.news_summary_label(name),
                        Text(summary, style="italic"),
                        "", "", "", "", "", "", "",
                        height=summary.count("\n") + 1,
                        key=f"proposal-{index}-p{player_index}-news",
                    )
            reason = self.wrap_board_text(proposal.get("reason") or "No rationale supplied.")
            table.add_row(
                "WHY",
                Text(reason, style="italic"),
                "", "", "", "", "", "", "",
                height=reason.count("\n") + 1,
                key=f"proposal-{index}-why",
            )
        self.query_one("#pair-plans", Static).update(
            f"CODEX PAIR BOARD  •  {advice.get('headline') or 'TURN PLAN'}  •  SHARES TOTAL 100%"
        )
        if self._player_by_row:
            self.show_player_details(next(iter(self._player_by_row.values())))

    def show_player_details(self, player: dict[str, Any]) -> None:
        factors = player.get("factors", {})
        lines = [
            f"{player.get('name')}  •  FP ECR {player.get('ecr') or '—'}  •  FP ADP {player.get('adp') or '—'}  •  "
            f"FLOCK {player.get('flock_rank') or '—'}  •  TIER {player.get('tier') or player.get('flock_tier') or '—'}  •  BYE {player.get('bye_week') or '—'}",
            f"AGE {player.get('age') or '—'}  •  EXP {player.get('years_exp') if player.get('years_exp') is not None else '—'}  •  "
            f"ECR RANGE {player.get('ecr_min') or '—'}–{player.get('ecr_max') or '—'} / AVG {player.get('ecr_average') or '—'}  •  "
            f"INJURY {player.get('injury') or 'CLEAR'}",
        ]
        if factors:
            lines.append(
                f"RANK {meter(float(factors.get('ranking', 0)) * 100)}  "
                f"FIT {meter(float(factors.get('roster_fit', 0)) * 100)}  "
                f"VALUE {meter(float(factors.get('value', 0)) * 100)}  "
                f"RISK {meter(float(factors.get('risk', 0)) * 100)}"
            )
        else:
            signal = self.news_signal(str(player.get("name") or ""))
            lines.append(f"OUTLOOK {signal}  •  ENTER TO EXPAND CACHED AGENT SUMMARY")
        self.query_one("#player-detail", Static).update("\n".join(lines))

    def on_data_table_row_highlighted(self, event: DataTable.RowHighlighted) -> None:
        if event.data_table.id == "candidate-table" and event.row_key is not None:
            key = str(event.row_key.value)
            player = self._player_by_row.get(key)
            if player:
                self.show_player_details(player)

    def load_advice(self, *, announce: bool = False) -> None:
        path = advice_path()
        if not path.exists():
            return
        mtime = path.stat().st_mtime_ns
        if mtime == self._advice_mtime:
            return
        self._advice_mtime = mtime
        data = read_json(path)
        self.latest_advice = data
        headline = data.get("headline") or "INCOMING"
        body = data.get("body") or ""
        players = data.get("players") or []
        player_line = "  •  ".join(players)
        banner = Text()
        banner.append(f"CODEX // {headline}\n", style="bold")
        banner.append(body)
        if player_line:
            banner.append(f"\n{player_line}", style="italic")
        self.query_one("#transmission", Static).update(banner)
        # Advice is canonical board state: a message with proposals renders the
        # pair board, while one without proposals returns to the single board.
        self.render_assessment()
        if announce:
            log = self.query_one("#comms-log", RichLog)
            log.write(Text(f"{data.get('published_at') or ''}  {headline}", style="bold"))
            if body:
                log.write(body)
            if player_line:
                log.write(player_line)
            log.write("")

    def load_news_checks(self) -> None:
        path = outlooks_path()
        if not path.exists():
            self.query_one("#news-detail", Static).update(
                "ASK CODEX FOR A RECENT NEWS CHECK — SEARCHES RUN ONLY ON REQUEST"
            )
            return
        mtime = path.stat().st_mtime_ns
        if mtime == self._news_mtime:
            return
        self._news_mtime = mtime
        self.news_checks = sorted(
            read_json(path).get("players", {}).values(),
            key=lambda item: item.get("checked_at", ""),
            reverse=True,
        )
        selector = self.query_one("#news-select", Select)
        selector.set_options([
            (f"{item.get('subject')}  //  {str(item.get('verdict', '')).upper()}", str(index))
            for index, item in enumerate(self.news_checks)
        ])
        if self.news_checks:
            selector.value = "0"
            self.render_news_check(0)
        else:
            selector.set_options([])
            self.query_one("#news-detail", Static).update(
                "NO CACHED OUTLOOKS — PLAYERS ARE SEARCHED ONLY WHEN DISCUSSED"
            )
        self.render_assessment()

    def news_signal(self, subject: str) -> str:
        item = self.news_item(subject)
        if not item:
            return "—"
        verdicts = {
            "positive": "POS",
            "neutral": "NEU",
            "negative": "NEG",
            "mixed": "MIX",
            "uncertain": "UNC",
        }
        confidence = str(item.get("confidence") or "low")[:1].upper()
        return f"{verdicts.get(str(item.get('verdict')), 'UNC')}·{confidence}"

    def news_key(self, subject: str) -> str:
        item = self.news_item(subject) or {}
        return str(item.get("player_id") or subject.casefold())

    def news_expanded(self, subject: str) -> bool:
        return self.news_key(subject) in self.expanded_summaries

    def player_cell(self, subject: str, *, bold: bool = False) -> Text:
        marker = ""
        if self.news_summary(subject):
            marker = "▾ " if self.news_expanded(subject) else "▸ "
        return Text(f"{marker}{subject}", style="bold" if bold else None)

    def wrap_board_text(self, value: str, *, max_lines: int = 10) -> str:
        lines = textwrap.wrap(str(value).strip(), width=BOARD_PLAYER_WIDTH)
        if len(lines) > max_lines:
            lines = lines[:max_lines]
            lines[-1] = lines[-1].rstrip(" .") + "…"
        return "\n".join(lines)

    def board_news_summary(self, subject: str) -> str:
        item = self.news_item(subject)
        if not item:
            return ""
        summary = str(item.get("summary") or "").strip()
        if not summary:
            return ""
        return self.wrap_board_text(f"{self.news_signal(subject)}  {summary}")

    def news_item(self, subject: str) -> dict[str, Any] | None:
        return next(
            (check for check in self.news_checks if str(check.get("subject", "")).casefold() == subject.casefold()),
            None,
        )

    def news_summary(self, subject: str, *, width: int = 84) -> str:
        item = self.news_item(subject)
        if not item:
            return ""
        summary = str(item.get("summary") or "").strip()
        lines = textwrap.wrap(summary, width=width)
        if len(lines) > 2:
            lines = lines[:2]
            lines[-1] = lines[-1].rstrip(" .") + "…"
        return "\n".join(lines)

    def news_summary_label(self, subject: str) -> str:
        item = self.news_item(subject) or {}
        return "↳ LOCAL" if item.get("summary_author") == "local" else "↳ AGENT"

    def render_news_check(self, index: int) -> None:
        if not 0 <= index < len(self.news_checks):
            return
        item = self.news_checks[index]
        verdict = str(item.get("verdict") or "uncertain").upper()
        confidence = str(item.get("confidence") or "low").upper()
        lines = [
            f"{verdict}  •  CONFIDENCE {confidence}  •  LAST {item.get('window_days', 7)} DAYS  •  {item.get('checked_at', '')}",
            "",
            str(item.get("summary") or "No summary supplied."),
        ]
        flags = item.get("flags") or []
        if flags:
            lines.extend(["", "SIGNALS"])
            lines.extend(
                f"{str(flag.get('severity', 'info')).upper()} / {str(flag.get('type', 'other')).upper()}  {flag.get('claim', '')}"
                for flag in flags
            )
        sources = item.get("sources") or []
        if sources:
            lines.extend(["", "SOURCES"])
            lines.extend(f"{source.get('published_at', '')}  {source.get('title', '')}  {source.get('url', '')}" for source in sources)
        reviewer = item.get("reviewer") or {}
        lines.extend(["", f"SANITY {str(reviewer.get('outcome', 'unknown')).upper()}"])
        lines.extend(f"CAVEAT  {concern}" for concern in (reviewer.get("concerns") or []))
        self.query_one("#news-detail", Static).update("\n".join(lines))

    def on_select_changed(self, event: Select.Changed) -> None:
        if event.select.id == "news-select" and event.value is not Select.BLANK:
            self.render_news_check(int(str(event.value)))

    def action_toggle_summary(self) -> None:
        table = self.query_one("#candidate-table", DataTable)
        if not table.has_focus or not table.row_count:
            return
        cell_key = table.coordinate_to_cell_key(table.cursor_coordinate)
        row_key = str(cell_key.row_key.value)
        subject = self._summary_subject_by_row.get(row_key)
        if not subject:
            return
        news_key = self.news_key(subject)
        if news_key in self.expanded_summaries:
            self.expanded_summaries.remove(news_key)
        else:
            self.expanded_summaries.add(news_key)
        self.render_assessment()
        try:
            table.move_cursor(row=table.get_row_index(row_key), animate=False)
        except Exception:
            pass

    @work(exclusive=True, group="advice-watch", exit_on_error=False)
    async def watch_advice(self) -> None:
        path = advice_path()
        path.parent.mkdir(parents=True, exist_ok=True)
        outlook_path = outlooks_path()
        outlook_path.parent.mkdir(parents=True, exist_ok=True)
        async for changes in awatch(path.parent, outlook_path.parent):
            changed_paths = {Path(changed).resolve() for _, changed in changes}
            if path.resolve() in changed_paths:
                self.load_advice(announce=True)
            if outlook_path.resolve() in changed_paths:
                self.load_news_checks()

    @work(thread=True, exclusive=True, group="live-refresh", exit_on_error=False)
    def refresh_live(self) -> None:
        try:
            refresh(load_config(), include_players=False)
        except Exception as exc:
            self.call_from_thread(self.query_one("#status", Static).update, f"OFFLINE • LAST GOOD CACHE • {exc}")
            return
        self.call_from_thread(self.render_assessment)

    def action_refresh_live(self) -> None:
        self.query_one("#status", Static).update("SYNCING SLEEPER…")
        self.refresh_live()

    def set_position(self, position: str | None) -> None:
        self.position_filter = position
        self.render_assessment()
        self.query_one("#workspace", TabbedContent).active = "board-tab"

    def action_position_all(self) -> None:
        self.set_position(None)

    def action_position_qb(self) -> None:
        self.set_position("QB")

    def action_position_rb(self) -> None:
        self.set_position("RB")

    def action_position_wr(self) -> None:
        self.set_position("WR")

    def action_position_te(self) -> None:
        self.set_position("TE")

    def action_show_board(self) -> None:
        self.query_one("#workspace", TabbedContent).active = "board-tab"

    def action_show_team(self) -> None:
        self.query_one("#workspace", TabbedContent).active = "team-tab"

    def action_show_comms(self) -> None:
        self.query_one("#workspace", TabbedContent).active = "comms-tab"

    def action_show_news(self) -> None:
        self.query_one("#workspace", TabbedContent).active = "news-tab"


def run(*, auto_refresh: bool = False) -> None:
    DraftDeck(live_refresh=auto_refresh).run()
