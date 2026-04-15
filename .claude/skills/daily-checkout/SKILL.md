---
name: daily-checkout
description: Generate a daily narrative checkout from your GitHub activity. Produces ready-to-post Slack, LinkedIn, and X copy by inferring the intent and story arc behind your commits, PRs, and reviews. Run it at end of day.
argument-hint: "[--date YYYY-MM-DD] [--user USERNAME] [--hours N] [--channel slack|linkedin|x]"
disable-model-invocation: true
allowed-tools: "Bash(gh:*)"
---

# Pisto — daily checkout

Raw GitHub activity (fetched live):

```!
ARGS="$ARGUMENTS"
USER=$(echo "$ARGS" | awk 'match($0, /--user ([^ ]+)/, a) {print a[1]}')
[ -z "$USER" ] && USER=$(gh api /user --jq '.login')
echo "user: $USER"
echo "fetched: $(date -u +%Y-%m-%dT%H:%M:%SZ)"
echo "arguments: $ARGS"
echo "---"
gh api "/users/$USER/events?per_page=100" \
  --jq '.[] | "\(.created_at)  \(.type)  \(.repo.name)  \(
    if .type == "PushEvent" then
      (.payload.commits // [] | map(.message | split("\n")[0]) | join(" | "))
    elif .type == "PullRequestEvent" then
      "\(.payload.action): \(.payload.pull_request.title)"
    elif .type == "PullRequestReviewEvent" then
      "review(\(.payload.review.state)): \(.payload.pull_request.title)"
    elif .type == "IssuesEvent" then
      "\(.payload.action): \(.payload.issue.title)"
    elif .type == "IssueCommentEvent" then
      "comment on: \(.payload.issue.title // \"?\")"
    else "(other)" end
  )"'
```

---

Your task is to turn the activity above into a narrative checkout. Work through three stages. Only output the final posts.

## Stage 2 — Signal extraction (silent)

Identify what kind of day this was from the raw events. Filter to the window requested in `arguments` (if `--date` given: that UTC day; if `--hours N`: last N hours; default: last 24 hours).

Derive:
- Which repos were touched, and which was primary
- Commit themes: `fix` · `feat` · `refactor` · `test` · `chore` · `docs` (from commit message prefixes)
- Work mode: `focused` · `scattered` · `collaborative` · `maintenance` · `exploratory` · `mixed`
- Key concepts: the 3–5 meaningful nouns from commit messages (skip stop words)
- PR momentum: were PRs being opened, merged, or reviewed?
- Collaboration signal: what share of activity was reviews and comments?

Keep this internal.

## Stage 3 — Intent inference (silent)

From the signals, infer the underlying intent — not what was done, but what the engineer was trying to accomplish.

Identify:
- **primary_intent** — `hardening_boundary` · `shipping_capability` · `paying_down_debt` · `exploring_unknown` · `enabling_team` · `stabilising_prod` · `laying_foundations` · `consolidating_gains`
- **emotional_register** — `urgent` · `exploratory` · `methodical` · `generative` · `collaborative`
- **protagonist_arc** — one sentence: *"What was this engineer in the middle of today?"* — story framing, not a task list
- **tension** (optional) — the constraint or problem that drove the work
- **resolution** (optional) — what was resolved or meaningfully advanced
- **anchor_concepts** — 3–5 domain concepts worth naming
- **named_artifacts** — repos, PRs, or issues worth mentioning by name (only meaningful ones)

Keep this internal.

## Stage 4 — Narrative posts

Write posts for each channel. If `--channel` was passed, write only that channel. Otherwise write all three.

**Rules for all channels:**
- Tell a story with a point of view — never list what was done
- Build from the protagonist arc; do not restate it verbatim
- Use anchor_concepts and named_artifacts naturally — do not force all of them in
- Never open with "Today I worked on" or "I spent time"
- Never use the word "delve" or the phrase "it's worth noting"
- First person throughout — past tense for events, present tense for insights

---

**SLACK** (internal team standup)
- 80–130 words · one paragraph · no hashtags · emoji only if it adds meaning
- Candid and direct — talking to people who know the codebase
- End with what's next or what's still open

**LINKEDIN** (public, peers + tech community)
- 150–220 words · two or three short paragraphs
  - First: the insight or tension
  - Second: what the work revealed
  - Third (optional): broader takeaway or open question
- End with 2–4 relevant hashtags
- Reflective and specific — a CTO building in public, not self-promotional

**X / Twitter** (public, punchy)
- ≤280 characters · one or two sentences max
- First sentence: the hook. Optional second: tension or twist
- No hashtags unless they earn their place in the character count
- An engineer who notices things others miss

---

## Output

```
────────────────────────────────────────────────────
PISTO DAILY CHECKOUT · {username} · {date}
arc: {protagonist_arc}
────────────────────────────────────────────────────

── SLACK ──
{post}

── LINKEDIN ──
{post}

── X ──
{post}

────────────────────────────────────────────────────
```

If there is no activity in the requested window, say so briefly and skip the posts.
