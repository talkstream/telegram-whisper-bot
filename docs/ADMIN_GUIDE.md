# Admin Guide

Admin commands: OWNER_ID only.

## Commands

### Users
| Command | Description |
|---------|-------------|
| `/user [search] [--page N]` | Search by username/name/ID |
| `/credit <id> <min>` | Add/remove minutes |

### Trials
| Command | Description |
|---------|-------------|
| `/review_trials` | Pending requests (inline ✅/❌) |

Approve = 15 min + notification to user.

### Statistics
| Command | Description |
|---------|-------------|
| `/stat` | Users, transcriptions, duration |
| `/cost` | ASR/LLM costs by period |
| `/metrics [hours]` | Performance (TTFT, success rate, languages) |

### Queue
| Command | Description |
|---------|-------------|
| `/status` | Pending/processing/completed/failed |
| `/flush` | Clear stuck jobs (>10 min processing) |
| `/batch [user_id]` | User's jobs |

### Export & Reports
| Command | Description |
|---------|-------------|
| `/export users\|logs\|payments <days>` | CSV export |
| `/report daily\|weekly` | Summary report |

### System (v4.0.0)
| Command | Description |
|---------|-------------|
| `/mute` | Show notification status |
| `/mute <hours>` | Mute error notifications |
| `/mute off` | Unmute |
| `/debug` | Toggle diarization debug output |

## Auto-notifications

| Event | Recipient |
|-------|-----------|
| Balance < 5 min | User |
| New trial request | Admin |
| >3 consecutive failures | Admin |
| Queue > 100 pending | Admin |
| Any ERROR log | Admin (via TelegramErrorHandler, 60s cooldown) |

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
# Compensate user:       /user <name> → /credit <id> <min>
# Debug user issue:      /user <name> → /batch <id> → /export logs 1
# Stuck queue:           /status → /flush → /status
# Emergency shutdown:    curl -X POST ".../deleteWebhook"
# Restore:               curl -X POST ".../setWebhook" -d '{"url":"..."}'
```

## Security

- Never `/credit` without reason
- Never `/flush` without `/status`
- No export to unsecured devices
- All actions logged (user_id, action, params, timestamp)

---

*v4.0.0*
