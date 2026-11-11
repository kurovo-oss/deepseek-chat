# -*- coding: utf-8 -*-
import shutil
import subprocess
import uuid
from pathlib import Path
from types import SimpleNamespace

from qqbot import asr
from qqbot.config import Settings

ROOT = Path(__file__).resolve().parents[1]


def _settings(base: Path, **kwargs):
    return Settings(
        allow_user_id=123,
        model_api_key="k",
        base_dir=base,
        db_path=base / "data" / "chat.db",
        system_prompt_file=base / "prompts" / "system.md",
        **kwargs,
    )


def test_temp_dir_never_uses_c_drive():
    base = ROOT / "build" / ("asr-test-" + uuid.uuid4().hex)
    base.mkdir(parents=True)
    try:
        settings = _settings(base, asr_tmp_dir=Path("C:/asr-temp"))
        path = asr.temp_dir(settings)
        assert path.drive.upper() != "C:"
        assert path.is_dir()
    finally:
        shutil.rmtree(base, ignore_errors=True)


def test_parse_text_handles_json_and_timestamps():
    assert asr._parse_text('{"text": "你好世界"}') == "你好世界"
    assert asr._parse_text("0.0 -- 1.5: 第一句\n1.5 -- 3.0: 第二句") == "第一句 第二句"


def test_available_reports_missing_files(tmp_path):
    settings = _settings(tmp_path, asr_enabled=True)
    enabled, reason = asr.available(settings)
    assert enabled is False
    assert "模型" in reason or "sherpa" in reason


def test_transcribe_video_runs_wrapper_without_real_model(monkeypatch):
    base = ROOT / "build" / ("asr-test-" + uuid.uuid4().hex)
    base.mkdir(parents=True)
    try:
        fake_bin = base / "tools" / "asr" / "bin" / "sherpa-onnx-offline.exe"
        fake_bin.parent.mkdir(parents=True)
        fake_bin.write_bytes(b"fake")
        settings = _settings(base, asr_enabled=True, asr_bin=fake_bin)
        calls = []

        def fake_run(command, _settings, timeout):
            calls.append((command, timeout))
            return subprocess.CompletedProcess(command, 0, '{"text": "视频里的口播"}', "")

        monkeypatch.setattr(asr, "available", lambda _settings: (True, ""))
        monkeypatch.setattr(asr, "extract_wav", lambda *args, **kwargs: True)
        monkeypatch.setattr(asr, "_run", fake_run)
        # 需要让 transcribe 里的临时目录可写；模型路径不必真的存在，因为 available 被替换
        text = asr.transcribe_video(base / "input.mp4", settings)

        assert text == "视频里的口播"
        assert len(calls) == 1
        assert calls[0][0][0].endswith("sherpa-onnx-offline.exe")
        assert calls[0][0][-1].endswith("audio.wav")
    finally:
        shutil.rmtree(base, ignore_errors=True)
