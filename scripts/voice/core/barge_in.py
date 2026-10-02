# -*- coding: utf-8 -*-
"""Barge-in gate: sustained user speech during Aali's speech interrupts playback.

Pure logic (injectable probability stream) so it is testable without the VAD
model. Fires only on SUSTAINED speech (consecutive hot frames) and respects a
cooldown so playback echo never triggers it.
"""
from __future__ import annotations

import time


class BargeInGate:
    def __init__(
        self,
        threshold: float = 0.6,
        consecutive_frames: int = 3,
        cooldown_s: float = 0.8,
        clock=time.monotonic,
    ):
        self.threshold = float(threshold)
        self.consecutive_frames = int(consecutive_frames)
        self.cooldown_s = float(cooldown_s)
        self._clock = clock
        self._hot_run = 0
        self._last_fire = 0.0

    def feed(self, prob: float) -> bool:
        """Feed one VAD probability; True = barge-in NOW."""
        if self._clock() - self._last_fire < self.cooldown_s:
            self._hot_run = 0
            return False
        if prob >= self.threshold:
            self._hot_run += 1
            if self._hot_run >= self.consecutive_frames:
                self._last_fire = self._clock()
                self._hot_run = 0
                return True
        else:
            self._hot_run = 0
        return False

    def reset(self) -> None:
        self._hot_run = 0
        self._last_fire = 0.0
