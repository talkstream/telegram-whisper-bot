# Developer Guide

Canonical reference: [CLAUDE.md](../CLAUDE.md) (architecture, env vars, database, endpoints, patterns)

## Stack

| Component | Technology |
|-----------|------------|
| Runtime | Python 3.10 |
| Cloud | Alibaba FC 3.0 (eu-central-1) |
| Database | Tablestore |
| Queue | MNS |
| Storage | OSS |
| ASR | Qwen3-ASR-Flash + Fun-ASR |
| LLM | Qwen-turbo (fallback: Gemini 2.5 Flash) |
| Deploy | Serverless Devs (`s`) |

## Local Setup

```bash
git clone https://github.com/talkstream/telegram-whisper-bot.git
cd telegram-whisper-bot
python3.10 -m venv venv && source venv/bin/activate
pip install -r alibaba/webhook-handler/requirements.txt
pip install -r alibaba/audio-processor/requirements.txt
```

Env vars: see [CLAUDE.md#environment-variables](../CLAUDE.md#environment-variables)

## Deploy

See [DEPLOY.md](../alibaba/DEPLOY.md)

## Testing

```bash
pytest alibaba/tests/ -v
```

230 tests (v4.3.0).

## Fork

1. `gh repo fork talkstream/telegram-whisper-bot --clone`
2. Create bot via [@BotFather](https://t.me/BotFather)
3. Set up Alibaba Cloud (FC, Tablestore, MNS, OSS, DashScope)
4. `cp alibaba/s.yaml.example alibaba/s.yaml` → edit
5. `cd alibaba && s deploy -y`

## Monitoring

```bash
s logs --tail webhook-handler
s logs --tail audio-processor
```

Admin commands: see [ADMIN_GUIDE.md](ADMIN_GUIDE.md)
Troubleshooting: see [TROUBLESHOOTING.md](TROUBLESHOOTING.md)

---

*v4.3.0*
