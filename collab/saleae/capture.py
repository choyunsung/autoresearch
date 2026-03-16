"""
Saleae Logic Analyzer — Analog Signal Capture Module

Saleae Logic 2 앱과 연동하여 아날로그 신호를 캡처하고,
numpy 배열 또는 CSV/NPZ로 저장합니다.

Requirements:
  - Saleae Logic 2 앱 실행 중 (자동 실행 지원)
  - pip install saleae numpy
  - Saleae 디바이스 USB 연결

Usage:
  from collab.saleae.capture import SaleaeCapture

  cap = SaleaeCapture()
  cap.configure(analog_channels=[0,1,2,3], sample_rate=50000, duration=10.0)
  data = cap.capture()  # returns dict of {channel: numpy array}
  cap.save_npz("output.npz")
"""

import csv
import json
import time
import tempfile
import os
from pathlib import Path
from datetime import datetime, timezone
from dataclasses import dataclass, field, asdict
from typing import Optional

import numpy as np

try:
  import saleae
  SALEAE_AVAILABLE = True
except ImportError:
  SALEAE_AVAILABLE = False


@dataclass
class CaptureConfig:
  """Saleae 캡처 설정."""
  analog_channels: list[int] = field(default_factory=lambda: [0, 1, 2, 3])
  digital_channels: list[int] = field(default_factory=list)
  sample_rate: int = 50000        # Hz (analog sample rate)
  digital_sample_rate: int = 0    # Hz (0 = auto)
  duration: float = 10.0          # seconds
  voltage_option: str = ""        # e.g. "3.3" or "5.0"
  label: str = ""                 # User label for this capture
  description: str = ""


@dataclass
class CaptureResult:
  """캡처 결과."""
  config: CaptureConfig
  channels: dict[int, np.ndarray] = field(default_factory=dict)  # ch -> samples
  time_array: Optional[np.ndarray] = None  # shared time axis
  actual_sample_rate: float = 0.0
  actual_duration: float = 0.0
  captured_at: str = ""
  device_name: str = ""
  export_path: str = ""


class SaleaeCapture:
  """Saleae Logic Analyzer 아날로그 신호 캡처."""

  def __init__(self, host: str = "localhost", port: int = 10429):
    """
    Args:
      host: Logic 2 소켓 서버 주소
      port: Logic 2 소켓 포트 (기본 10429)
    """
    if not SALEAE_AVAILABLE:
      raise ImportError("saleae 패키지가 필요합니다: pip install saleae")
    self.host = host
    self.port = port
    self._conn: Optional[saleae.Saleae] = None
    self._config = CaptureConfig()
    self._result: Optional[CaptureResult] = None

  def connect(self) -> "SaleaeCapture":
    """Logic 2 앱에 연결."""
    self._conn = saleae.Saleae(host=self.host, port=self.port)
    return self

  def _ensure_connected(self):
    if self._conn is None:
      self.connect()

  def get_devices(self) -> list[dict]:
    """연결된 Saleae 디바이스 목록."""
    self._ensure_connected()
    devices = self._conn.get_connected_devices()
    return [{"index": i, "name": str(d)} for i, d in enumerate(devices)]

  def get_sample_rates(self) -> list[tuple]:
    """사용 가능한 샘플레이트 조합 (digital, analog)."""
    self._ensure_connected()
    return self._conn.get_all_sample_rates()

  def configure(
    self,
    analog_channels: list[int] = None,
    digital_channels: list[int] = None,
    sample_rate: int = None,
    duration: float = None,
    label: str = "",
    description: str = "",
  ) -> "SaleaeCapture":
    """캡처 파라미터 설정."""
    if analog_channels is not None:
      self._config.analog_channels = analog_channels
    if digital_channels is not None:
      self._config.digital_channels = digital_channels
    if sample_rate is not None:
      self._config.sample_rate = sample_rate
    if duration is not None:
      self._config.duration = duration
    if label:
      self._config.label = label
    if description:
      self._config.description = description
    return self

  def _apply_config(self):
    """Logic 2 앱에 설정 적용."""
    conn = self._conn

    # Set active channels
    conn.set_active_channels(
      digital=self._config.digital_channels or [],
      analog=self._config.analog_channels,
    )

    # Find best matching sample rate
    rates = conn.get_all_sample_rates()
    target = self._config.sample_rate
    best = min(rates, key=lambda r: abs(r[1] - target) if r[1] > 0 else float("inf"))
    conn.set_sample_rate(best)
    self._config.sample_rate = best[1]  # actual analog rate
    self._config.digital_sample_rate = best[0]

    # Set duration
    conn.set_capture_seconds(self._config.duration)

  def capture(self) -> CaptureResult:
    """
    아날로그 신호를 캡처하고 numpy 배열로 반환.

    Returns:
      CaptureResult with channel data as numpy arrays
    """
    self._ensure_connected()
    self._apply_config()

    # Get device info
    devices = self._conn.get_connected_devices()
    active = self._conn.get_active_device()
    device_name = str(devices[active]) if active < len(devices) else "Unknown"

    print(f"[Saleae] 캡처 시작: {len(self._config.analog_channels)}ch, "
          f"{self._config.sample_rate}Hz, {self._config.duration}s")

    t0 = time.time()
    self._conn.capture_start_and_wait_until_finished()
    elapsed = time.time() - t0

    print(f"[Saleae] 캡처 완료: {elapsed:.1f}s")

    # Export to CSV temp file
    tmpdir = tempfile.mkdtemp(prefix="saleae_")
    csv_path = os.path.join(tmpdir, "capture.csv")

    self._conn.export_data2(
      csv_path,
      analog_channels=self._config.analog_channels,
      format="csv",
      analog_format="voltage",
    )

    # Parse CSV into numpy arrays
    channels, time_arr = self._parse_csv(csv_path)

    self._result = CaptureResult(
      config=self._config,
      channels=channels,
      time_array=time_arr,
      actual_sample_rate=self._config.sample_rate,
      actual_duration=elapsed,
      captured_at=datetime.now(timezone.utc).isoformat(),
      device_name=device_name,
      export_path=csv_path,
    )

    return self._result

  def _parse_csv(self, csv_path: str) -> tuple[dict[int, np.ndarray], np.ndarray]:
    """Saleae CSV export를 numpy 배열로 파싱."""
    data = {}
    time_col = []

    with open(csv_path, "r") as f:
      reader = csv.reader(f)
      header = next(reader)

      # Initialize channel arrays
      ch_indices = {}
      for i, col in enumerate(header):
        col_lower = col.strip().lower()
        if "time" in col_lower:
          ch_indices["time"] = i
        elif "channel" in col_lower or col.strip().isdigit():
          # Extract channel number
          ch_num = int("".join(c for c in col if c.isdigit()) or str(i - 1))
          ch_indices[ch_num] = i
          data[ch_num] = []

      for row in reader:
        if not row:
          continue
        if "time" in ch_indices:
          try:
            time_col.append(float(row[ch_indices["time"]]))
          except (ValueError, IndexError):
            continue
        for ch_num, col_idx in ch_indices.items():
          if ch_num == "time":
            continue
          try:
            data[ch_num].append(float(row[col_idx]))
          except (ValueError, IndexError):
            data[ch_num].append(0.0)

    # Convert to numpy
    channels = {ch: np.array(vals, dtype=np.float64) for ch, vals in data.items()}
    time_arr = np.array(time_col, dtype=np.float64) if time_col else None

    return channels, time_arr

  def save_npz(self, path: str) -> str:
    """캡처 결과를 NPZ로 저장 (amcgx-test 호환 포맷)."""
    if not self._result:
      raise ValueError("캡처 데이터가 없습니다. capture()를 먼저 실행하세요.")

    save_data = {}

    # Channel data: (n_channels, n_samples) 형태
    ch_nums = sorted(self._result.channels.keys())
    if ch_nums:
      signal_matrix = np.stack([self._result.channels[ch] for ch in ch_nums])
      save_data["raw_data"] = signal_matrix
      save_data["channel_indices"] = np.array(ch_nums)

    if self._result.time_array is not None:
      save_data["time"] = self._result.time_array

    # Metadata
    meta = {
      "source": "saleae_logic_analyzer",
      "device": self._result.device_name,
      "sample_rate": self._result.actual_sample_rate,
      "duration": self._result.actual_duration,
      "captured_at": self._result.captured_at,
      "analog_channels": self._result.config.analog_channels,
      "label": self._result.config.label,
      "description": self._result.config.description,
    }
    save_data["capture_info"] = json.dumps(meta)

    np.savez(path, **save_data)
    print(f"[Saleae] 저장 완료: {path}")
    return path

  def save_csv(self, path: str) -> str:
    """캡처 결과를 CSV로 저장."""
    if not self._result:
      raise ValueError("캡처 데이터가 없습니다.")

    ch_nums = sorted(self._result.channels.keys())
    with open(path, "w", newline="") as f:
      writer = csv.writer(f)
      header = ["time"] + [f"ch{ch}" for ch in ch_nums]
      writer.writerow(header)

      n_samples = len(next(iter(self._result.channels.values())))
      time_arr = self._result.time_array
      for i in range(n_samples):
        row = [time_arr[i] if time_arr is not None else i / self._result.actual_sample_rate]
        row += [self._result.channels[ch][i] for ch in ch_nums]
        writer.writerow(row)

    print(f"[Saleae] CSV 저장: {path}")
    return path


class SaleaeSimulator:
  """
  Saleae 디바이스 없이 테스트용 시뮬레이터.
  사인파 + 노이즈로 가상 아날로그 신호를 생성합니다.
  """

  def __init__(self):
    self._config = CaptureConfig()
    self._result: Optional[CaptureResult] = None

  def configure(self, **kwargs) -> "SaleaeSimulator":
    for k, v in kwargs.items():
      if hasattr(self._config, k):
        setattr(self._config, k, v)
    return self

  def capture(self) -> CaptureResult:
    """시뮬레이션 신호 생성."""
    sr = self._config.sample_rate
    dur = self._config.duration
    n = int(sr * dur)
    t = np.linspace(0, dur, n, dtype=np.float64)

    channels = {}
    for i, ch in enumerate(self._config.analog_channels):
      freq = 1.0 + i * 0.5  # 1Hz, 1.5Hz, 2Hz, ...
      amplitude = 0.5 + i * 0.1
      signal = amplitude * np.sin(2 * np.pi * freq * t)
      noise = np.random.normal(0, 0.02, n)
      channels[ch] = signal + noise

    self._result = CaptureResult(
      config=self._config,
      channels=channels,
      time_array=t,
      actual_sample_rate=float(sr),
      actual_duration=dur,
      captured_at=datetime.now(timezone.utc).isoformat(),
      device_name="Simulator",
    )
    return self._result

  def save_npz(self, path: str) -> str:
    if not self._result:
      raise ValueError("capture() 먼저 실행하세요.")
    ch_nums = sorted(self._result.channels.keys())
    signal_matrix = np.stack([self._result.channels[ch] for ch in ch_nums])
    meta = {
      "source": "saleae_simulator",
      "sample_rate": self._result.actual_sample_rate,
      "duration": self._result.actual_duration,
      "captured_at": self._result.captured_at,
      "analog_channels": self._result.config.analog_channels,
      "label": self._result.config.label,
    }
    np.savez(path, raw_data=signal_matrix, time=self._result.time_array,
             channel_indices=np.array(ch_nums), capture_info=json.dumps(meta))
    return path

  def save_csv(self, path: str) -> str:
    if not self._result:
      raise ValueError("capture() 먼저 실행하세요.")
    ch_nums = sorted(self._result.channels.keys())
    with open(path, "w", newline="") as f:
      writer = csv.writer(f)
      writer.writerow(["time"] + [f"ch{ch}" for ch in ch_nums])
      for i in range(len(self._result.time_array)):
        writer.writerow(
          [self._result.time_array[i]] + [self._result.channels[ch][i] for ch in ch_nums]
        )
    return path
