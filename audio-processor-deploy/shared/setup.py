from setuptools import setup, find_packages

setup(
    name="telegram_bot_shared",
    version="0.1.0",
    packages=find_packages(),
    install_requires=[
        "google-cloud-firestore>=2.19.0",
        "google-cloud-pubsub>=2.26.0",
        "google-cloud-secret-manager>=2.21.1",
        "google-genai>=1.0.0",
        "python-json-logger>=2.0.7",
        "requests>=2.32.3",
        "pytz>=2025.1",
        "cachetools>=5.3.2"
    ],
)
