# Dependency Versions - v2.0.0

## System Dependencies
- **FFmpeg:** 8.0 "Huffman" (with --enable-whisper)
- **Whisper.cpp:** Latest from https://github.com/ggerganov/whisper.cpp
- **Whisper Model:** ggml-base.bin (~140MB)
- **Python:** 3.11

## Python Packages
See requirements.txt for exact versions.

Key packages:
- google-genai: 1.0.0 (for Gemini 3 Flash)
- google-cloud-firestore: 2.19.0
- redis: 5.2.0

## Removed Dependencies
- ❌ openai - Replaced by FFmpeg Whisper integration
- ❌ google-cloud-aiplatform - Replaced by google-genai

## Update Strategy
- Review updates quarterly
- Test in staging before production
- Monitor deprecation warnings
