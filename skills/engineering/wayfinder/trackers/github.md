# Wayfinding on GitHub Issues

The tracker for this repo is **GitHub Issues** in `GH_REPO` (default
`benjamin-wo/deep-agent-coding`). Every wayfinder artifact is a regular issue;
the map and its tickets are distinguished by labels, not a separate system.

All operations below are agent tools: `create_github_issue`,
`list_github_issues`, `get_github_issue`, `update_github_issue`,
`comment_github_issue`. They hit the GitHub REST API with `GH_TOKEN` (the same
fine-grained PAT used for git pushes — it must also have **Issues: Read and
write** on the repo).

## Wayfinding operations

### Create the map
One issue, label `wayfinder:map`, body per the skill's map template
(Destination, Notes, Decisions so far, Not yet specified, Out of scope).

- `create_github_issue(title="<Destination> — wayfinding map", body=<map body>, labels=["wayfinder:map"])`

### Create a ticket
A child issue whose body is the question, labeled `wayfinder:<type>` where
`type` is `research`, `prototype`, `grilling`, or `task`.

- `create_github_issue(title=<ticket name>, body=<question body>, labels=["wayfinder:grilling"])`

### Read issues
- `get_github_issue(number)` — full body, labels, assignee of any issue.
- `list_github_issues(state="open", label="wayfinder")` — all open wayfinder
  issues (pull requests are filtered out automatically). Use label
  `wayfinder:map` to find the map, or no label to scan everything.

### Claim a ticket
GitHub has no native "claim"; assignee is the claim. Assign before any work
so concurrent sessions skip it:

- `update_github_issue(number, assignees=["<your github username>"])`

An open, unassigned wayfinder ticket is unclaimed. To unclaim,
`assignees=[]`.

### Blocking
GitHub issues have **no native dependency relationship** (no blocking edges in
the REST API), so use the body convention: each ticket that depends on others
lists them in a section of its body:

```markdown
## Blocked by
- #<number> — <title of blocker>
```

A ticket is **unblocked** when every issue in its `## Blocked by` list is
closed. The **frontier** is the set of open tickets labeled `wayfinder:*`
(excluding the map) that are unassigned and unblocked.

### Resolve a ticket
1. Post the answer as a resolution comment:
   `comment_github_issue(number, body=<resolution>)`
2. Close it: `update_github_issue(number, state="closed")`
3. Append a line to the map's **Decisions so far**:
   `update_github_issue(map_number, body=<map body with new decision line>)`
   — one line per closed ticket: `- [<title>](<url>) — <one-line gist>`.
4. Graduate fog / add tickets: create-then-wire as normal; edit the map's
   **Not yet specified** to clear graduated patches.

### Rule out of scope
Comment the reason, close the ticket, and add one line to the map's
**Out of scope** section (gist + why, linking the closed ticket). It never
joins **Decisions so far**.

## Concurrency

The map is meant to be worked by several sessions (one Telegram chat each).
Claim-before-work is what keeps them from colliding. When you resolve a
ticket, re-fetch the map before editing it (`get_github_issue(map_number)`) so
you don't clobber a decision another session appended while you worked.
