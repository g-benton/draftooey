from __future__ import annotations

from datetime import UTC, datetime, timedelta
import hashlib
import json
import typer
from rich.console import Console
from rich.table import Table

from .board import available_players, enrich_with_rankings, needs, roster_players, user_roster
from .assessment import build_assessment
from .config import load_config
from .contracts import AdviceMessage, DraftProposal, NewsCheck, NewsFlag, NewsReview, NewsSource, SearchResult
from .rankings import fetch_fantasypros, fetch_fantasypros_html, import_csv
from .sleeper import refresh
from .store import advice_path, now, outlooks_path, players_path, ranking_paths, read_json, request_log_path, snapshot_path, write_json


app = typer.Typer(help="Live Sleeper draft assistant.", no_args_is_help=True)
rankings_app = typer.Typer(help="Fetch and manage external rankings.", no_args_is_help=True)
app.add_typer(rankings_app, name="rankings")
console = Console()


def snapshot():
    return read_json(snapshot_path())


@app.command("refresh")
def refresh_data(players: bool = typer.Option(True, "--players/--no-players", help="Refresh Sleeper's full player catalog too.")) -> None:
    """Fetch a fresh live snapshot from Sleeper."""
    result = refresh(load_config(), include_players=players)
    console.print(f"[green]Updated[/green] Sleeper snapshot at {result['refreshed_at']}")


@app.command()
def status() -> None:
    """Show your live league, draft, and roster status."""
    data = snapshot()
    league, draft = data["league"], data["draft"]
    roster = user_roster(data)
    drafted = roster_players(data)
    picks = sorted(data["picks"], key=lambda pick: pick["pick_no"])
    current = league.get("metadata", {}).get("current_pick_no")
    table = Table(title=league["name"])
    table.add_column("Field", style="bold")
    table.add_column("Value")
    table.add_row("Snapshot", data["refreshed_at"])
    table.add_row("Draft", f"{draft['status']} · {draft['type']} · slot {draft['draft_order'][data['config']['user_id']]}")
    table.add_row("Current pick", str(current or "not started"))
    table.add_row("Your draft team", f"Roster {roster['roster_id']} · {len(drafted)} keeper/draft selections")
    table.add_row("Drafted/keepers", str(len(picks)))
    console.print(table)


@app.command()
def roster() -> None:
    """Show your 2026 keeper/draft selections so far."""
    data = snapshot()
    table = Table(title="The Gnarbler — 2026 draft team")
    table.add_column("Player")
    table.add_column("Pos")
    table.add_column("Team")
    table.add_column("Injury")
    for player in sorted(roster_players(data), key=lambda item: (item.get("position", ""), item.get("full_name", ""))):
        table.add_row(player.get("full_name", "Unknown"), player.get("position", ""), player.get("team", ""), player.get("injury_status") or "")
    console.print(table)


@app.command()
def available(position: str | None = typer.Option(None, "--position", "-p"), limit: int = typer.Option(30, min=1, max=200)) -> None:
    """Show undrafted players, ordered by cached ECR then ADP."""
    data = snapshot()
    players = enrich_with_rankings(available_players(data, position=position))
    players.sort(
        key=lambda item: (
            item.get("ecr") is None,
            item.get("ecr") or item.get("adp") or 99999,
            item.get("name") or "",
        )
    )
    table = Table(title=f"Available players{f' — {position.upper()}' if position else ''}")
    table.add_column("Player")
    table.add_column("Pos")
    table.add_column("Team")
    table.add_column("ECR", justify="right")
    table.add_column("ADP", justify="right")
    table.add_column("Injury")
    for player in players[:limit]:
        table.add_row(player["name"] or "Unknown", player["position"] or "", player["team"] or "", str(player.get("ecr") or ""), str(player.get("adp") or ""), player.get("injury") or "")
    console.print(table)


@app.command()
def plan() -> None:
    """Show lineup gaps based on the current roster."""
    data = snapshot()
    table = Table(title="Starting-lineup gaps")
    table.add_column("Position")
    table.add_column("Open starter slots", justify="right")
    for position, count in needs(data).items():
        table.add_row(position, str(count))
    console.print(table)


@app.command()
def assess(
    position: str | None = typer.Option(None, "--position", "-p"),
    limit: int = typer.Option(5, min=1, max=12),
    json_output: bool = typer.Option(False, "--json", help="Emit the assessment contract as JSON."),
) -> None:
    """Build a short recommendation slate from the current cached draft state."""
    data = build_assessment(position=position.upper() if position else None, limit=limit)
    if json_output:
        console.print_json(json.dumps(data))
        return
    table = Table(title=f"Draft signal — {position.upper() if position else 'overall'}")
    table.add_column("Lean", justify="right")
    table.add_column("Player")
    table.add_column("Pos")
    table.add_column("Team")
    table.add_column("ECR", justify="right")
    table.add_column("ADP", justify="right")
    table.add_column("Flock", justify="right")
    table.add_column("Tier", justify="right")
    table.add_column("Bye", justify="right")
    table.add_column("Injury")
    for player in data["candidates"]:
        table.add_row(
            f"{player['share']}%",
            player.get("name") or "Unknown",
            player.get("position") or "",
            player.get("team") or "",
            str(player.get("ecr") or ""),
            str(player.get("adp") or ""),
            str(player.get("flock_rank") or ""),
            str(player.get("tier") or player.get("flock_tier") or ""),
            str(player.get("bye_week") or ""),
            player.get("injury") or "",
        )
    console.print(table)


@app.command()
def publish(
    headline: str = typer.Argument(..., help="Short recommendation headline."),
    body: str = typer.Option("", "--body", "-b", help="Recommendation explanation."),
    player: list[str] = typer.Option([], "--player", "-p", help="Shortlisted player; repeat for several."),
    pair: list[str] = typer.Option(
        [], "--pair", help="Pair proposal formatted as 'Player One + Player Two'; repeat for alternatives."
    ),
    lean: list[int] = typer.Option([], "--lean", help="Proposal share; repeat once per --pair and total 100."),
    reason: list[str] = typer.Option([], "--reason", help="Pair rationale; repeat once per --pair."),
) -> None:
    """Publish an assistant transmission to the running TUI."""
    data = snapshot()
    proposals: list[DraftProposal] = []
    if pair:
        parsed_pairs = []
        for value in pair:
            names = tuple(part.strip() for part in value.split("+", maxsplit=1))
            if len(names) != 2 or not all(names):
                raise typer.BadParameter("Each --pair must use 'Player One + Player Two'")
            parsed_pairs.append(names)
        if lean and len(lean) != len(parsed_pairs):
            raise typer.BadParameter("Repeat --lean once per --pair")
        if reason and len(reason) != len(parsed_pairs):
            raise typer.BadParameter("Repeat --reason once per --pair")
        shares = lean or [100 // len(parsed_pairs)] * len(parsed_pairs)
        if not lean:
            shares[0] += 100 - sum(shares)
        if sum(shares) != 100 or any(value < 0 for value in shares):
            raise typer.BadParameter("Pair --lean values must be non-negative and total 100")
        reasons = reason or [""] * len(parsed_pairs)
        proposals = [
            DraftProposal(share=share, players=names, reason=why)
            for names, share, why in zip(parsed_pairs, shares, reasons, strict=True)
        ]
    payload = AdviceMessage(
        published_at=now(),
        based_on_pick=data["league"].get("metadata", {}).get("current_pick_no"),
        headline=headline,
        body=body,
        players=tuple(player),
        proposals=tuple(proposals),
    )
    write_json(advice_path(), payload.to_dict())
    console.print(f"[green]Transmitted[/green] to {advice_path()}")


def _parts(value: str, count: int, label: str) -> list[str]:
    parts = [part.strip() for part in value.split("|", maxsplit=count - 1)]
    if len(parts) != count or not all(parts):
        raise typer.BadParameter(f"{label} must contain {count} non-empty pipe-separated fields")
    return parts


def _search_parts(value: str) -> list[str]:
    parts = [part.strip() for part in value.split("|", maxsplit=4)]
    if len(parts) != 5 or not all(parts[:2]):
        raise typer.BadParameter("--search-result must contain TITLE|URL|PUBLISHED_AT|PUBLISHER|SNIPPET")
    return parts


OUTLOOK_PROVIDER = "tinyfish"
OUTLOOK_PURPOSE = "Assess current fantasy-football draft outlook"
OUTLOOK_RECENCY_MINUTES = 7 * 24 * 60
OUTLOOK_TTL_HOURS = 6


def _resolve_player(subject: str, player_id: str | None) -> tuple[str, str | None]:
    catalog = read_json(players_path()).get("players", {})
    if player_id:
        player = catalog.get(str(player_id)) or {}
        return str(player_id), player.get("team")
    matches = [
        (str(candidate_id), player.get("team"))
        for candidate_id, player in catalog.items()
        if str(player.get("full_name") or "").casefold() == subject.strip().casefold()
    ]
    if len(matches) != 1:
        raise typer.BadParameter("Supply --player-id; subject did not resolve uniquely in Sleeper")
    return matches[0]


def _outlook_fingerprint(query: str) -> str:
    value = "|".join((OUTLOOK_PROVIDER, query.casefold().strip(), OUTLOOK_PURPOSE, str(OUTLOOK_RECENCY_MINUTES), "v1"))
    return hashlib.sha256(value.encode()).hexdigest()


def _empty_outlook_cache() -> dict:
    return {
        "schema_version": 1,
        "provider": OUTLOOK_PROVIDER,
        "query_policy": {
            "template": 'fantasy outlook "{player}"',
            "purpose": OUTLOOK_PURPOSE,
            "recency_minutes": OUTLOOK_RECENCY_MINUTES,
            "ttl_hours": OUTLOOK_TTL_HOURS,
        },
        "updated_at": None,
        "players": {},
    }


@app.command("outlook-status")
def outlook_status(
    subject: str = typer.Argument(...),
    player_id: str | None = typer.Option(None, "--player-id"),
    query: str = typer.Option("", help="Override the standard fantasy-outlook query."),
    full: bool = typer.Option(False, "--full", help="Include raw cached search evidence on a hit."),
) -> None:
    """Report whether one player's lazy TinyFish outlook is a fresh cache hit."""
    resolved_id, _ = _resolve_player(subject, player_id)
    resolved_query = query.strip() or f'fantasy outlook "{subject.strip()}"'
    expected = _outlook_fingerprint(resolved_query)
    path = outlooks_path()
    cache = read_json(path) if path.exists() else _empty_outlook_cache()
    entry = cache.get("players", {}).get(resolved_id)
    status = "miss"
    if entry:
        try:
            fresh = datetime.fromisoformat(entry["expires_at"].replace("Z", "+00:00")) > datetime.now(UTC)
        except (KeyError, TypeError, ValueError):
            fresh = False
        status = "hit" if fresh and entry.get("query_fingerprint") == expected else "stale"
    cached = None
    if status == "hit":
        cached = entry if full else {
            key: entry.get(key)
            for key in ("subject", "team", "checked_at", "expires_at", "verdict", "confidence", "summary", "flags")
        }
    console.print_json(json.dumps({"status": status, "player_id": resolved_id, "query": resolved_query, "entry": cached}))


@app.command("news-publish", hidden=True)
@app.command("outlook-publish")
def outlook_publish(
    subject: str = typer.Argument(..., help="Player or team shown in the dropdown."),
    player_id: str | None = typer.Option(None, "--player-id", help="Canonical Sleeper player ID."),
    verdict: str = typer.Option(..., help="positive, neutral, negative, mixed, or uncertain."),
    confidence: str = typer.Option(..., help="low, medium, or high."),
    summary: str = typer.Option(..., help="Brief factual result and fantasy implication."),
    summary_author: str = typer.Option("fast-agent", help="Author provenance shown on the board."),
    query: str = typer.Option("", help="Search query used."),
    search_result: list[str] = typer.Option([], help="Repeat TITLE|URL|PUBLISHED_AT|PUBLISHER|SNIPPET."),
    flag: list[str] = typer.Option([], help="Repeat SEVERITY|TYPE|CLAIM."),
    source: list[str] = typer.Option([], help="Repeat TITLE|URL|PUBLISHED_AT."),
    review: str = typer.Option("pass", help="pass, pass_with_caveats, or fail."),
    reviewer_role: str = typer.Option("fast-sanity", help="fast-sanity or local-sanity."),
    concern: list[str] = typer.Option([], help="Repeat for reviewer caveats."),
) -> None:
    """Upsert one lazily requested TinyFish outlook for the TUI."""
    verdict = verdict.lower()
    confidence = confidence.lower()
    review = review.lower()
    reviewer_role = reviewer_role.lower()
    if not subject.strip() or not summary.strip():
        raise typer.BadParameter("Subject and summary must not be blank")
    if len(summary.strip()) > 240:
        raise typer.BadParameter("Summary must be at most 240 characters")
    if verdict not in {"positive", "neutral", "negative", "mixed", "uncertain"}:
        raise typer.BadParameter("Invalid --verdict")
    if confidence not in {"low", "medium", "high"}:
        raise typer.BadParameter("Invalid --confidence")
    if review not in {"pass", "pass_with_caveats", "fail"}:
        raise typer.BadParameter("Invalid --review")
    if reviewer_role not in {"fast-sanity", "local-sanity"}:
        raise typer.BadParameter("Invalid --reviewer-role")
    resolved_id, team = _resolve_player(subject, player_id)
    resolved_query = query.strip() or f'fantasy outlook "{subject.strip()}"'

    flags = []
    for value in flag:
        severity, kind, claim = _parts(value, 3, "--flag")
        if severity not in {"info", "watch", "material"}:
            raise typer.BadParameter("Flag severity must be info, watch, or material")
        if kind not in {"injury", "role", "depth_chart", "transaction", "discipline", "performance", "other"}:
            raise typer.BadParameter("Invalid flag type")
        flags.append(NewsFlag(severity=severity, type=kind, claim=claim))
    sources = []
    for value in source:
        title, url, published_at = _parts(value, 3, "--source")
        if not url.startswith(("https://", "http://")):
            raise typer.BadParameter("Source URL must be HTTP(S)")
        try:
            datetime.fromisoformat(published_at.replace("Z", "+00:00"))
        except ValueError as exc:
            raise typer.BadParameter("Source date must be ISO-8601") from exc
        sources.append(NewsSource(title, url, published_at))
    search_results = []
    for value in search_result:
        title, url, published_at, publisher, snippet = _search_parts(value)
        if not url.startswith(("https://", "http://")):
            raise typer.BadParameter("Search-result URL must be HTTP(S)")
        search_results.append(SearchResult(title, url, published_at, publisher, snippet))
    checked_at = datetime.now(UTC)
    check = NewsCheck(
        player_id=resolved_id,
        subject=subject.strip(),
        team=team,
        query=resolved_query,
        query_fingerprint=_outlook_fingerprint(resolved_query),
        checked_at=checked_at.isoformat(timespec="seconds"),
        expires_at=(checked_at + timedelta(hours=OUTLOOK_TTL_HOURS)).isoformat(timespec="seconds"),
        verdict=verdict,  # type: ignore[arg-type]
        confidence=confidence,  # type: ignore[arg-type]
        summary=summary.strip(),
        summary_author=summary_author.strip() or "fast-agent",
        search_results=tuple(search_results),
        flags=tuple(flags),
        sources=tuple(sources),
        reviewer=NewsReview(outcome=review, concerns=tuple(concern), role=reviewer_role),  # type: ignore[arg-type]
    )
    path = outlooks_path()
    cached = read_json(path) if path.exists() else _empty_outlook_cache()
    cached["players"][resolved_id] = check.to_dict()
    cached["updated_at"] = now()
    write_json(path, cached)
    console.print(f"[green]Outlook cached[/green] for {subject} [{resolved_id}] at {path}")


@app.command("tui")
def tui_command(
    auto_refresh: bool = typer.Option(
        False,
        "--auto-refresh",
        help="Refresh Sleeper every 30 seconds instead of updating only on request or with R.",
    ),
) -> None:
    """Launch the cassette-futurist draft deck."""
    from .tui import run

    run(auto_refresh=auto_refresh)


@rankings_app.command("status")
def rankings_status() -> None:
    """Show cached ranking sources and their retrieval times."""
    paths = ranking_paths()
    if not paths:
        console.print("[yellow]No rankings cached.[/yellow] Fetch FantasyPros data or import a permitted CSV source.")
        raise typer.Exit()
    table = Table(title="Cached ranking sources")
    table.add_column("Source")
    table.add_column("Kind")
    table.add_column("Fetched")
    table.add_column("Players", justify="right")
    for path in paths:
        data = read_json(path)
        table.add_row(data["source"], data["kind"].upper(), data["fetched_at"], str(len(data["players"])))
    console.print(table)
    requests = read_json(request_log_path()) if request_log_path().exists() else []
    console.print(f"Completed provider requests recorded locally: {len(requests)}")


@rankings_app.command("fantasypros")
def fantasypros(kind: str = typer.Argument(..., help="ecr or adp"), season: int = typer.Option(datetime.now().year), scoring: str = typer.Option("HALF"), force: bool = typer.Option(False, "--force", help="Bypass the local cached response and spend one new API request.")) -> None:
    """Fetch official FantasyPros ECR or ADP (requires FANTASYPROS_API_KEY)."""
    payload = fetch_fantasypros(kind=kind.lower(), season=season, scoring=scoring.upper(), force=force)
    console.print(f"[green]Cached[/green] {len(payload['players'])} FantasyPros {kind.upper()} rows at {payload['fetched_at']}")


@rankings_app.command("fantasypros-html")
def fantasypros_html(force: bool = typer.Option(False, "--force", help="Bypass the local cached page and fetch once more.")) -> None:
    """Fetch the public FantasyPros Half-PPR cheatsheet page (ECR + ADP)."""
    payload = fetch_fantasypros_html(force=force)
    console.print(f"[green]Cached[/green] {len(payload['ecr']['players'])} ECR and {len(payload['adp']['players'])} ADP FantasyPros HTML rows")


@rankings_app.command("import-csv")
def csv_import(source: str, kind: str, url: str, name_column: str = "name", rank_column: str = "rank", position_column: str = "position") -> None:
    """Import a public CSV ranking/ADP feed by URL."""
    payload = import_csv(source=source, kind=kind.lower(), url=url, name_column=name_column, rank_column=rank_column, position_column=position_column)
    console.print(f"[green]Saved[/green] {len(payload['players'])} {kind.upper()} rows from {source}")
