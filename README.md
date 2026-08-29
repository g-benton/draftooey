# Fantasy toolkit

Personal, local-first command-line tools for a live Sleeper fantasy-football draft.

## Setup

`sleeper.yaml` stores the league, username, and Sleeper user ID. Keep API keys out of that file.

```sh
uv sync
uv run fantasy refresh
uv run fantasy status
uv run fantasy roster
uv run fantasy available --position RB
```

`refresh` queries Sleeper and stores a timestamped snapshot under
`data/sleeper/`. Sleeper remains the live source of truth; the local files only
make the views fast and reproducible.

## Rankings

The public Half-PPR cheatsheet currently provides the useful full-board cache
in one page request:

```sh
uv run fantasy rankings fantasypros-html
```

That command caches both embedded datasets: overall Half-PPR ECR and ADP. It is
cache-first; pass `--force` only when you intentionally want a fresh page.

FantasyPros provides official ECR and ADP through its API. Set an API key in your shell, then fetch the sources separately:

```sh
export FANTASYPROS_API_KEY='...'
uv run fantasy rankings fantasypros ecr --season 2026 --scoring HALF
uv run fantasy rankings fantasypros adp --season 2026 --scoring HALF
uv run fantasy rankings status
```

The data is cached with fetch time and source metadata. Repeating either command reuses the local cache and costs no API request. Add `--force` only when you intentionally want a fresh provider request; every successful provider request is recorded locally. The `available` command merges cached ECR/ADP with live Sleeper availability using normalized, fuzzy player-name matching.

## Data contract

All persisted inputs and caches are grouped by provider under `data/` (for
example `data/fantasypros/`, `data/flock/`, and `data/sleeper/`). Provider
payloads are normalized behind the versioned types in
`fantasy/contracts.py`. See `DATA_CONTRACT.md` for the identity, rankings,
projections, trends, teams, and draft-state contract intended for the terminal
UI. UI code should consume `FantasyData` rather than read `data/` directly.

For any permitted CSV source, use:

```sh
uv run fantasy rankings import-csv underdog adp 'https://example.com/adp.csv' \
  --name-column player --rank-column adp --position-column position
```

## Draft commands

```sh
uv run fantasy refresh --no-players  # quicker refresh when the player catalog is already cached
uv run fantasy status
uv run fantasy available --limit 20
uv run fantasy available -p RB --limit 15
uv run fantasy plan
uv run fantasy rankings status
```

## Draft deck TUI

Run the cassette-futurist dashboard in a terminal panel beside chat:

```sh
uv run fantasy tui
```

It loads the cached board immediately and updates at conversation pace: ask
Codex to reassess or press `R` for a manual Sleeper refresh. Use
`--auto-refresh` only when you explicitly want a 30-second background refresh.
It supports `0`–`4` for position filters and `B`/`T`/`C` for board, team/turns,
and transmission history. Its default
ANSI theme inherits the active Ghostty palette; Textual's command palette can
switch to any built-in theme at runtime. Build the same five-player slate
without the full-screen UI with:

```sh
uv run fantasy assess
uv run fantasy assess --position RB --json
```

Assistant messages are written atomically to `data/session/advice.json`; a
running TUI receives them through an event-driven file watcher:

```sh
uv run fantasy publish 'TAKE GIBBS' \
  --body 'Elite tier and our clearest roster-construction fit.' \
  --player 'Jahmyr Gibbs' --player 'Bijan Robinson'
```

For a paired snake turn, publish several alternatives. The BOARD tab renders
each as three rows (P1 evidence, P2 evidence, and WHY), and proposal shares must
total 100:

```sh
uv run fantasy publish 'TURN 2 // PICKS 28 + 29' \
  --pair 'Josh Allen + Tucker Kraft' --lean 60 \
  --reason 'Elite QB plus a TE target.' \
  --pair 'James Cook + Drake London' --lean 40 \
  --reason 'Balanced RB/WR volume.'
```
