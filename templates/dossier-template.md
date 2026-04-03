# Dossier Template — Option C (YAML frontmatter + Markdown body)

Use this format for all contact dossiers in `~/Documents/Obsidian Vault/👥 People/`.

## Internal Coworker

```markdown
---
contact_type: internal
full_name: "First Last"
preferred_name: "First"
email: "email@company.com"
slack_id: "UXXXXXXXXXX"
slack_handle: "@handle"
title: "Job Title"
team: "Team Name"
department: "Department"
reports_to: "Manager Name"
timezone: "America/Denver"
location: "City, State"
last_updated: "YYYY-MM-DD"
trust_level: high | medium | low
priority: high | medium | low
areas_of_ownership:
  - "Area 1"
  - "Area 2"
go_to_for:
  - "Topic 1"
  - "Topic 2"
preferred_channels:
  - slack_dm
  - slack_channel
---

# Full Name

## Role & Context

What they do, where they sit, what they own. Current scope and responsibilities.

## Communication Playbook

How they communicate. Tone, speed, channel preferences, meeting behavior.
What works, what doesn't. Writing quirks. Pet peeves.

## Working Relationship

How they relate to James. Trust level, alignment, friction points.
Topics of agreement and disagreement. Sensitivities to be aware of.

## Current Focus

What they're actively working on RIGHT NOW. Replace old items, don't append.

## Domain Expertise

What they're strong at. What they're less familiar with.
When to go to them vs. someone else.

## Personal Notes

Only things they've shared openly. Interests, conversation starters.
No speculation on personal life beyond what's been directly stated.

## Open Items

Active threads, commitments made (by them or to them), blockers.
Remove items once resolved.
```

## Vendor Contact

Same frontmatter pattern, but add:
```yaml
company: "Vendor Co"
relationship_status: active | churned | evaluating | paused
contract_term: "12 months"
renewal_date: "YYYY-MM-DD"
```

And replace "Working Relationship" with "Vendor Context" and "Negotiation Notes".

## Customer Contact

Same frontmatter pattern, but add:
```yaml
company: "Customer Co"
account_tier: enterprise | growth | starter | trial
health_score: green | yellow | red
mrr: "$X,XXX"
renewal_date: "YYYY-MM-DD"
```

And add "Account Context" and "Relationship Dynamics" sections.

## LLM Merge Rules

When updating a dossier:

1. ONLY include information the person has directly stated or that is clearly
   observable from their behavior. Never infer personal details.

2. For communication style, base observations on patterns across multiple
   interactions. Note the channel (Slack vs email vs meeting).

3. For sensitivities, only flag things that affect professional interaction.
   Frame as actionable guidance, not gossip.

4. Distinguish between what someone SAID they would do (commitments) and
   what you THINK they should do (recommendations).

5. If confidence on any field is low, use "~" prefix for approximations
   or omit entirely. Don't guess.

6. "Current Focus" gets REPLACED with what's current. Not appended.

7. "Open Items" should only contain items that are still open.

8. No em dashes. Use commas or periods instead.

9. The dossier is a LIVING PROFILE, not a journal. It should read like
   "who is this person right now" not "what happened on each date."
