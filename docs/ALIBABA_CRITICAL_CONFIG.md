# Alibaba Cloud Critical Configuration

**Version:** v3.0.1 | **Updated:** 2026-02-04

---

## ASR Model (Speech Recognition)

| Parameter | Value |
|-----------|-------|
| **Model** | `qwen3-asr-flash` |
| **Snapshot** | `qwen3-asr-flash-2025-09-08` |
| **Languages** | 52 (including Russian) |
| **Protocol** | REST API (NOT WebSocket) |
| **Endpoint** | `https://dashscope-intl.aliyuncs.com/api/v1` |

### Python SDK Call
```python
import dashscope
dashscope.base_http_api_url = 'https://dashscope-intl.aliyuncs.com/api/v1'

response = dashscope.MultiModalConversation.call(
    api_key=api_key,
    model="qwen3-asr-flash",
    messages=[
        {"role": "system", "content": [{"text": ""}]},
        {"role": "user", "content": [{"audio": "data:audio/mpeg;base64,..."}]}
    ],
    result_format="message",
    asr_options={"enable_itn": True}
)
```

### DO NOT USE (Deprecated)
- ~~paraformer-realtime-v2~~ (2024)
- ~~paraformer-v1~~
- ~~dashscope.aliyuncs.com~~ (Beijing, use `-intl`)

---

## LLM Model (Text Formatting)

| Parameter | Value |
|-----------|-------|
| **Model** | `qwen-turbo` (v3.0.1: 2x faster, 3x cheaper than qwen-plus) |
| **Endpoint** | `https://dashscope-intl.aliyuncs.com/api/v1/services/aigc/text-generation/generation` |
| **Fallback** | Gemini 2.5 Flash |
| **Threshold** | 150 words (v3.0.1: increased from 100) |

---

## Environment Variables

| Variable | Required |
|----------|----------|
| `DASHSCOPE_API_KEY` | Yes |
| `WHISPER_BACKEND` | Yes (`qwen-asr`) |
| `TELEGRAM_BOT_TOKEN` | Yes |
| `TABLESTORE_ENDPOINT` | Yes |
| `TABLESTORE_INSTANCE` | Yes |
| `MNS_ENDPOINT` | Yes |
| `GOOGLE_API_KEY` | Optional (Gemini fallback) |

---

## Endpoints (International)

| Service | Endpoint |
|---------|----------|
| DashScope ASR | `https://dashscope-intl.aliyuncs.com/api/v1` |
| DashScope LLM | `https://dashscope-intl.aliyuncs.com/api/v1/services/aigc/text-generation/generation` |
| Tablestore | `https://twbot-prod.eu-central-1.ots.aliyuncs.com` |
| MNS | `https://5907469887573677.mns.eu-central-1.aliyuncs.com` |

---

## FFmpeg Commands (v3.0.1)

```bash
# Standard (audio > 10 sec)
ffmpeg -y -i input.ogg -b:a 32k -ar 16000 -ac 1 -threads 4 output.mp3

# Short audio (< 10 sec) - ultra-light settings
ffmpeg -y -i input.ogg -b:a 24k -ar 8000 -ac 1 -threads 4 output.mp3

# PCM for ASR (if needed)
ffmpeg -y -i input.mp3 -ar 16000 -ac 1 -f s16le -acodec pcm_s16le output.wav
```

**v3.0.1 optimizations:** bitrate 64k->32k, short audio 24k/8kHz, sync threshold 30->60 sec

---

## Troubleshooting

| Error | Cause | Solution |
|-------|-------|----------|
| Model not found | Deprecated model | Use `qwen3-asr-flash` |
| Connection timeout | Wrong endpoint | Use `-intl` endpoint |
| 400 Bad Request | REST format error | Check API format |

---

## Resources

- [Qwen3-ASR Documentation](https://www.alibabacloud.com/help/en/model-studio/qwen-real-time-speech-recognition)
- [DashScope API Reference](https://www.alibabacloud.com/help/en/model-studio/qwen-api-reference/)
- [Qwen3-ASR GitHub](https://github.com/QwenLM/Qwen3-ASR)
