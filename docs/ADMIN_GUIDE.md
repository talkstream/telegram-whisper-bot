# Admin Guide

All commands: OWNER_ID only. Full command list: [CLAUDE.md#commands](../CLAUDE.md#commands)

## Commands

| Command | Action |
|---------|--------|
| `/user [search] [--page N]` | Search by username/name/ID |
| `/credit <id> <min>` | Add/remove minutes |
| `/stat` | Users, transcriptions, duration |
| `/cost` | ASR/LLM costs by period |
| `/metrics [hours]` | Performance (TTFT, success rate) |
| `/status` | Queue: pending/processing/completed/failed |
| `/flush` | Clear stuck jobs (>10 min processing) |
| `/batch [user_id]` | User's jobs |
| `/export users\|logs\|payments <days>` | CSV export |
| `/report daily\|weekly` | Summary report |
| `/mute [hours\|off]` | Error notification control |
| `/debug` | Toggle diarization debug output |

Approve trial = 15 min + notification to user.

## Auto-notifications

| Event | Recipient |
|-------|-----------|
| Balance < 5 min | User |
| New trial request | Admin |
| >3 consecutive failures | Admin |
| Queue > 100 pending | Admin |
| Any ERROR log | Admin (TelegramErrorHandler, 60s cooldown) |

## Monitoring

```bash
s logs --tail webhook-handler
s logs --tail audio-processor
```

| Metric | Normal | Alert |
|--------|--------|-------|
| Cold start | < 3s | > 5s |
| Processing | < 30s/min | > 60s/min |
| Success rate | > 95% | < 90% |
| Queue depth | < 10 | > 50 |

## Common Operations

```
Compensate user:    /user <name> → /credit <id> <min>
Debug user issue:   /user <name> → /batch <id> → /export logs 1
Stuck queue:        /status → /flush → /status
Emergency shutdown: curl -X POST ".../deleteWebhook"
Restore:            curl -X POST ".../setWebhook" -d '{"url":"..."}'
```

## Security

- Never `/credit` without reason
- Never `/flush` without `/status` first
- No export to unsecured devices
- All actions logged (user_id, action, params, timestamp)

---

*v4.0.0*
