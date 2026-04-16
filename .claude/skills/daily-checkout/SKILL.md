---
name: daily-checkout
description: Generates a daily narrative checkout from a user's GitHub activity. Produces ready-to-post Slack, LinkedIn, and X copy by inferring the intent and story arc behind commits, PRs, and reviews. Use when summarizing end-of-day GitHub activity into polished social media posts or team standups.
argument-hint: "[--date YYYY-MM-DD] [--user USERNAME] [--hours N] [--channel slack|linkedin|x]"
disable-model-invocation: true
allowed-tools: Bash(gh *)
---

# Daily checkout

Arguments: $ARGUMENTS

Authenticated GitHub user:

```!
gh api /user --jq '.login'
```

Raw GitHub activity for the authenticated user (last 100 events):

```!
gh api "/users/$(gh api /user --jq '.login')/events?per_page=100" --jq '.[] | "\(.created_at)  \(.type)  \(.repo.name)  \(if .type == "PushEvent" then (.payload.commits // [] | map(.message | split("\n")[0]) | join(" | ")) elif .type == "PullRequestEvent" then "\(.payload.action): \(.payload.pull_request.title)" elif .type == "PullRequestReviewEvent" then "review(\(.payload.review.state)): \(.payload.pull_request.title)" elif .type == "IssuesEvent" then "\(.payload.action): \(.payload.issue.title)" elif .type == "IssueCommentEvent" then "comment on: \(.payload.issue.title // "?")" else "(other)" end)"'
```

---

Your task is to turn the activity above into a narrative checkout. Work through three stages. Only output the final posts.

If `--user` was passed in the arguments and it differs from the authenticated user above, fetch that user's events yourself using `gh api "/users/{username}/events?per_page=100"` with the same jq filter, and use that data instead.

## Error handling

Before proceeding, check for these conditions and respond accordingly:

- **No events found in the time window**: Output a single message — "No GitHub activity found for the specified window. Nothing to report." Do not fabricate activity.
- **User not found** (404 from API): Output — "GitHub user '{username}' not found. Please check the username and try again."
- **API rate limit hit** (403 or 429): Output — "GitHub API rate limit reached. Try again in a few minutes or authenticate with a token that has higher limits."
- **Empty or malformed event list**: If the API returns but no events match the filter, treat as "no events found" above.

## Stage 1 — Signal extraction (silent)

Filter to the window in `arguments` (if `--date`: that UTC day; if `--hours N`: last N hours; default: last 24 hours). Derive:

- Primary repo and which others were touched
- Commit themes from prefixes: `fix` · `feat` · `refactor` · `test` · `chore` · `docs`
- Work mode: e.g. `focused` (one repo, deep commits), `collaborative` (heavy review/comment activity)
- Key concepts: 3–5 meaningful nouns from commit messages
- PR momentum: opening, merging, or reviewing
- Collaboration share: proportion of reviews and comments

**Validation checkpoint**: Confirm at least one event falls in the requested window. If not, stop and report no activity (see error handling above).

Keep this internal.

## Stage 2 — Intent inference (silent)

Infer the underlying intent — not what was done, but what the engineer was trying to accomplish:

- **primary_intent** — e.g. `shipping_capability`, `paying_down_debt`
- **emotional_register** — e.g. `methodical`, `exploratory`
- **protagonist_arc** — one sentence: *"What was this engineer in the middle of today?"* — story framing, not a task list
- **tension** (optional) — the constraint or problem that drove the work
- **resolution** (optional) — what was resolved or meaningfully advanced
- **anchor_concepts** — 3–5 domain concepts worth naming
- **named_artifacts** — repos, PRs, or issues worth mentioning (only meaningful ones)

Keep this internal.

## Stage 3 — Narrative posts

Write posts for each channel. If `--channel` was passed, write only that channel. Otherwise write all three.

**Rules for all channels:**
- Tell a story with a point of view — never list what was done
- Build from the protagonist arc; do not restate it verbatim
- Use anchor_concepts and named_artifacts naturally — do not force all of them in
- Never open with "Today I" or a bare task summary
- Do not use hashtags unless the channel is LinkedIn or X
- Never invent work not evidenced in the raw activity

---

### Slack

- **Length**: 3–6 sentences, conversational and direct
- **Tone**: Team-facing, collegial — written for people who already know the project context
- **Format**: Plain prose, no markdown headers; emoji optional and sparing
- **Goal**: Give teammates a clear sense of where you landed and what's moving next
- **Example output**:
  > Spent most of today pushing the auth refactor across the finish line — the token refresh edge cases were gnarlier than expected but they're handled now. Opened a PR for review; if it lands tomorrow we unblock the mobile team. Also left a few comments on @sam's caching work — looks solid.

---

### LinkedIn

- **Length**: 4–8 sentences across 2–3 short paragraphs
- **Tone**: Professional but human — thoughtful, not self-promotional; first-person narrative voice
- **Format**: Short paragraphs with line breaks; 2–4 relevant hashtags at the end
- **Goal**: Frame the day's work as a meaningful engineering story a broader professional audience can appreciate
- **Avoid**: Hype language ("crushed it", "killed it"), vague claims ("exciting work"), or over-explaining technical details
- **Example output**:
  > Some days the work is about shipping features. Today was about paying off a debt quietly accumulated over six months — a token refresh system that worked until it didn't.
  >
  > Refactored the auth layer, handled a handful of tricky edge cases, and opened a PR that should unblock a cross-team dependency. Not glamorous, but the kind of work that makes future shipping faster.
  >
  > #softwaredevelopment #engineering #refactoring

---

### X

- **Length**: 1–3 posts; each post ≤ 280 characters; thread if needed
- **Tone**: Pithy, opinionated, direct — write for a technical audience who values signal over noise
- **Format**: Plain text; optional 1–2 hashtags maximum; no corporate speak
- **Goal**: Crystallise the day into a sharp, shareable observation or moment
- **Avoid**: Threads longer than 3 posts; padding to fill character count
- **Example output**:
  > Six months of "we'll fix it later" finally came due today. Rewrote the auth token refresh from scratch. Turns out the edge cases were the whole job. PR is up. 🧵
  >
  > *(if threading)* The tricky part wasn't the code — it was understanding why the original design made sense at the time. Refactoring without that context is just rewriting bugs.

---

**Output format**: Label each section clearly (`## Slack`, `## LinkedIn`, `## X`) and write only the post copy under each label. No commentary, no stage summaries, no meta-notes.