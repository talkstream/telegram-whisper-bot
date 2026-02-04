"""
Progress Service - Modern UX progress indicators for Telegram Bot
v2.0.0 - Best practices 2026

Features:
- Visual progress bar with emoji
- Estimated time remaining (ETA)
- Stage-based progress with sub-steps
- Rate-limited updates (Telegram API friendly)
- Graceful degradation messaging
"""

import time
import logging
from typing import Optional, Dict, Tuple
from dataclasses import dataclass, field
from enum import Enum


class ProcessingStage(Enum):
    """Processing stages with weights for progress calculation"""
    QUEUED = ("queued", 0, "В очереди")
    DOWNLOADING = ("downloading", 5, "Загрузка")
    CONVERTING = ("converting", 15, "Конвертация")
    TRANSCRIBING = ("transcribing", 60, "Распознавание")
    FORMATTING = ("formatting", 15, "Форматирование")
    SENDING = ("sending", 5, "Отправка")
    COMPLETED = ("completed", 100, "Готово")

    def __init__(self, stage_id: str, weight: int, display_name: str):
        self.stage_id = stage_id
        self.weight = weight
        self.display_name = display_name


# Progress bar configuration
PROGRESS_BAR_LENGTH = 10
PROGRESS_FILLED = "▓"
PROGRESS_EMPTY = "░"
PROGRESS_STAGES = [
    (0, "🔄"),    # Starting
    (20, "📥"),   # Downloading
    (35, "🔧"),   # Converting
    (50, "🎙"),   # Transcribing
    (80, "✨"),   # Formatting
    (95, "📤"),   # Sending
    (100, "✅"),  # Done
]


@dataclass
class ProgressState:
    """Tracks progress state for a single job"""
    job_id: str
    chat_id: int
    message_id: Optional[int] = None
    stage: ProcessingStage = ProcessingStage.QUEUED
    sub_progress: float = 0.0  # 0.0 - 1.0 within stage
    start_time: float = field(default_factory=time.time)
    stage_start_time: float = field(default_factory=time.time)
    audio_duration: int = 0  # seconds
    backend: str = "openai"  # or "gpu"
    last_update_time: float = 0
    last_message_text: str = ""


class ProgressService:
    """
    Modern progress indicator service for Telegram bots.

    Usage:
        progress = ProgressService(telegram_service)
        state = progress.create_state(job_id, chat_id, message_id, audio_duration)

        progress.update(state, ProcessingStage.DOWNLOADING)
        progress.update(state, ProcessingStage.TRANSCRIBING, sub_progress=0.5)
        progress.complete(state)
    """

    # Telegram rate limits: ~30 edits per minute per chat
    MIN_UPDATE_INTERVAL = 3.0  # seconds between updates
    FORCE_UPDATE_INTERVAL = 10.0  # force update after this time

    # ETA coefficients by backend (seconds per second of audio)
    ETA_COEFFICIENTS = {
        "openai": {
            "base": 8,  # base overhead (download, convert, format)
            "per_second": 0.3,  # 0.3 sec processing per 1 sec audio
        },
        "gpu": {
            "base": 15,  # higher due to potential cold start
            "per_second": 1.8,  # RTF ~1.5-2.0
        },
    }

    def __init__(self, telegram_service):
        self.telegram = telegram_service
        self._states: Dict[str, ProgressState] = {}

    def create_state(
        self,
        job_id: str,
        chat_id: int,
        message_id: Optional[int],
        audio_duration: int,
        backend: str = "openai"
    ) -> ProgressState:
        """Create a new progress state for tracking"""
        state = ProgressState(
            job_id=job_id,
            chat_id=chat_id,
            message_id=message_id,
            audio_duration=audio_duration,
            backend=backend
        )
        self._states[job_id] = state
        return state

    def get_state(self, job_id: str) -> Optional[ProgressState]:
        """Get existing progress state"""
        return self._states.get(job_id)

    def update(
        self,
        state: ProgressState,
        stage: ProcessingStage,
        sub_progress: float = 0.0,
        force: bool = False
    ) -> bool:
        """
        Update progress state and send message if needed.

        Args:
            state: Progress state to update
            stage: Current processing stage
            sub_progress: Progress within stage (0.0 - 1.0)
            force: Force update regardless of rate limit

        Returns:
            True if message was updated, False if skipped
        """
        now = time.time()

        # Update state
        if state.stage != stage:
            state.stage = stage
            state.stage_start_time = now
        state.sub_progress = sub_progress

        # Check rate limit
        time_since_update = now - state.last_update_time
        should_update = (
            force or
            time_since_update >= self.FORCE_UPDATE_INTERVAL or
            (time_since_update >= self.MIN_UPDATE_INTERVAL and
             self._progress_changed_significantly(state))
        )

        if not should_update:
            return False

        # Generate message
        message = self._format_progress_message(state)

        # Avoid sending identical messages
        if message == state.last_message_text:
            return False

        # Send update
        if state.message_id:
            try:
                self.telegram.edit_message_text(
                    state.chat_id,
                    state.message_id,
                    message
                )
                state.last_update_time = now
                state.last_message_text = message
                return True
            except Exception as e:
                logging.warning(f"Failed to update progress: {e}")
                return False

        return False

    def complete(self, state: ProgressState, success: bool = True) -> None:
        """Mark processing as complete"""
        state.stage = ProcessingStage.COMPLETED
        state.sub_progress = 1.0

        # Clean up state
        if state.job_id in self._states:
            del self._states[state.job_id]

    def _calculate_overall_progress(self, state: ProgressState) -> float:
        """Calculate overall progress percentage (0-100)"""
        # Calculate cumulative progress based on stage weights
        stages = list(ProcessingStage)
        current_idx = stages.index(state.stage)

        # Sum weights of completed stages
        completed_weight = sum(
            s.weight for s in stages[:current_idx]
        )

        # Add partial progress of current stage
        current_stage_weight = state.stage.weight * state.sub_progress

        return min(99, completed_weight + current_stage_weight)

    def _estimate_remaining_time(self, state: ProgressState) -> int:
        """Estimate remaining time in seconds"""
        coef = self.ETA_COEFFICIENTS.get(state.backend, self.ETA_COEFFICIENTS["openai"])

        # Total estimated time
        total_estimate = coef["base"] + (state.audio_duration * coef["per_second"])

        # Time elapsed
        elapsed = time.time() - state.start_time

        # Remaining
        remaining = max(0, total_estimate - elapsed)

        return int(remaining)

    def _format_progress_message(self, state: ProgressState) -> str:
        """Format progress message with visual elements"""
        progress = self._calculate_overall_progress(state)
        remaining = self._estimate_remaining_time(state)

        # Build progress bar
        filled = int(progress / 100 * PROGRESS_BAR_LENGTH)
        bar = PROGRESS_FILLED * filled + PROGRESS_EMPTY * (PROGRESS_BAR_LENGTH - filled)

        # Get stage emoji
        stage_emoji = "🔄"
        for threshold, emoji in PROGRESS_STAGES:
            if progress >= threshold:
                stage_emoji = emoji

        # Format ETA
        if remaining > 60:
            eta_str = f"~{remaining // 60} мин."
        elif remaining > 0:
            eta_str = f"~{remaining} сек."
        else:
            eta_str = "почти готово"

        # Build message
        lines = [
            f"{stage_emoji} {state.stage.display_name}...",
            "",
            f"[{bar}] {int(progress)}%",
            "",
            f"⏱ Осталось: {eta_str}"
        ]

        # Add backend indicator for transparency (optional)
        if state.backend == "gpu":
            lines.append("")
            lines.append("🖥 GPU-обработка")

        return "\n".join(lines)

    def _progress_changed_significantly(self, state: ProgressState) -> bool:
        """Check if progress changed enough to warrant an update"""
        # Always update on stage change
        if state.stage.display_name not in state.last_message_text:
            return True

        # Update if progress changed by 10%+
        current = self._calculate_overall_progress(state)
        # Extract previous progress from message (rough check)
        if f"{int(current)}%" not in state.last_message_text:
            return True

        return False


class GracefulDegradationMessages:
    """
    Messages for graceful degradation scenarios.
    Used when processing is interrupted or delayed.
    """

    @staticmethod
    def gpu_cold_start() -> str:
        """Message when GPU VM is starting up"""
        return (
            "🖥 Запускаю GPU-сервер...\n\n"
            "Это может занять до 1 минуты при первом запуске.\n"
            "Последующие запросы будут быстрее."
        )

    @staticmethod
    def preemption_recovery() -> str:
        """Message when Spot VM was preempted"""
        return (
            "⚠️ Обработка была прервана\n\n"
            "Ваш запрос автоматически перезапущен.\n"
            "Это может добавить 1-2 минуты к времени ожидания."
        )

    @staticmethod
    def queue_position(position: int, estimated_wait: int) -> str:
        """Message showing queue position"""
        if estimated_wait > 60:
            wait_str = f"~{estimated_wait // 60} мин."
        else:
            wait_str = f"~{estimated_wait} сек."

        return (
            f"📋 Ваш запрос в очереди\n\n"
            f"Позиция: {position}\n"
            f"Ожидание: {wait_str}"
        )

    @staticmethod
    def fallback_to_api() -> str:
        """Message when falling back from GPU to OpenAI API"""
        return (
            "🔄 Переключаюсь на быструю обработку...\n\n"
            "GPU-сервер занят, использую облачный API."
        )

    @staticmethod
    def long_audio_warning(duration_minutes: int) -> str:
        """Warning for long audio files"""
        estimated = int(duration_minutes * 1.5)  # rough estimate
        return (
            f"📢 Длинная запись ({duration_minutes} мин.)\n\n"
            f"Обработка займёт ~{estimated} мин.\n"
            "Вы получите результат, как только он будет готов."
        )
