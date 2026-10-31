#!/usr/bin/env python3
"""
Voice test: what Vosk writes for each phrase, and where the NLU sends it.

  # Phrase list as text, no audio: lines "phrase | component | parameter [| value]", # starts a comment.
  # The value is the radio frequency ("118.500") or the spelled airfield code ("LIRZ") the handler gets.
  python3 voicetest.py <cases.json> --phrases rbvoice-test.it.txt
  # The same phrases spoken by Piper, then heard by Vosk
  python3 voicetest.py <cases.json> --phrases rbvoice-test.it.txt --model <vosk model> --piper <voice.onnx> [--piper ...]
  # Recordings, such as the vrin_*.wav files that --rec saves
  python3 voicetest.py <cases.json> --model <vosk model> --wav /opt/stratux/www/playback/vrin_*.wav

Vosk gets the grammar rbvoice.py builds and, as in rbvoice.py, only its first final result is the
utterance: a pause long enough to split a phrase loses the rest, and the test says so.
The NLU runs against a closed port, so nothing reaches an RB-01: only the routing is read back.
Piper speech is clean and Italian-accented: a pass is not proof for the cockpit, a failure is a finding.
Exits 1 when a phrase routes elsewhere than expected.
"""
import argparse
import contextlib
import io
import json
import os
import re
import shutil
import subprocess
import sys
import wave

import assistant

# Nothing listens there: handlers fail before any RB-01 call, after the routing is stored
CLOSED_PORT_URL = "http://127.0.0.1:9"
# Bytes per AcceptWaveform, as pipe-rec.sh chunks reach rbvoice.py
CHUNK = 4096
# Silence after a Piper phrase, so the Vosk endpointer closes the utterance
TRAILING_SILENCE = 1.0


def transcribe(model, grammar, pcm, rate):
    """Every utterance Vosk finds in 16-bit mono PCM, in order; rbvoice.py acts on the first."""
    from vosk import KaldiRecognizer
    rec = KaldiRecognizer(model, rate, grammar)
    texts = []
    for i in range(0, len(pcm), CHUNK):
        if rec.AcceptWaveform(pcm[i:i + CHUNK]):
            texts.append(json.loads(rec.Result()).get("text", "").strip())
    texts.append(json.loads(rec.FinalResult()).get("text", "").strip())
    return [t for t in texts if t]


def piper_command():
    # rbvoice.sh puts the venv on PATH; a venv python run directly finds piper next to itself
    return shutil.which("piper") or os.path.join(os.path.dirname(sys.executable), "piper")


def piper_speak(voice, text):
    """16-bit mono PCM and its rate, from the piper command rbvoice.startPiper() runs."""
    config = voice + ".json"
    with open(config) as f:
        rate = json.load(f)["audio"]["sample_rate"]
    pcm = subprocess.run([piper_command(), "--model", voice, "--config", config, "--output_raw"],
                         input=text.encode("utf-8"), stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
                         check=True).stdout
    return pcm + bytes(2 * int(rate * TRAILING_SILENCE)), rate


def read_wav(path):
    with wave.open(path, "rb") as w:
        if w.getsampwidth() != 2 or w.getnchannels() != 1:
            raise ValueError(f"{path}: not 16-bit mono")
        return w.readframes(w.getnframes()), w.getframerate()


def route(config, text):
    ctx = assistant.SessionContext()
    with contextlib.redirect_stdout(io.StringIO()):
        assistant.handle_utterance(text, ctx, config)
    return ctx.Context["last_component"], ctx.Context["last_parameter"]


def words(text):
    return re.sub(r"\s+", " ", text.lower()).strip().split()


def value_of(config, component, rewritten):
    """What the handler acts on: the radio frequency, the spelled airfield code; "" otherwise."""
    if component == "radio":
        # handle_radio parses the text left after skip words, as here; a station name gives no frequency
        text = assistant.remove_skip_words(rewritten, config.get("skip_words", []))
        frequency = assistant.parse_frequency(text, config["language"], config["numbers"])
        return frequency if assistant.is_com_frequency(frequency) else ""
    if component in ("autopilot", "airfields"):
        return assistant.spelled_code(rewritten, config["language"])
    return ""


def report(config, texts, expected=None, spoken=None, expected_value=""):
    """Print one test; True unless it routes, or reads a value, other than expected."""
    heard = texts[0] if texts else ""
    component, parameter = route(config, heard) if heard else ("", "")
    rewritten = assistant.apply_aliases(heard, config.get("aliases", {}))
    value = value_of(config, component, rewritten)
    ok = expected is None or ((component, parameter) == expected and (not expected_value or value == expected_value))
    status = "    " if expected is None else ("ok  " if ok else "MISS")
    wanted = "" if ok else f"   expected {expected[0]}/{expected[1]}" + (f" {expected_value}" if expected_value else "")
    print(f"{status} {spoken or heard!r} -> {component}/{parameter}" + (f" {value}" if value else "") + wanted)
    if spoken is not None and words(heard) != words(spoken):
        print(f"       heard:     {heard!r}")
    if rewritten != heard.lower():
        print(f"       aliases:   {rewritten!r}")
    if len(texts) > 1:
        print(f"       split:     the device drops {texts[1:]!r}")
    return ok


def read_phrases(path):
    phrases = []
    with open(path, encoding="utf-8") as f:
        for line in f:
            line = line.split("#", 1)[0].strip()
            if line:
                spoken, component, parameter, *value = [x.strip() for x in line.split("|")]
                phrases.append((spoken, (component, parameter), value[0] if value else ""))
    return phrases


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("cases", help="case file")
    parser.add_argument("--phrases", help="phrase list; routed as text, or spoken by each --piper voice")
    parser.add_argument("--model", help="Vosk model directory, for --piper and --wav")
    parser.add_argument("--piper", action="append", default=[], help="Piper .onnx voice (its .onnx.json next to it)")
    parser.add_argument("--wav", nargs="+", default=[], help="16-bit mono recordings")
    parser.add_argument("--rb-dir", default="/boot/firmware/rb", help="where db.airfields.json and db.frequencies.json are")
    args = parser.parse_args()
    if not (args.phrases or args.wav) or (args.piper and not args.phrases):
        parser.error("give --phrases, optionally with --piper voices, or --wav")
    if (args.piper or args.wav) and not args.model:
        parser.error("--piper and --wav need --model")

    with open(args.cases, encoding="utf-8") as f:
        config = json.load(f)
    config["base_url"] = CLOSED_PORT_URL
    if args.model:
        # Text mode needs neither Vosk nor rbvoice.py (which imports it)
        from vosk import Model, SetLogLevel
        from rbvoice import buildGrammar
        SetLogLevel(-1)
        model = Model(args.model)
        # rbvoice.startVR() grammar; a model with HCLG.fst ignores it
        grammar = buildGrammar(config, args.rb_dir)

    failures = 0
    for path in args.wav:
        print(f"== {path}")
        pcm, rate = read_wav(path)
        report(config, transcribe(model, grammar, pcm, rate))

    if args.phrases:
        phrases = read_phrases(args.phrases)
        for voice in args.piper or [None]:
            name = os.path.basename(voice) if voice else "text"
            print(f"== {name}")
            passed = 0
            for spoken, expected, expected_value in phrases:
                if voice:
                    pcm, rate = piper_speak(voice, spoken)
                    texts = transcribe(model, grammar, pcm, rate)
                else:
                    texts = [spoken]
                passed += report(config, texts, expected, spoken, expected_value)
            failures += len(phrases) - passed
            print(f"{passed}/{len(phrases)} phrases route as expected ({name})\n")
    sys.exit(1 if failures else 0)


if __name__ == "__main__":
    main()
