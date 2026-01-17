import redis
import hashlib
import json
import logging
import os

class CacheService:
    """Redis caching for transcriptions and metadata"""

    def __init__(self):
        redis_host = os.getenv('REDIS_HOST', 'localhost')
        redis_port = int(os.getenv('REDIS_PORT', 6379))

        self.client = redis.Redis(
            host=redis_host,
            port=redis_port,
            decode_responses=True,
            socket_connect_timeout=5
        )

        # Test connection
        try:
            self.client.ping()
            logging.info("Redis connection successful")
        except Exception as e:
            logging.warning(f"Redis unavailable: {e}")
            self.client = None

    def get_transcription(self, audio_hash: str) -> str | None:
        """Get cached transcription by audio hash"""
        if not self.client:
            return None

        try:
            key = f"transcription:{audio_hash}"
            return self.client.get(key)
        except Exception as e:
            logging.warning(f"Cache read failed: {e}")
            return None

    def set_transcription(self, audio_hash: str, text: str, ttl: int = 86400):
        """Cache transcription with TTL (default 24 hours)"""
        if not self.client:
            return

        try:
            key = f"transcription:{audio_hash}"
            self.client.setex(key, ttl, text)
        except Exception as e:
            logging.warning(f"Cache write failed: {e}")

    @staticmethod
    def compute_audio_hash(audio_path: str) -> str:
        """Compute SHA256 hash of audio file"""
        sha256 = hashlib.sha256()
        with open(audio_path, 'rb') as f:
            for chunk in iter(lambda: f.read(8192), b''):
                sha256.update(chunk)
        return sha256.hexdigest()
