#!/usr/bin/env python3
import argparse
import json
import os
import sys
import subprocess
import queue
import threading
import time
import requests
import math
from datetime import datetime
from vosk import Model, KaldiRecognizer, SetLogLevel
from assistant import handle_utterance,SessionContext,CONFIG,ctx
import traceback
import struct
from array import array

def read_arecord_output():
    """Thread to read arecord raw data and put in queue"""
    global arecordproc
    global stoprecording
    global dataqueue
    try:
        while not stoprecording.is_set():
            chunk = arecordproc.stdout.read(4096)
            if chunk:
                dataqueue.put(chunk)
            else:
                print("no chunk")
                break
    except:
        print(traceback.format_exc())  
        pass
    finally:
        print("Recording stopped")
import select


def create_wav_header(sample_rate, num_samples=0, channels=1, bits_per_sample=16):
    """
    Create WAV header for given sample rate.
    
    Args:
    - sample_rate: int, e.g., 22500 or 16000
    - num_samples: int=0, set >0 to finalize sizes for audio data
    - channels: int=1 (mono)
    - bits_per_sample: int=16
    
    Returns: bytes, ready to prepend to raw PCM data
    """
    block_align = channels * (bits_per_sample // 8)
    byte_rate = sample_rate * block_align
    
    # Template matching your bytes structure
    header = b'RIFF\x00\x00\x00\x00WAVEfmt \x10\x00\x00\x00\x01\x00'
    header += struct.pack('<H', channels)
    header += struct.pack('<I', sample_rate)
    header += struct.pack('<I', byte_rate)
    header += struct.pack('<H', block_align)
    header += struct.pack('<H', bits_per_sample)
    header += b'data\x00\x00\x00\x00'
    
    if num_samples > 0:
        data_size = num_samples * block_align
        riff_size = 36 + data_size  # 8 + 16fmt + 8data + data_size
        # Patch sizes (little-endian uint32 at pos 4 and 40)
        header = (header[:4] + struct.pack('<I', riff_size) + 
                  header[8:40] + struct.pack('<I', data_size))
    
    return header


def wave_save_file(wavfile, pcm_data, hz):
    """Save raw PCM to WAV: prepends correct header with sizes."""
    header = create_wav_header(hz, num_samples=len(pcm_data)//2)  # //2 for 16-bit samples
    with open(wavfile, 'wb') as f:
        f.write(header)
        f.write(pcm_data)
    print(f"Saved {wavfile} ({len(pcm_data)} bytes, {hz}Hz)")
    
    subprocess.Popen(
        ['bash', '-c', f"$PWD/bin/remotecopy.sh {wavfile}"],  # Example long-running command
        stdout=subprocess.DEVNULL,
        stderr=subprocess.STDOUT,
        preexec_fn=os.setsid,  # New session, detaches from parent
        close_fds=True
    )


def extractCommandsFromAirfieldsOnline(config, commands):
    comp_cfg = config['components']['airfields']
    base_url = config['base_url']
    path = comp_cfg['endpoints']['find']['path']
    url = f"{base_url}/{path}"
    resp = requests.get(url, timeout=60.0)
    resp.raise_for_status()
    data = resp.json()
    for v in data:
        for vv in v["name"].split(" "):
            commands.append(vv)
    return commands

def extractCommandsFromAirfieldsOffline(filename, commands):
    with open(filename, 'r') as f:
        data = json.load(f)
        for v in data:
            for vv in v["name"].split(" "):
                commands.append(vv)
        return commands



def extractCommandsFromConfig(CONFIG, commands):
    """
    Extracts all strings from specified paths in the config dict.
    Returns a set of unique strings.
    """
    
    # answers: values
    #if 'answers' in CONFIG:
    #    for v in CONFIG['answers'].values():
    #        if isinstance(v, str):
    #            commands.append(v)
    
    # wake_words: list elements
    if 'wake_words' in CONFIG:
        for item in CONFIG['wake_words']:
            if isinstance(item, str):
                commands.append(item)
    
    # skip_words: list elements
    if 'skip_words' in CONFIG:
        for item in CONFIG['skip_words']:
            if isinstance(item, str):
                commands.append(item)
    
    # actions.GET, .SET, .DEL: list elements
    if 'actions' in CONFIG:
        for act in ['GET', 'SET', 'DEL']:
            if act in CONFIG['actions']:
                for item in CONFIG['actions'][act]:
                    if isinstance(item, str):
                        commands.append(item)
    
    # components.*.keywords: all keys (user phrases)
    if 'components' in CONFIG:
        for comp_name, comp_data in CONFIG['components'].items():
            if isinstance(comp_data, dict) and 'keywords' in comp_data:
                kw = comp_data['keywords']
                if isinstance(kw, dict):
                    for k in kw.keys():
                        if isinstance(k, str):
                            commands.append(k)

    return commands


def rms16le(pcm_bytes: bytes) -> float:
  a = array('h')
  a.frombytes(pcm_bytes)
  n = len(a)
  if n == 0:
    return 0.0
  s = 0.0
  for v in a:
    s += float(v) * float(v)
  return math.sqrt(s / n)

arecordproc = None
stoprecording = None
dataqueue = None
piper_proc = None
piper_out_queue = None
piper_lock = None

import requests
import json


def _piper_stdout_reader():
    global piper_proc, piper_out_queue
    try:
        while piper_proc is not None:
            chunk = piper_proc.stdout.read(4096)
            if not chunk:
                break
            piper_out_queue.put(chunk)
    except Exception:
        print(traceback.format_exc())
    finally:
        print("Piper stdout reader exiting")


def speak(reply: str, timestamp: str, SAMPLE_RATE_PLAY: int, DEV: str, RECORDINGSPATH: str):
    global piper_proc, piper_out_queue, piper_lock

    if piper_proc is None or piper_proc.poll() is not None:
        raise RuntimeError("Piper process is not running")

    text = (reply or "").strip()
    if not text:
        return

    # Send text to the persistent piper process
    with piper_lock:
        piper_proc.stdin.write((text + "\n").encode('utf-8'))
        piper_proc.stdin.flush()

    # Play and capture the output audio for this utterance.
    full_audio_bytes = bytearray()
    #playproc = subprocess.Popen([
    #    'aplay', '-D', DEV, '-f', 'S16_LE', '-c', '1', '-r', f"{SAMPLE_RATE_PLAY}", '-t', 'raw'
    #], stdin=subprocess.PIPE)
    #playproc = subprocess.Popen([
    #    'pw-play',"--channels=1","--format=s16", f"--rate={SAMPLE_RATE_PLAY}","-a","-"
    #], stdin=subprocess.PIPE)
    playproc = subprocess.Popen([
        'pipe-play.sh',DEV,f"{SAMPLE_RATE_PLAY}"
    ], stdin=subprocess.PIPE)

    try:
        first_received = False
        silent_cycles = 0

        while True:
            try:
                chunk = piper_out_queue.get(timeout=0.25)
                if chunk:
                    first_received = True
                    silent_cycles = 0
                    playproc.stdin.write(chunk)
                    playproc.stdin.flush()
                    full_audio_bytes.extend(chunk)
            except queue.Empty:
                if first_received:
                    silent_cycles += 1
                    if silent_cycles >= 4:
                        break
                else:
                    # If no output from pipers in first second, keep waiting a little
                    if not piper_proc or (piper_proc.poll() is not None):
                        break

        try:
            playproc.stdin.close()
        except Exception:
            pass

    except Exception as e:
        print(f"Streaming error: {e}")
        try:
            playproc.stdin.close()
        except Exception:
            pass
    finally:
        playproc.wait(timeout=30)

    wavfile = f'{RECORDINGSPATH}/vrout_{timestamp}.wav'
    wave_save_file(wavfile, bytes(full_audio_bytes), SAMPLE_RATE_PLAY)


def startPiper(args):
    global piper_proc, piper_out_queue, piper_lock

    if not os.path.isfile(args.piper_model):
        raise RuntimeError(f"PIPER model not found: {args.piper_model}")
    if not os.path.isfile(args.piper_json):
        raise RuntimeError(f"PIPER config not found: {args.piper_json}")

    piper_cmd = [
        'piper', '--model', args.piper_model,
        '--config', args.piper_json,
        '--output_raw'
    ]
    print(f"Starting Piper process: {piper_cmd}")

    piper_proc = subprocess.Popen(
        piper_cmd,
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.DEVNULL,
        bufsize=0
    )
    piper_out_queue = queue.Queue()
    piper_lock = threading.Lock()

    reader = threading.Thread(target=_piper_stdout_reader, daemon=True)
    reader.start()

    return piper_proc
from typing import Dict
import queue
import threading
import requests
import traceback

voice_event_queue = queue.Queue(maxsize=100)
voice_event_worker_started = False

def _voice_event_worker():
    while True:
        item = voice_event_queue.get()
        if item is None:
            break

        event, input_text, output_text, state, config, ctx = item
        try:
            comp_cfg = config['components']['display']
            base_url = config['base_url'].rstrip('/')
            path = comp_cfg['endpoints']['display']['path']
            url = f"{base_url}/{path}"

            requests.put(
                url,
                json={
                    "source": "voice",
                    "target": ctx.Context["last_wwword"],
                    "key": event,
                    "in": input_text,
                    "out": output_text,
                    "status": state,
                },
                timeout=10.0
            )
        except Exception:
            print(traceback.format_exc())
        finally:
            voice_event_queue.task_done()

def start_voice_event_worker():
    global voice_event_worker_started
    if not voice_event_worker_started:
        t = threading.Thread(target=_voice_event_worker, daemon=True)
        t.start()
        voice_event_worker_started = True

def voiceEvent(event: str, input: str, output: str, state: int, config, ctx):
    try:
        voice_event_queue.put_nowait((event, input, output, state, config, ctx))
    except queue.Full:
        pass

def startVR():
    global arecordproc
    global stoprecording
    global dataqueue

    parser = argparse.ArgumentParser()
    parser.add_argument('--server',  required=True, help='Url server')
    parser.add_argument('--cases',  required=True, help='Language Cases')
    parser.add_argument('--rec', required=True, help='Write Wav recordings here')
    parser.add_argument('--dev', required=True, help='Bluetooth DEV MAC address')
    parser.add_argument('--out', required=True, help='Bluetooth DEV MAC address')
    parser.add_argument('--model',  required=True, help='Path to Vosk model')
    parser.add_argument('--piper-model', required=True, help='Path to Piper .onnx model')
    parser.add_argument('--piper-json', required=True, help='Path to Piper .onnx.json config')
    parser.add_argument('--threshold', type=int, default=150, help='RMS threshold for speech detection')
    args = parser.parse_args()

    DEV = args.dev
    DEVOUT = args.out
    SAMPLE_RATE_RECORDING = 16000
    # No real improvements
    #SAMPLE_RATE_RECORDING = 22500
    SAMPLE_RATE_PLAY = 22500
    RECORDINGSPATH=args.rec
    TRAILLEN = 50
    THRESHOLD = args.threshold
    
    dataqueue = queue.Queue()
    stoprecording = threading.Event()

    print("Piper starting...")
    piper_proc = startPiper(args)
    start_voice_event_worker()

    print("Vosk starting...")
    model = Model(args.model)
    print("Recognizer starting...")
    SetLogLevel(-1)
    

    history = []
    ctx = SessionContext()
    with open(args.cases, 'r') as f:
        CONFIG = json.load(f)

    CONFIG["base_url"] = args.server



    supportedGrammar = []
    supportedGrammar = extractCommandsFromConfig(CONFIG,supportedGrammar)
    print(f"{supportedGrammar}")
    #supportedGrammar = extractCommandsFromAirfields(CONFIG,supportedGrammar)
    supportedGrammar = extractCommandsFromAirfieldsOffline("/boot/firmware/rb/db.airfields.json",supportedGrammar)
    stringOfCommands = json.dumps(supportedGrammar)
    #rec.SetWords(True)
    rec = KaldiRecognizer(model, SAMPLE_RATE_RECORDING, stringOfCommands)
    #rec.SetGrammar(stringOfCommands)
    #rec.SetEndpointerDelays(2.0, 0.7, 5.0)
    startupTime = datetime.now().strftime('%Y%m%d%H%M%S')
    historyFile = f'{RECORDINGSPATH}/vr_{startupTime}.json'
    speak("Il sistema è pronto",startupTime,SAMPLE_RATE_PLAY, DEVOUT, RECORDINGSPATH)

    try:
        while True:
            rec.Reset()
            print("Listening... speak now")
            aparameters = [
                'pipe-rec.sh',DEV,f"{SAMPLE_RATE_RECORDING}","/boot/firmware/rb/noise.prof"
            ]
            arecordproc = subprocess.Popen(aparameters, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,bufsize=0, universal_newlines=False)
            stoprecording.clear()
            readerthread = threading.Thread(target=read_arecord_output, daemon=True)
            readerthread.start()
            print(f"{aparameters}")

            wavInput = b''
            lastSample = None

            waitingForSilence = 0
            

            while True:
                try:
                    data = dataqueue.get(timeout=1.0)
                    level = rms16le(data)
                    
                    if(level > THRESHOLD):
                        print(f"RMS={level:4.0f} SPEAKING: {waitingForSilence}")
                        if(waitingForSilence==0):
                            if(lastSample!=None):
                                wavInput += lastSample
                                rec.AcceptWaveform(lastSample)
                                lastSample = None
                            voiceEvent("SPEAKING","", "",1, CONFIG,ctx)
                        waitingForSilence = TRAILLEN
                        wavInput += data
                    else:
                        if(waitingForSilence>0):
                            print(f"RMS={level:4.0f} NO-SPEAK: {waitingForSilence}")
                            wavInput += data
                            waitingForSilence = waitingForSilence - 1
                        if(waitingForSilence == 1):
                            voiceEvent("SPEAKING","", "",0, CONFIG,ctx)
                            waitingForSilence = 0
    
                    lastSample = data

                    if waitingForSilence>0 and rec.AcceptWaveform(data):
                        result = json.loads(rec.Result())
                        text = result.get('text', '').strip()
                        if text:
                            print(f'Recognized: {text}')
                            rec.Reset()
                            stoprecording.set()
                            arecordproc.terminate()
                            arecordproc.wait(timeout=2)
                            readerthread.join(timeout=2)

                            timestamp = datetime.now().strftime('%Y%m%d%H%M%S')
                            wavfile = f'{RECORDINGSPATH}/vrin_{timestamp}.wav'
                            wave_save_file(wavfile,wavInput,SAMPLE_RATE_RECORDING)
                            wavInput = b''
                            waitingForSilence = 0
                            voiceEvent("SPEAKING","", "",0, CONFIG,ctx)

                            # Use Piper HTTP server for TTS (raw output)
                            history.append({"role": "user", "content": text,"timestamp":timestamp})
                            reply = handle_utterance(text, ctx, CONFIG)
                            #reply = ""
                            print(f'Reply: {reply}')
                            
                            #if(reply == False or reply == ""):
                            #    reply = ollama_generate(text,ctx.Context,history)
                            if(reply and reply != ""):
                                voiceEvent("VOICE",text,reply,0, CONFIG,ctx)
                                timestamp = datetime.now().strftime('%Y%m%d%H%M%S')
                                history.append({"role": "assistant", "content": reply,"timestamp":timestamp})                                
                                speak(reply,timestamp,SAMPLE_RATE_PLAY, DEVOUT, RECORDINGSPATH)
                            else:
                                voiceEvent("VOICE",text,"",0, CONFIG,ctx)
                            with open(historyFile, 'wb') as f:
                                f.write(json.dumps(history).encode("utf-8"))
                            break

                        else:
                            print(f'No text')
                            wavInput = b''
                            waitingForSilence = 0
                            voiceEvent("SPEAKING","", "",0, CONFIG,ctx)
                    #else:
                    #    print("Partial:", rec.PartialResult())
                except queue.Empty:
                    # Brief sleep/yield if empty
                    time.sleep(0.001)
                    if arecordproc.poll() is not None:
                        print("No speech detected, retrying...")
                        stoprecording.set()
                        arecordproc.terminate()
                        arecordproc.wait()
                        break
                except Exception as e:
                    print(traceback.format_exc())
                    stoprecording.set()
                    break
            #while not dataqueue.empty():
            #    dataqueue.get()
    except KeyboardInterrupt:
        print("\nInterrupted")
    except Exception as e:
        print(traceback.format_exc())        
    finally:
        stoprecording.set()
        if arecordproc:
            arecordproc.terminate()
            arecordproc.wait()
        # Stop Piper process
        if piper_proc is not None:
            piper_proc.terminate()
            piper_proc.wait()


def main():
    startVR()

if __name__ == "__main__":
    main()
