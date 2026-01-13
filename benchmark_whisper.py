#!/usr/bin/env python3
"""
Benchmark FFmpeg Whisper vs OpenAI API performance
"""
import time
import os
from services.audio import AudioService

def benchmark_transcription(audio_path: str):
    """Benchmark transcription speed"""
    audio_service = AudioService()

    # Get audio duration
    duration = audio_service.get_audio_duration(audio_path)
    print(f"Audio duration: {duration:.1f}s")

    # Benchmark FFmpeg Whisper
    start = time.time()
    result = audio_service.transcribe_audio(audio_path)
    elapsed = time.time() - start

    print(f"\nFFmpeg Whisper Results:")
    print(f"  Transcription time: {elapsed:.1f}s")
    print(f"  Real-time factor: {elapsed/duration:.2f}x")
    print(f"  Text length: {len(result)} chars")
    print(f"  Text preview: {result[:100]}...")

    return elapsed, len(result)


if __name__ == '__main__':
    import sys

    if len(sys.argv) < 2:
        print("Usage: python benchmark_whisper.py <audio_file>")
        sys.exit(1)

    audio_file = sys.argv[1]

    if not os.path.exists(audio_file):
        print(f"Error: {audio_file} not found")
        sys.exit(1)

    benchmark_transcription(audio_file)
