"""Small local CosyVoice 2 HTTP sidecar used by qqbot.voice.

Run with the CosyVoice venv, not the project Python:
  H:\\deepseek-chat\\tools\\cosyvoice\\venv39\\Scripts\\python.exe scripts\\cosyvoice-server.py
"""

from __future__ import annotations

import argparse
import io
import sys
from pathlib import Path

import numpy as np
import soundfile as sf
import uvicorn
from fastapi import FastAPI
from fastapi.responses import JSONResponse, Response
from pydantic import BaseModel

ROOT = Path(__file__).resolve().parent.parent
COSY_ROOT = ROOT / "tools" / "cosyvoice" / "CosyVoice"
sys.path.insert(0, str(COSY_ROOT))
sys.path.insert(0, str(COSY_ROOT / "third_party" / "Matcha-TTS"))

from cosyvoice.cli.cosyvoice import AutoModel  # noqa: E402
from cosyvoice.utils.file_utils import load_wav  # noqa: E402


class TTSRequest(BaseModel):
    text: str
    ref_audio: str
    prompt_text: str = ""
    instruct: str = ""


app = FastAPI()
model = None


@app.get("/health")
def health():
    return {"ok": model is not None, "sample_rate": int(model.sample_rate) if model else 0}


@app.post("/tts")
def tts(req: TTSRequest):
    if model is None:
        return JSONResponse({"error": "model not loaded"}, status_code=503)
    text = str(req.text or "").strip()
    ref = Path(req.ref_audio)
    if not text or not ref.is_file():
        return JSONResponse({"error": "text/ref_audio missing"}, status_code=400)
    prompt = load_wav(str(ref), 16000)
    if req.instruct.strip():
        instruction = req.instruct.strip()
        if "<|endofprompt|>" not in instruction:
            instruction += "<|endofprompt|>"
        pieces = model.inference_instruct2(text, instruction, prompt)
    else:
        pieces = model.inference_zero_shot(text, str(req.prompt_text or ""), prompt)
    chunks = []
    for item in pieces:
        speech = item["tts_speech"]
        if hasattr(speech, "detach"):
            speech = speech.detach().cpu().numpy()
        chunks.append(np.asarray(speech).reshape(-1))
    if not chunks:
        return JSONResponse({"error": "empty audio"}, status_code=500)
    audio = np.concatenate(chunks).astype(np.float32)
    buffer = io.BytesIO()
    sf.write(buffer, audio, int(model.sample_rate), format="WAV", subtype="PCM_16")
    return Response(content=buffer.getvalue(), media_type="audio/wav")


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--model-dir",
        default=str(ROOT / "tools" / "cosyvoice" / "pretrained_models" / "CosyVoice2-0.5B"),
    )
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=50000)
    args = parser.parse_args()
    global model
    model = AutoModel(model_dir=args.model_dir)
    uvicorn.run(app, host=args.host, port=args.port)


if __name__ == "__main__":
    main()
