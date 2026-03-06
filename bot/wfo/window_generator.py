from __future__ import annotations

from dataclasses import dataclass
from typing import Any

import pandas as pd


@dataclass
class WalkForwardWindow:
    """
    One rolling walk-forward split with separate training and testing datasets.
    """

    window_id: str
    train_start: pd.Timestamp
    train_end: pd.Timestamp
    test_start: pd.Timestamp
    test_end: pd.Timestamp
    training_data: pd.DataFrame
    testing_data: pd.DataFrame

    def summary(self) -> dict[str, Any]:
        return {
            "window_id": self.window_id,
            "train_start": self.train_start.isoformat(),
            "train_end": self.train_end.isoformat(),
            "test_start": self.test_start.isoformat(),
            "test_end": self.test_end.isoformat(),
            "training_rows": int(len(self.training_data)),
            "testing_rows": int(len(self.testing_data)),
        }


@dataclass
class RollingWindowGenerator:
    """
    Split a sorted OHLCV DataFrame into rolling training and testing windows.
    """

    training_days: int = 90
    testing_days: int = 30
    step_days: int | None = None

    def generate(self, df: pd.DataFrame) -> list[WalkForwardWindow]:
        if df is None or df.empty:
            return []

        if "timestamp" not in df.columns:
            raise ValueError("Walk-forward generation requires a 'timestamp' column.")

        frame = df.copy()
        frame["timestamp"] = pd.to_datetime(frame["timestamp"], utc=True, errors="coerce")
        frame = frame.dropna(subset=["timestamp"]).sort_values("timestamp").reset_index(drop=True)
        if frame.empty:
            return []

        step_days = int(self.step_days or self.testing_days)
        training_delta = pd.Timedelta(days=int(self.training_days))
        testing_delta = pd.Timedelta(days=int(self.testing_days))
        step_delta = pd.Timedelta(days=step_days)

        start_time = frame["timestamp"].iloc[0]
        final_time = frame["timestamp"].iloc[-1]

        windows: list[WalkForwardWindow] = []
        cursor = start_time
        index = 1

        while True:
            train_cutoff = cursor + training_delta
            test_cutoff = train_cutoff + testing_delta
            if test_cutoff > final_time:
                break

            training_data = frame[(frame["timestamp"] >= cursor) & (frame["timestamp"] < train_cutoff)].copy()
            testing_data = frame[(frame["timestamp"] >= train_cutoff) & (frame["timestamp"] < test_cutoff)].copy()
            if not training_data.empty and not testing_data.empty:
                windows.append(
                    WalkForwardWindow(
                        window_id=f"window_{index}",
                        train_start=training_data["timestamp"].iloc[0],
                        train_end=training_data["timestamp"].iloc[-1],
                        test_start=testing_data["timestamp"].iloc[0],
                        test_end=testing_data["timestamp"].iloc[-1],
                        training_data=training_data.reset_index(drop=True),
                        testing_data=testing_data.reset_index(drop=True),
                    )
                )
                index += 1

            cursor = cursor + step_delta
            if cursor >= final_time:
                break

        return windows
