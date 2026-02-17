# Alibaba Cloud Critical Configuration

**v4.3.0** | 2026-02-17

Env vars & architecture: see [CLAUDE.md](../CLAUDE.md)

---

## ASR

| Parameter | Value |
|-----------|-------|
| Model | `qwen3-asr-flash` |
| Languages | 52 (incl. Russian) |
| Protocol | REST (NOT WebSocket) |
| Endpoint | `https://dashscope-intl.aliyuncs.com/api/v1` |
| Chunking | Auto-split >150s |

**Never use:** ~~paraformer-v1/v2~~, ~~dashscope.aliyuncs.com~~ (Beijing)

---

## Diarization (multi-backend)

Two parallel async passes, single OSS upload:

| Pass | Model | Purpose | Russian |
|------|-------|---------|---------|
| 1 (speakers) | `fun-asr-mtl` | Speaker labels + timestamps | No |
| 2 (text) | `qwen3-asr-flash-filetrans` | Accurate text + timestamps | Yes |

| Parameter | Value |
|-----------|-------|
| Protocol | Async API (`X-DashScope-Async: enable`) |
| Endpoint | `https://dashscope-intl.aliyuncs.com/api/v1/services/audio/asr/transcription` |
| Input (fun-asr-mtl) | `file_urls` (list) |
| Input (qwen3-filetrans) | `file_url` (string) |
| Language (fun-asr-mtl) | `language_hints: ["ru"]` (list) |
| Language (qwen3-filetrans) | `language: "ru"` (string) |
| Poll | 5s interval, 240s max |

Flow: OSS upload → parallel passes → poll → merge by timestamps → OSS cleanup

**Never use:** `paraformer-v2` (China-only, "Model not exist" on intl)

### Backend Routing

| Backend | Model | Env | Fallback |
|---------|-------|-----|----------|
| `dashscope` (default) | fun-asr-mtl + qwen3-asr-flash-filetrans | `DASHSCOPE_API_KEY` | — |
| `assemblyai` | Universal-2 | `ASSEMBLYAI_API_KEY` | → dashscope |
| `gemini` | Gemini 2.5 Flash | `GOOGLE_API_KEY` | → dashscope |

Set via `DIARIZATION_BACKEND` env var. All backends → same segment format.

---

## LLM

| Parameter | Value |
|-----------|-------|
| Model | `qwen-turbo` |
| Endpoint | `https://dashscope-intl.aliyuncs.com/api/v1/services/aigc/text-generation/generation` |
| Fallback | Gemini 2.5 Flash |
| Skip threshold | text <=100 chars |

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

---

## Resources

- [Qwen3-ASR Docs](https://www.alibabacloud.com/help/en/model-studio/qwen-real-time-speech-recognition)
- [DashScope API](https://www.alibabacloud.com/help/en/model-studio/qwen-api-reference/)

---

*v4.3.0*
