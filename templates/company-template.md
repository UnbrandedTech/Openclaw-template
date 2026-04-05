# Company Profile Template

Use this format for all client/vendor/partner profiles in `~/Documents/Obsidian Vault/Clients/`.

```markdown
---
company_name: "Company Name"
type: client | vendor | partner
domain: "company.com"
key_contacts:
  - "[[Contact Name]]"
relationship_status: active | churned | evaluating | paused
slack_channels:
  - "channel-name"
meeting_cadence: "weekly | biweekly | monthly | ad-hoc"
last_updated: "YYYY-MM-DD"
---

# Company Name

## Overview

What the company does, how they relate to us, scope of the engagement.

## Active Engagements

Current projects, deliverables, timelines. Replace old items, don't append.

## Communication

Primary Slack channels, meeting cadence, key decision makers,
preferred communication style. Who to go to for what.

## Key Contacts

Brief notes on each contact and their role in the relationship.
Link to their individual dossier in People/ for full details.

## Notes

Notable context about the relationship. Only include facts
that are directly relevant to working with this company.
No speculation. No em dashes.
```

## LLM Merge Rules

1. ONLY include information from actual interactions (Slack, meetings, emails).
2. "Active Engagements" gets REPLACED with current state, not appended.
3. Relationship status should reflect the actual current state.
4. No em dashes. Use commas or periods instead.
5. The profile is a LIVING DOCUMENT, not a log. It should read as
   "who is this company right now" not "history of interactions."
