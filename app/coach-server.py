#!/usr/bin/env python3
# ============================ CONFIG — edit this ============================
MODEL         = "large-v3"   # best quality. Faster English fallback: "medium.en" / "small.en"
LANGUAGE      = "en"
PORT          = 9091
HTML_FILE     = "gemma-coach.html"

# ── anti-hallucination gates (Whisper invents text on silence/noise) ──
SILENCE_RMS       = 0.008    # skip chunks quieter than this (raise if it drops your quiet speech)
NO_SPEECH_MAX     = 0.6      # drop a segment if P(no speech) exceeds this
LOGPROB_MIN       = -1.0     # drop a segment whose avg token logprob is below this (low confidence)
COMPRESSION_MAX   = 2.4      # drop a segment that's too repetitive (hallucination signature)
# ===========================================================================

import os, tempfile, threading, time
os.environ.setdefault("PYTORCH_ENABLE_MPS_FALLBACK", "1")  # CPU-fallback for ops MPS lacks

from pathlib import Path
from flask import Flask, request, jsonify, send_from_directory
import numpy as np
import torch, whisper

HERE = Path(__file__).resolve().parent

DEVICE   = "mps" if torch.backends.mps.is_available() else ("cuda" if torch.cuda.is_available() else "cpu")
USE_FP16 = (DEVICE == "cuda")

print("", flush=True)
print(f"  ◈ Gemma Coach — Whisper backend", flush=True)
print(f"  folder : {HERE}", flush=True)
print(f"  device : {DEVICE} | model: {MODEL} | lang: {LANGUAGE}", flush=True)
print(f"  loading model (first run downloads weights)...", flush=True)
_t0 = time.time()
model = whisper.load_model(MODEL, device=DEVICE)
print(f"  ✓ model ready in {time.time()-_t0:.1f}s", flush=True)

# MPS model is not safe for concurrent transcribe calls — serialize with a lock.
# (The frontend also uploads chunks serially; this is belt-and-suspenders.)
_lock = threading.Lock()

app = Flask(__name__, static_folder=str(HERE), static_url_path="")


@app.route("/")
def index():
    return send_from_directory(str(HERE), HTML_FILE)


@app.route("/health")
def health():
    return jsonify({"ok": True, "device": DEVICE, "model": MODEL})


@app.route("/transcribe", methods=["POST"])
def transcribe():
    if "audio" not in request.files:
        return jsonify({"error": "no audio field"}), 400
    blob = request.files["audio"]

    # Whisper reads via ffmpeg from a real file path; keep the original extension.
    suffix = os.path.splitext(blob.filename or "")[1] or ".webm"
    tmp = tempfile.NamedTemporaryFile(suffix=suffix, delete=False)
    try:
        blob.save(tmp.name)
        tmp.close()

        # 1) energy gate — silence/near-silence is where Whisper hallucinates
        try:
            audio = whisper.load_audio(tmp.name)   # float32 mono @ 16k, range ~[-1,1]
        except Exception as e:
            return jsonify({"error": f"decode failed: {e}"}), 500
        if audio.size == 0:
            return jsonify({"text": "", "skipped": "empty"})
        rms = float(np.sqrt(np.mean(audio ** 2)))
        if rms < SILENCE_RMS:
            return jsonify({"text": "", "skipped": "silence", "rms": round(rms, 5)})

        with _lock:
            result = model.transcribe(
                audio,
                language=LANGUAGE,
                fp16=USE_FP16,
                temperature=0.0,                    # greedy → less improvisation
                condition_on_previous_text=False,   # each chunk stands alone → less drift
                no_speech_threshold=NO_SPEECH_MAX,
                logprob_threshold=LOGPROB_MIN,
                compression_ratio_threshold=COMPRESSION_MAX,
            )

        # 2) confidence gate — keep only segments that look like real speech
        kept = []
        for seg in result.get("segments", []):
            if seg.get("no_speech_prob", 0.0) > NO_SPEECH_MAX:      continue
            if seg.get("avg_logprob", 0.0)    < LOGPROB_MIN:        continue
            if seg.get("compression_ratio", 0.0) > COMPRESSION_MAX: continue
            t = (seg.get("text") or "").strip()
            if t:
                kept.append(t)
        text = " ".join(kept).strip()
        return jsonify({"text": text, "rms": round(rms, 5)})
    except Exception as e:
        return jsonify({"error": str(e)}), 500
    finally:
        try:
            os.unlink(tmp.name)
        except OSError:
            pass


if __name__ == "__main__":
    print(f"  ✓ serving http://localhost:{PORT}/{HTML_FILE}", flush=True)
    print(f"    (POST audio blobs to /transcribe)\n", flush=True)
    # threaded=True so the browser can poll /health while a transcribe runs;
    # the lock guarantees only one transcription touches the model at a time.
    app.run(host="127.0.0.1", port=PORT, threaded=True, debug=False)
