# Alibaba Cloud Critical Configuration

**v3.6.0** | 2026-02-07

---

## ASR (Speech Recognition)

| Parameter | Value |
|-----------|-------|
| Model | `qwen3-asr-flash` (`qwen3-asr-flash-2025-09-08`) |
| Languages | 52 (incl. Russian) |
| Protocol | REST API (NOT WebSocket) |
| Endpoint | `https://dashscope-intl.aliyuncs.com/api/v1` |
| Chunking | Auto-split audio >150s |

**DO NOT USE:** ~~paraformer-v1/v2~~, ~~dashscope.aliyuncs.com~~ (Beijing)

---

## Diarization (v3.6.0)

| Parameter | Value |
|-----------|-------|
| Model | `fun-asr` |
| Protocol | Async API (`X-DashScope-Async: enable`) |
| Endpoint | `https://dashscope-intl.aliyuncs.com/api/v1/services/audio/asr/transcription` |
| Input | File URL (signed OSS URL, NOT base64) |
| Poll interval | 5s, max 5 min |

Flow: upload to OSS → POST async → poll → fetch result → cleanup OSS

Requires: `OSS_BUCKET`, `OSS_ENDPOINT`, `ALIBABA_ACCESS_KEY/SECRET_KEY`

---

## LLM (Text Formatting)

| Parameter | Value |
|-----------|-------|
| Model | `qwen-turbo` (2x faster, 3x cheaper than qwen-plus) |
| Endpoint | `https://dashscope-intl.aliyuncs.com/api/v1/services/aigc/text-generation/generation` |
| Fallback | Gemini 2.5 Flash |
| Threshold | Skip LLM for text ≤100 chars |

---

## All Endpoints

| Service | Endpoint |
|---------|----------|
| DashScope ASR/LLM | `https://dashscope-intl.aliyuncs.com/api/v1` |
| Tablestore | `https://twbot-prod.eu-central-1.ots.aliyuncs.com` |
| MNS | `https://5907469887573677.mns.eu-central-1.aliyuncs.com` |
| OSS | `oss-eu-central-1.aliyuncs.com` |

---

## FFmpeg

```bash
# Standard (>10s)
ffmpeg -y -i input.ogg -b:a 32k -ar 16000 -ac 1 -threads 4 output.mp3

# Short (<10s)
ffmpeg -y -i input.ogg -b:a 24k -ar 8000 -ac 1 -threads 4 output.mp3
```

Optimizations: bitrate 64k→32k, short audio 24k/8kHz, sync threshold 60s.

---

## Resources

- [Qwen3-ASR Docs](https://www.alibabacloud.com/help/en/model-studio/qwen-real-time-speech-recognition)
- [DashScope API](https://www.alibabacloud.com/help/en/model-studio/qwen-api-reference/)

---

*v3.6.0*
