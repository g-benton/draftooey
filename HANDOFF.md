# Fantasy draft partner handoff

## Read this first

The user wants Codex to act as a conversational draft partner. The TUI is the
shared display surface for that conversation—not an autonomous draft bot.

Work at the user's pace:

1. Wait until the user asks for an assessment or wants to plan a turn.
2. Refresh Sleeper once with `uv run fantasy refresh --no-players`.
3. Inspect `uv run fantasy assess --json` and any relevant position views.
4. Discuss strategy in chat. Do not publish a plan before the user is ready.
5. Publish the agreed shortlist/pairs; the running TUI updates immediately.

Do not start a polling loop. The TUI is manual by default. `R` refreshes it;
`--auto-refresh` exists but should only be used if the user explicitly asks.

## League

- League: `1386392203340840960` (`The BZ`)
- Sleeper user: `grgbntn` / `946594702218842112`
- Team: The Gnarbler
- Format: 14 teams, half-PPR, 16-round snake
- Draft slot: 1
- Keepers: Chris Olave in round 4; Quentin Johnston in round 14
- Lineup: QB, 2 RB, 3 WR, TE, FLEX, K, DEF, 6 bench

Sleeper is the authority for picks and availability. Before the draft,
Sleeper's roster endpoint still contains last year's roster; determine the 2026
team from keeper/draft picks, as the toolkit does.

## Snake-turn strategy

Slot 1 creates these early decision units:

- Pick 1: one selection
- Picks 28+29: a two-player turn
- Picks 56+57: Olave is locked at 56; choose the complement at 57
- Picks 84+85: a two-player turn
- Pick 196 is locked to Quentin Johnston

Treat consecutive picks as one pair decision. Planning for 28+29 begins after
pick 1, when the user asks—not automatically and not only when pick 28 arrives.
Use conversation to refine several pair constructions before the clock starts.
Nobody drafts between the two selections, so decide the pair as a set.

## TUI contract

Launch beside chat:

```sh
uv run fantasy tui
```

The board has two roles:

- Without an authored plan, it displays cached individual evidence.
- When Codex publishes pair proposals, the BOARD tab becomes the proposal
  board. Each pair is three rows: P1 with stats, P2 with stats, then WHY.

Every proposed player is resolved against Sleeper, FantasyPros, and Flock. The
visible columns include FantasyPros ECR/ADP, Flock rank, tier, and bye. Lean
shares across proposals must total 100%; they mean relative preference among
the displayed choices, not independent probabilities.

Publish a pair board like this:

```sh
uv run fantasy publish 'TURN 2 PAIR BOARD // 28 + 29' \
  --body 'Alternative constructions for the same two-player turn.' \
  --pair 'Josh Allen + Tucker Kraft' --lean 40 \
  --reason 'Elite QB edge plus a TE target before the long turn gap.' \
  --pair 'James Cook + Drake London' --lean 35 \
  --reason 'Balanced RB/WR volume.' \
  --pair 'Chase Brown + Trey McBride' --lean 25 \
  --reason 'Secure RB plus an elite-tier tight end.'
```

Repeat `--pair`, `--lean`, and `--reason` in the same order. Explicit lean
values must be non-negative and total 100. Publishing writes atomically to
`data/session/advice.json`; the TUI receives it through `watchfiles` and reloads
the board from the current stored state.

Controls: `B` board, `T` team/turns, `C` transmission history, `N` recent
news checks, `0` all players, `1` QB, `2` RB, `3` WR, `4` TE, `R` manual
Sleeper refresh, `Q` quit.

## Recent news checks

The global `$fantasy-news-check` skill handles decision-time news research. Use
it when the user asks about a player, injury, role, transaction, or recent buzz.
It queries Google News RSS with `when:7d`, verifies stories against a strict
rolling seven-day window, and uses ordinary web search for corroboration. It
then sends only the evidence packet to a fast `gpt-5.6-luna` subagent for an
independent sanity check. Call the first stage Google News, not the full Google
web index.

Search is always manual and conversation-paced. The TUI never runs searches.
After review, cache the result for the NEWS CHECKS dropdown with:

```sh
uv run fantasy news-publish 'Tucker Kraft' \
  --verdict positive --confidence medium \
  --summary 'Recent usage reports modestly improve the role outlook.' \
  --flag 'watch|role|First-team usage increased.' \
  --source 'Camp report|https://example.com/report|2026-08-28' \
  --review pass_with_caveats \
  --reviewer-role fast-sanity \
  --concern 'Camp usage can change.'
```

This upserts the subject in `data/session/news-checks.json`, newest first, and
keeps 25 checks. A running TUI reloads the dropdown immediately.

## Data sources

- `data/sleeper/`: player catalog, league/draft snapshot, filtered/trending feeds
- `data/fantasypros/ecr.json`: 941 Half-PPR ECR rows from the public page
- `data/fantasypros/adp.json`: 338 ADP rows from the public page
- `data/fantasypros/api/`: retained ten-row API samples; do not prefer these
- `data/flock/`: supplied overall/RB/WR editorial rankings
- `data/session/advice.json`: latest assistant-to-TUI transmission
- `data/session/news-checks.json`: manually requested, reviewed seven-day news signals

Canonical player identity is the Sleeper player ID. Never overwrite it with a
FantasyPros ID; provider IDs belong in source-specific fields.

The FantasyPros API key is in `.env`, which is gitignored. Never print it,
write it to data/code, or commit it. The full rankings are already cached; do
not spend API requests unless the user explicitly asks.

## Useful commands

```sh
uv run fantasy status
uv run fantasy refresh --no-players
uv run fantasy assess --json
uv run fantasy assess --position RB
uv run fantasy roster
uv run fantasy plan
uv run fantasy available --position RB --limit 20
uv run fantasy rankings status
```

Use current web research only as a cited, decision-time check for injury, role,
or breaking news. It supplements stored rankings; it does not replace them.
