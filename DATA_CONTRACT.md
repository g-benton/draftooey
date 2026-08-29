# Fantasy data and interaction contract

## Purpose

This project supports a conversation between the user and Codex during a
Sleeper draft. Provider data supplies evidence; Codex and the user decide the
strategy; the TUI displays the resulting proposals.

The interaction is deliberately conversation-paced. Nothing should invent or
publish a new strategy merely because a timer fired.

## Provider data

All provider data lives below `data/<source>/`. Sleeper IDs are canonical.
FantasyPros IDs must remain `fantasypros_id`/`source_ids["fantasypros"]`; they
must never replace the Sleeper ID during a ranking merge.

Rankings are observations with an explicit source and kind:

- FantasyPros `ecr`
- FantasyPros `adp`
- Flock `editorial`

FantasyPros page data also supplies tier, bye, expert average, and expert range.
Sleeper supplies player metadata, age, experience, team, injury, draft picks,
keepers, and availability.

## Assessment data

`fantasy.assessment.build_assessment()` combines the cached Sleeper snapshot
with rankings and produces:

- current and upcoming snake turns;
- roster and open starter positions;
- available individual candidates and supporting evidence;
- relative recommendation factors used as discussion input;
- keeper-locked slots.

This output is evidence, not an automatically approved recommendation.

## Assistant-to-TUI message

`AdviceMessage` in `fantasy/contracts.py` is serialized atomically to
`data/session/advice.json`:

```json
{
  "schema_version": 1,
  "published_at": "...",
  "based_on_pick": 28,
  "headline": "TURN 2 PAIR BOARD // 28 + 29",
  "body": "Alternative constructions for this turn.",
  "players": [],
  "proposals": [
    {
      "share": 40,
      "players": ["Josh Allen", "Tucker Kraft"],
      "reason": "Elite QB edge plus a TE target."
    }
  ]
}
```

Rules:

- Proposal shares use one denominator and total 100.
- Shares express relative lean among displayed proposals, not win probability.
- A paired turn proposal normally contains exactly two players.
- The reason explains the construction, opportunity cost, or pivot logic.
- `based_on_pick` identifies the Sleeper state used for the recommendation.
- New writes replace the current board but remain in the running TUI's history.

## TUI behavior

The TUI watches the session file with `watchfiles`:

- a normal assessment renders an individual evidence table;
- an advice message with proposals replaces it with an authored proposal board;
- each pair renders as P1 evidence, P2 evidence, and a WHY row;
- proposal names are joined back to available player evidence at render time;
- a publish event reloads both the transmission and the cached assessment;
- Sleeper is not refreshed merely because advice changed.

The default TUI makes no timed network requests. Sleeper refresh happens when
the user presses `R` or Codex explicitly runs `refresh --no-players` in response
to the conversation.

## Recent-news signal

`NewsCheck` in `fantasy/contracts.py` is an on-request research artifact. The
search/review agent writes it through `fantasy news-publish`; the TUI only reads
the cache and never initiates network activity. Checks are wrapped in
`data/session/news-checks.json`:

```json
{
  "schema_version": 1,
  "updated_at": "...",
  "checks": [
    {
      "schema_version": 1,
      "subject": "Tucker Kraft",
      "query": "Tucker Kraft Packers injury role",
      "checked_at": "...",
      "window_days": 7,
      "verdict": "positive",
      "confidence": "medium",
      "summary": "Recent usage reports modestly improve the role outlook.",
      "flags": [{"severity": "watch", "type": "role", "claim": "First-team usage increased."}],
      "sources": [{"title": "Camp report", "url": "https://example.com/report", "published_at": "2026-08-28", "supports": ""}],
      "reviewer": {"role": "fast-sanity", "outcome": "pass_with_caveats", "concerns": ["Camp usage can change."]}
    }
  ]
}
```

Allowed verdicts are `positive`, `neutral`, `negative`, `mixed`, and
`uncertain`; they describe near-term fantasy impact, not generic media tone.
Confidence is `low`, `medium`, or `high`. Checks are upserted case-insensitively
by subject, ordered newest first, and capped at 25. Sources must be direct URLs
from the seven-day evidence window. Reviewer caveats remain visible in the TUI.

## Breaking changes

Increment `schema_version` only for incompatible serialized changes. Consumers
should ignore unknown fields and reject unsupported major versions.
