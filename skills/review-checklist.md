# Document review checklist (draft — tailor to your actual review type)

**This is a generic starting point, not a finished skill.** The
`perform-reviews` workspace has no MCP tools attached (`mcp_group: none`) —
its whole job is UC2 (read a document, ask questions, write structured
feedback), so what makes a *good* review depends entirely on what's being
reviewed: a manuscript, a beamtime/proposal, an internal report, or
something else. Edit the criteria below (or replace them outright) to match
that before relying on this skill for real reviews. Everything after this
paragraph is a reasonable default for a scientific document, not a
one-size-fits-all checklist.

## Workflow

1. Read the document(s) dropped into your source folder in full before
   commenting — don't review from a partial read of the first section.
2. Work through the checklist below, noting concrete evidence (a page/
   section/paragraph reference, a quoted claim) for every point raised —
   not vague impressions.
3. Write the review as a Markdown report (`write_markdown_report`) into
   your target folder: one section per checklist category below, each with
   concrete findings; a short overall recommendation at the top.
4. Flag anything you're not confident judging (a domain claim outside your
   knowledge, a citation you can't verify) rather than guessing — say what
   you couldn't check and why.

## Checklist

### Scope and claims
- Does the abstract/summary accurately represent what the document actually
  shows? Overclaiming (a "novel" method that's a known variant, a "proves"
  where the evidence only supports "is consistent with") is worth flagging
  explicitly.
- Are the stated goals/questions actually answered by the end, or does the
  document drift?

### Methods and reproducibility
- Is there enough detail that the work could be reproduced or checked
  independently (instrument/settings, sample prep, software/version,
  analysis parameters)?
- Are units, conventions, and terminology used consistently and correctly
  throughout (see the `saxs-basics` skill if this is SAXS/USAXS-related
  work)?

### Data and statistical validity
- Do error bars/uncertainties appear where they should, and are they
  described (statistical counting error vs. systematic, standard deviation
  vs. standard error, N samples)?
- Are outliers or excluded data points disclosed and justified, not simply
  dropped without comment?
- Do figures and tables match the text's description of them (right axis
  labels/units, right sample identified, numbers that agree)?

### Clarity and structure
- Can a reader follow the logical flow section to section?
- Are figures/tables self-contained (a reader shouldn't need to hunt
  through the text to know what a plot's axes mean)?
- Is jargon defined on first use, or assumed without introduction?

### Prior work and context
- Are relevant prior results (the field's, or the same group's own earlier
  work) cited and correctly characterized, not just cited in passing?
- Does the document overlook an obvious alternative explanation or
  competing method that a reviewer would expect addressed?

## Output format

Structure the written report as:

```markdown
# Review: <document title>

## Recommendation
<one paragraph: accept / accept with minor revisions / major revisions / reject, and why>

## Scope and claims
...

## Methods and reproducibility
...

## Data and statistical validity
...

## Clarity and structure
...

## Prior work and context
...

## Specific line/page comments
- <page/section>: <comment>
```
