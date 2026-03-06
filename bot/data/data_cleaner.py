from __future__ import annotations

from datetime import datetime, timezone

import numpy as np
import pandas as pd


def clean_dataframe(df: pd.DataFrame) -> pd.DataFrame:
    """
    Clean OHLCV candles:
    - timestamp normalization
    - numeric conversion
    - NaN/Inf removal
    - duplicate removal
    - unfinished last candle removal
    """
    required = ["timestamp", "open", "high", "low", "close", "volume"]
    if df is None or df.empty:
        return pd.DataFrame(columns=required)

    missing = [col for col in required if col not in df.columns]
    if missing:
        raise ValueError(f"Input DataFrame missing required columns: {missing}")

    cleaned = df[required].copy()
    cleaned["timestamp"] = pd.to_datetime(cleaned["timestamp"], utc=True, errors="coerce")

    numeric_cols = ["open", "high", "low", "close", "volume"]
    cleaned[numeric_cols] = cleaned[numeric_cols].apply(pd.to_numeric, errors="coerce")
    cleaned[numeric_cols] = cleaned[numeric_cols].replace([np.inf, -np.inf], np.nan)

    cleaned = cleaned.dropna(subset=["timestamp"] + numeric_cols)
    cleaned = cleaned.sort_values("timestamp")
    cleaned = cleaned.drop_duplicates(subset=["timestamp"], keep="last")

    # Drop unfinished last candle by inferring candle size from median timestamp delta.
    if len(cleaned) >= 2:
        step = cleaned["timestamp"].diff().median()
        if pd.notna(step) and step.total_seconds() > 0:
            last_open = cleaned["timestamp"].iloc[-1].to_pydatetime()
            candle_close = last_open + step.to_pytimedelta()
            if datetime.now(timezone.utc) < candle_close:
                cleaned = cleaned.iloc[:-1]

    return cleaned.reset_index(drop=True)
