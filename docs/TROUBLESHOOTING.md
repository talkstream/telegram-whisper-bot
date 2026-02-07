# Troubleshooting

## ASR

| Error | Cause | Fix |
|-------|-------|-----|
| Model not found | Deprecated model | Use `qwen3-asr-flash` |
| Connection timeout | Wrong endpoint | Use `dashscope-intl.aliyuncs.com` |
| 400 Bad Request | WebSocket instead of REST | REST: `/api/v1/services/audio/asr/transcription` |
| Audio too short | < 0.5 sec | Check duration before sending |

## Diarization (Fun-ASR)

| Error | Cause | Fix |
|-------|-------|-----|
| OSS upload failed | Wrong credentials/bucket | Check `OSS_BUCKET`, `OSS_ENDPOINT`, access keys |
| Diarization timeout | Audio too long / API slow | Max poll 5 min; fallback to Qwen3-ASR auto |
| No speakers detected | Single speaker / noise | Expected — returns single segment |
| `TASK_FAILED` | Unsupported format / corrupt | Check FFmpeg output, try re-encoding |

## Tablestore

| Error | Cause | Fix |
|-------|-------|-----|
| OTSConditionCheckFail | Row exists mismatch | Use correct Condition |
| Invalid update_columns | `'PUT'` vs `'put'` | **Always lowercase `'put'`** |
| Primary key mismatch | int vs str | `[('user_id', str(user_id))]` |

Conditions: `EXPECT_NOT_EXIST` (create), `EXPECT_EXIST` (update), `IGNORE` (upsert)

## MNS

| Error | Cause | Fix |
|-------|-------|-----|
| Queue not found | Wrong name/region | Check MNS Console |
| Message too large | > 64KB | Pass metadata only, not audio bytes |
| Jobs stuck | Worker crashed | `/status` → `/flush` |

## Telegram

| Error | Cause | Fix |
|-------|-------|-----|
| Message too long | > 4096 chars | Split at 4000 chars or send as file |
| Bot blocked | User blocked bot | Ignore, don't retry |
| File too big | > 20MB | Show error to user |

## FFmpeg

| Error | Cause | Fix |
|-------|-------|-----|
| No such file | FFmpeg not installed | Add FFmpeg layer |
| Invalid data | Corrupt file | `ffprobe -v error` before processing |
| Empty output | Unsupported codec | `-b:a 32k -ar 16000 -ac 1` |

Config: see [ALIBABA_CRITICAL_CONFIG.md](ALIBABA_CRITICAL_CONFIG.md#ffmpeg)

## Deploy

| Error | Cause | Fix |
|-------|-------|-----|
| Function not found | Wrong name in s.yaml | Check `resources:` names |
| Timeout | Large package | Use `.fcignore` |
| Permission denied | Missing policies | `AliyunFCFullAccess`, `AliyunOTSFullAccess`, `AliyunMNSFullAccess` |
| 502 after deploy | Module import error | Check `pythonjsonlogger` fallback in utility.py |

## Diagnostics

```bash
# Webhook
curl "https://api.telegram.org/bot${BOT_TOKEN}/getWebhookInfo"

# Logs
s logs webhook-handler --tail
s logs audio-processor --tail

# Admin
/status   # queue
/metrics  # performance
/cost     # expenses
```

## Lessons Learned

1. REST > WebSocket for serverless (qwen3-asr-flash)
2. Always `-intl` endpoints
3. `pythonjsonlogger` NOT available on FC runtime — use try/except fallback
4. `logConfig: auto` works for initial SLS setup, then switch to explicit config
5. 512MB sufficient for webhook with lazy imports

---

*v3.6.0*
