# HEARTBEAT.md — Ambient Monitoring

Heartbeat runs every 30 min. Keep it LIGHTWEIGHT (<30 seconds of thinking).

## What to do each beat

1. **Check heartbeat state** at `memory/heartbeat-state.json` for last check times
2. Run the checks below
3. Update state file with timestamps
4. If nothing needs attention → HEARTBEAT_OK

## Priority: Calendar (every beat)

Check for meetings in the next 90 min:
- `khal list now 90m`
- If a meeting is coming up and no prep has been sent, build a prep brief

## Secondary: Quick Glance (only if >1h since last check)

- Skim Slack DMs for anything directed at you that needs response
- Only alert if someone's clearly waiting

## What NOT to do on heartbeat

- Heavy API calls
- Multi-file analysis
- Long-form writing

## Quiet Hours

- 22:00–07:00: HEARTBEAT_OK unless genuinely urgent
- If user hasn't messaged in >4h during work hours, don't push notifications
