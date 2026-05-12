
#!/usr/bin/env python3
"""
Priority VR Assistant - ENHANCED with Autopilot & Airfields Special Handlers
Supports: GET/SET/DELETE destination, GET airfield {name}
"""
import json
import difflib
import re
import string
import requests
from typing import Dict, Any, Optional, Tuple, List
import traceback
import time
from RuleEvaluator import RuleEvaluator
from math import radians, sin, cos, sqrt, atan2, degrees
from text_to_num import text2num



def parse_frequency(text: str, lang: str = "it", rules: dict = None) -> string:
    words = text.lower().split()
    decimal_markers = [m.lower() for m in rules["main_words"]]
    numberString = ""
    punctuationDone = 0
    for i, w in enumerate(words):
        if w in decimal_markers:
            if punctuationDone == 0:
                numberString=numberString+"."
            punctuationDone = punctuationDone + 1
            continue
        try:
            intValue = int(text2num(w, lang=lang))
            numberString=numberString+f"{intValue}"
        except Exception as e:
            continue
    if punctuationDone == 0 and len(numberString)>2:
        numberString = numberString[:3] + "."+numberString[3:]
    while len(numberString)<7:
        numberString = numberString + "0"
    return numberString


def gps_distance_bearing(lat1, lon1, lat2, lon2):
    """
    Calcola distanza in km e bearing (prua) da (lat1, lon1) a (lat2, lon2).
    
    Args:
        lat1, lon1: Coordinate A in gradi decimali.
        lat2, lon2: Coordinate B in gradi decimali.
    
    Returns:
        tuple: (distanza_km, bearing_gradi)
    """
    # Converti in radianti
    lat1, lon1, lat2, lon2 = map(radians, [lat1, lon1, lat2, lon2])
    
    # Distanza Haversine
    dlat = lat2 - lat1
    dlon = lon2 - lon1
    a = sin(dlat/2)**2 + cos(lat1) * cos(lat2) * sin(dlon/2)**2
    c = 2 * atan2(sqrt(a), sqrt(1 - a))
    distanza = 6371.0 * c  # Raggio Terra in km [web:8][web:12]
    
    # Bearing iniziale
    x = sin(dlon) * cos(lat2)
    y = cos(lat1) * sin(lat2) - sin(lat1) * cos(lat2) * cos(dlon)
    bearing = atan2(x, y)
    bearing = degrees(bearing)
    bearing = (bearing + 360) % 360  # Normalizza 0-360 [web:8][web:12]
    
    return distanza, bearing


CONFIG: Dict[str, Any] = {}
ctx = None

def detect_wake_word(
    text: str,
    wake_words: list[str],
    min_score: float = 0.80
) -> Tuple[float,Optional[str], Optional[str]]:
    """
    Detects fuzzy wake word match (std lib only).
    Returns (score 0-1, wake_word, remaining after match) or None.
    Matches whole approximate words/phrases with SequenceMatcher ratio.
    """
    if not text or not wake_words:
        return 0,None,None

    t_lower = text.lower()
    candidates = []  # (score, end_idx, ww)
    
    # Extract word spans (handles punctuation)
    words_spans = list(re.finditer(r"\b\w+\b", text))
    
    for ww in wake_words:
        ww_lower = ww.lower()
        # Single word matches
        for m in words_spans:
            candidate = text[m.start():m.end()]
            score = difflib.SequenceMatcher(None, ww_lower, candidate.lower()).ratio()
            if score >= min_score:
                candidates.append((score, m.end(), ww))
        
        # Multi-word phrases (sliding window up to 3 words)
        for i in range(len(words_spans)):
            for length in range(2, min(4, len(words_spans) - i + 1)):
                span_start = words_spans[i].start()
                span_end = words_spans[i + length - 1].end()
                candidate = text[span_start:span_end].strip()
                score = difflib.SequenceMatcher(None, ww_lower, candidate.lower()).ratio()
                if score >= min_score:
                    candidates.append((score, span_end, ww))

    if not candidates:
        return 0,None,None

    # Best score, earliest position tiebreaker
    candidates.sort(key=lambda x: (-x[0], x[1]))
    best_score, end_idx, best_word = candidates[0]
    
    remaining = text[end_idx:].strip()
    return best_score, best_word, remaining



class SessionContext:
    def __init__(self):
        self.Context: Dict[str, str] = {}
        bobby = {
            "alternatorout":2,"amps":10,"batteryvoltage":12,"cht1":300,"cht2":200,"cht3":0,"cht4":0,"egt1":0,"egt2":0,"egt3":0,"egt4":0,"enginerpm":0,"fuel":0,"fuel1":0,"fuel2":0,"fuelpressure":0,"fuelremaining":0,"manifoldpressure":0,"oilpressure":0,"oiltemperature":0,"outsidetemperature":0,
            "AHRSGLoad": 0.9589144596,
            "AHRSGLoadMax": 2.3107541336,
            "AHRSGLoadMin": 0.3732092102,
            "AHRSGyroHeading": 3276.7,
            "AHRSMagHeading": 148.1412659798,
            "AHRSPitch": 12.2648697825,
            "AHRSRoll": -2.6020340371,
            "AHRSSlipSkid": 5.8550493165,
            "AHRSTurnRate": 3276.7,
            "BaroGasResistance": 0.0,
            "BaroHumidity": 0.0,
            "BaroPpm": 0.0,
            "BaroPressureAltitude": 498.5318908691,
            "BaroTemperature": 28.75,
            "BaroVerticalSpeed": -4.9377374649,
            "GPSAltitudeMSL": 748.3596191406,
            "GPSFixQuality": 1,
            "GPSGroundSpeed": 0.0529999994,
            "GPSLatitude": 43.1047019958,
            "GPSLongitude": 12.2512989044,
            "GPSTime": "2026-02-21 16:33:10.58 +0000 UTC",
            "GPSTrueCourse": 328.6600036621,
            "GPSTurnRate": 0.0,
            "GPSVerticalSpeed": 0.0
        }
        self.Context["last_component"]=""
        self.Context["last_parameter"]=""
        self.Context["last_wwword"]=""
        

def normalize_text(text: str) -> str:
    text = text.lower()
    text = re.sub(r'[^\w\s]', ' ', text)
    return text.strip()

def remove_skip_words(text: str, skip_words: List[str]) -> str:
    words = text.split()
    filtered = [w for w in words if w not in skip_words]
    return ' '.join(filtered)

def similarity_ratio(a: str, b: str) -> float:
    score, is_match, extracted = improved_similarity(a,b,0.7)
    return score
    #return difflib.SequenceMatcher(None, a, b).ratio()

def improved_similarity2(a: str, b: str, threshold: float = 0.8) -> tuple[float, bool, str]:
    """
    Enhanced matching for verb-in-phrase with partial ratio and prefix boost.
    Returns (score, is_match, extracted).
    """
    # Normalize: lowercase, remove punctuation
    a_norm = re.sub(f'[{string.punctuation}]', '', a.lower())
    b_norm = re.sub(f'[{string.punctuation}]', '', b.lower())
    
    # difflib partial via longest match
    matcher = difflib.SequenceMatcher(None, a_norm, b_norm)
    longest = max(m.size for m in matcher.get_matching_blocks())
    partial_ratio = 2.0 * longest / len(a_norm) if a_norm else 0.0
    
    # Prefix boost (common for commands)
    prefix_ratio = 1.0 if b_norm.startswith(a_norm) else partial_ratio * 0.9
    
    score = max(partial_ratio, prefix_ratio)
    is_match = score >= threshold
    extracted = b_norm[matcher.find_longest_match(0, len(a_norm), 0, len(b_norm)).b : 
                       matcher.find_longest_match(0, len(a_norm), 0, len(b_norm)).b + longest]
    
    return score, is_match, extracted


def improved_similarity(a: str, b: str, threshold: float = 0.8) -> tuple[float, bool, str]:
    a_norm = re.sub(f'[{string.punctuation}]', '', a.lower())
    b_norm = re.sub(f'[{string.punctuation}]', '', b.lower())
    
    if not a_norm or not b_norm:
        return 0.0, False, ''
    
    # Bidirectional partial ratios
    matcher_ab = difflib.SequenceMatcher(None, a_norm, b_norm)
    longest_ab = max(m.size for m in matcher_ab.get_matching_blocks())
    partial_ab = min(1.0, 2.0 * longest_ab / len(a_norm))
    
    matcher_ba = difflib.SequenceMatcher(None, b_norm, a_norm)
    longest_ba = max(m.size for m in matcher_ba.get_matching_blocks())
    partial_ba = min(1.0, 2.0 * longest_ba / len(b_norm))
    
    partial_ratio = (partial_ab + partial_ba) / 2  # Symmetric average
    
    # Prefix boost if either starts with the other
    prefix_boost = 1.2 if (b_norm.startswith(a_norm) or a_norm.startswith(b_norm)) else 0.9
    prefix_ratio = partial_ratio * prefix_boost
    
    score = max(partial_ratio, prefix_ratio)
    is_match = score >= threshold
    
    # Extract from longer match
    if longest_ab >= longest_ba:
        lmatch = matcher_ab.find_longest_match(0, len(a_norm), 0, len(b_norm))
        extracted = b_norm[lmatch.b: lmatch.b + longest_ab]
    else:
        lmatch = matcher_ba.find_longest_match(0, len(b_norm), 0, len(a_norm))
        extracted = a_norm[lmatch.b: lmatch.b + longest_ba]
    
    return score, is_match, extracted


def find_fuzzy_word_match(text: str, phrase: str, threshold: float = 0.5):
    """Fuzzy match on whole words only."""
    words = re.findall(r'\b\w+\b', text)
    p_norm = normalize_text(phrase)
    best_ratio = 0
    best_word_idx = -1
    for i, word in enumerate(words):
        ratio = similarity_ratio(p_norm, word)
        if ratio > best_ratio and ratio >= threshold:
            best_ratio = ratio
            best_word_idx = i
    if best_word_idx >= 0:
        before_words = ' '.join(words[:best_word_idx])
        after_words = ' '.join(words[best_word_idx+1:])
        return f"{before_words} {after_words}".strip(), best_word_idx, best_ratio
    return text, -1, 0

def remove_matched_phrase(text: str, phrase: str, threshold: float = 0.5) -> str:
    t_norm = normalize_text(text)
    p_norm = normalize_text(phrase)
    result, idx, ratio = find_fuzzy_word_match(t_norm, p_norm, threshold)
    print(f"O {t_norm}:{p_norm}:{result}")
    print(f"Word match idx {idx} ratio {ratio:.3f}")
    return result


def extract_json_path(data: dict, path: str) -> any:
    """Extract dict or value from dotted json_path."""
    keys = path.split('.')
    current = data
    for key in keys:
        if isinstance(current, dict):
            if key == "":
                return current
            current = current.get(key)
            if current is None:
                return current
        else:
            return current
    return current  # Returns dict if path ends at object, value otherwise


def detect_incontext(text: str, context: Dict[str, str], skip_words: List[str]) -> Tuple[str, str]:
    text_clean = remove_skip_words(text, skip_words)
    if not text_clean:
        return None, text
    candidates = []
    for action,value in context.items():
            ratio = similarity_ratio(action, text_clean)
            print(f"Z {action},{ratio}")
            candidates.append((ratio, action))
    if not candidates:
        return None, text_clean
    best_score, best_action = max(candidates, key=lambda x: x[0])
    if best_score > 0.60:
        return best_action, remove_matched_phrase(text_clean, best_action)
    return None, text_clean


def detect_action(text: str, actions_cfg: Dict[str, List[str]], default_action: str, skip_words: List[str]) -> Tuple[str, str]:
    text_clean = remove_skip_words(text, skip_words)
    print(f"Y {text_clean}")
    if not text_clean:
        return default_action, text
    print(f"U {text_clean}")
    candidates = []
    for action, triggers in actions_cfg.items():
        for trig in triggers:
            ratio = similarity_ratio(trig, text_clean)
            print(f"P {text_clean},{trig},{ratio}")
            if ratio > 0.6:
                candidates.append((ratio, action, trig))
    if not candidates:
        return default_action, text_clean
    best_score, best_action, best_phrase = max(candidates, key=lambda x: x[0])
    if best_score > 0.6:
        print(f"Y {text_clean},{best_phrase},{best_score}")
        return best_action, remove_matched_phrase(text_clean, best_phrase)
    return default_action, text_clean

def detect_component(text: str, components_cfg: Dict[str, Any], skip_words: List[str]) -> Tuple[Optional[str], Optional[str], str]:
    text_clean = remove_skip_words(text, skip_words)
    if not text_clean:
        return None, None, text_clean
    best_comp_score = 0.0
    best_component = None
    best_parameter = None
    best_phrase = None
    for comp_name, comp_cfg in components_cfg.items():
        keywords = comp_cfg.get('keywords', {})
        for kw, kw_param in keywords.items():
            score = similarity_ratio(kw, text_clean)
            if score > best_comp_score:
                best_comp_score = score
                best_component = comp_name
                best_parameter = kw_param
                best_phrase = kw
    if best_comp_score > 0.60:  # Keyword match
        remaining = remove_matched_phrase(text_clean, best_phrase)
        return best_component, best_parameter, remaining
    return None, None, text_clean
def detect_parameter(text: str, component: Optional[str], components_cfg: Dict[str, Any], skip_words: List[str]) -> Tuple[Optional[str], str, float]:
    # IMPROVED ITERATIVE VERSION
    original_text = text
    iterations = 0
    max_iters = 3

    while iterations < max_iters:
        text_clean = remove_skip_words(text, skip_words)
        if not text_clean:
            break

        best_score = 0.0
        best_param = None
        best_phrase = None

        # Priority 1: Component endpoints (exact param names)
        #if component and component in components_cfg:
        #    for p in components_cfg[component]['endpoints'].keys():
        #        score = similarity_ratio(p, text_clean)
        #        print(f"D {p}:{text_clean}:{score}")
        #        if score > best_score and score > 0.70:
        #            best_score = score
        #            best_param = p
        #            best_phrase = p

        # Priority 2: Keywords -> param mapping
        if best_score < 0.60:
            if component and component in components_cfg:
                kw_map = components_cfg[component].get('keywords', {})
                for kw, param in kw_map.items():
                    score = similarity_ratio(kw, text_clean)
                    print(f"E {kw}:{text_clean}:{score:.1f} > {best_score:.1f} => {param}")
                    if score > best_score:
                        print(f"G {kw}:{text_clean}:{score:.1f} > {best_score:.1f} => {param}")
                        best_score = score
                        best_param = param
                        best_phrase = kw

        if best_param and best_score > 0.60:
            remaining = remove_matched_phrase(text_clean, best_phrase)
            print(f"R {best_param}:{remaining}:{best_score}")
            return best_param, remaining, best_score

        # Remove first word and retry
        words = text.split()
        if len(words) > 1:
            text = ' '.join(words[1:])
        else:
            break
        iterations += 1

    return None, normalize_text(original_text), 0

def handle_traffic(context: str, component: str, action: str, parameter: str, text: str, config: Dict,ctx: SessionContext) -> str:
    comp_cfg = config['components'][component]
    base_url = config['base_url'].rstrip('/')
    path = comp_cfg['endpoints'][component]['path']
    url = f"{base_url}/{path}"


    ctx.Context["TrafficCount"]=0
    ctx.Context["TrafficDistance"]=0
    ctx.Context["TrafficLocation"]=""


    resp = requests.get(url, timeout=60.0)
    resp.raise_for_status()
    data = resp.json()

    urlSituation = f"{base_url}/getSituation"
    mySituation = requests.get(urlSituation, timeout=60.0)
    mySituation.raise_for_status()
    mySituationData = mySituation.json()

    if(len(data)>0):
        ctx.Context["TrafficCount"]=len(data)
        mostNearbyDistance = 0
        mostNearbyTraffic = None
        for traffic in data:
            if traffic["Position_valid"] == True:
                dist, bearing = gps_distance_bearing(mySituationData["GPSLatitude"], mySituationData["GPSLongitude"], traffic["Lat"], traffic["Lng"])
                if mostNearbyDistance == 0 or dist < mostNearbyDistance:
                    mostNearbyTraffic = traffic


        if mostNearbyTraffic != None:
            dist, bearing = gps_distance_bearing(mySituationData["GPSLatitude"], mySituationData["GPSLongitude"], mostNearbyTraffic["Lat"], mostNearbyTraffic["Lng"])
            clockInt = (12+int((bearing-mySituationData["GPSTrueCourse"])/30))%12
            if mostNearbyTraffic["Alt"]>mySituationData["BaroPressureAltitude"]:
                ctx.Context["TrafficLocation"]=config["answers"]["trafficAbove"]
            else:
                ctx.Context["TrafficLocation"]=config["answers"]["trafficBelow"]

            ctx.Context["TrafficLocation"]=ctx.Context["TrafficLocation"]+" "+config["answers"]["trafficClock"]+f" {clockInt}"
            ctx.Context["TrafficDistance"]=int(dist)
            template = comp_cfg['endpoints'][component]['reply_template']
            print(f"U {template}::{ctx.Context}")
            return template.format(**ctx.Context)
    return config["answers"]["trafficNone"]

def handle_display(context: str, component: str, action: str, parameter: str, text: str, config: Dict,ctx: SessionContext) -> str:
    print(f"context {context}")
    print(f"component {component}")
    print(f"action {action}")
    print(f"parameter {parameter}")
    print(f"text {text}")
    comp_cfg = config['components'][component]
    base_url = config['base_url'].rstrip('/')
    path = comp_cfg['endpoints'][component]['path']
    url = f"{base_url}/{path}"
    resp = requests.put(url, json={
        "source": "voice",
        "target": ctx.Context["last_wwword"],
        "key": parameter,
        "status": 0,
        }, timeout=60.0)
    resp.raise_for_status()
    print(f"{resp.text}")
    template = comp_cfg['endpoints'][component]['reply_template']
    return template.format(**ctx.Context)

def findRadioLabelByFrequencyOffline(filename, frequency):
    with open(filename, 'r') as f:
        data = json.load(f)
        for v in data:
            if "freq" in v and v["freq"]==frequency:
                return v["name"]
    return "---"
    
def handle_radio(context: str, component: str, action: str, parameter: str, text: str, config: Dict,ctx: SessionContext) -> str:
    #imposta frequenza radio centodicotto decimali centocinquanta
    print(f"context {context}")
    print(f"component {component}")
    print(f"action {action}")
    print(f"parameter {parameter}")
    print(f"text {text}")
    comp_cfg = config['components'][component]
    base_url = config['base_url'].rstrip('/')
    path = comp_cfg['endpoints'][component]['path']
    template = comp_cfg['endpoints'][component]['reply_template']
    ctx.Context["FrequencyStandby"] = parse_frequency(text,config["language"],config["numbers"])

    urlRadioGet = f"{base_url}/radio"
    radioGet = requests.get(urlRadioGet, timeout=60.0)
    radioGet.raise_for_status()
    radioData = radioGet.json()
    if len(radioData):
        radioData[0]["FrequencyStandby"] = ctx.Context["FrequencyStandby"]
        radioData[0]["LabelStandby"] = findRadioLabelByFrequencyOffline("/boot/firmware/rb/db.airfields.json",ctx.Context["FrequencyStandby"])
        
        urlRadioPost = f"{base_url}/radio/0"
        urlRadioResult=requests.post(urlRadioPost, json=radioData[0], timeout=60.0)
        print(f"{urlRadioResult.text}")
    return template.format(**ctx.Context)

def handle_autopilot(context: str, component: str, action: str, parameter: str, text: str, config: Dict,ctx: SessionContext) -> str:
    comp_cfg = config['components']['autopilot']
    base_url = config['base_url'].rstrip('/')
    path = comp_cfg['path']
    url = f"{base_url}/{path}"

    if action == "GET" and parameter == "destination":
        try:
            resp = requests.get(url, timeout=60.0)
            resp.raise_for_status()
            data = resp.json()
            if data and isinstance(data, list) and len(data) > 0:
                latest = data[-1]
                cmt = latest.get('Cmt', 'unknown')
                return f"Current destination: {cmt}"
            return "No destinations set."
        except Exception as e:
            print(traceback.format_exc())
            return ""

    elif action == "SET" and parameter == "destination":
        try:
            resp = requests.post(url, json={"text": text}, timeout=60.0)
            resp.raise_for_status()
            return f"Destination set to: {text}"
        except Exception as e:
            print(traceback.format_exc())
            return ""

    elif action == "DELETE" and parameter == "destination":
        try:
            resp = requests.delete(url, timeout=60.0)
            resp.raise_for_status()
            return "Destination cancelled."
        except Exception as e:
            print(traceback.format_exc())
            return ""

    return ""

def handle_timers(context: str, component: str, action: str, parameter: str, text: str, config: Dict,ctx: SessionContext) -> str:
    comp_cfg = config['components']['timers']
    base_url = config['base_url'].rstrip('/')
    path = comp_cfg['endpoints']['timer']['path']
    url = f"{base_url}/{path}"

    #{"0":{"className":"keypadSelectedYes","name":"Timer 1","text":"20:00","timerId":0,"CountDown":1200,"Epoch":1773077828,"Status":true,"triggered":"","Fired":false}}
    #

    if action == "SET" or action == "DEL":
        try:
            resp = requests.get(url, timeout=60.0)
            resp.raise_for_status()
            data = resp.json()

            search_name = normalize_text(text)
            nextValueIs = True
            if action == "DEL":
                nextValueIs = False
            if similarity_ratio(search_name,config['answers']['delete'])>0.7 or similarity_ratio(search_name,config['answers']['cancel'])>0.7 or similarity_ratio(search_name,config['answers']['stop'])>0.7:
                nextValueIs = False

            for t in data:
                best_match = t
                break

            if best_match:
                best_match["Status"] = nextValueIs
                best_match["Epoch"] = int(time.time())
                payload = json.dumps({"0":best_match}).encode("utf-8")
                print(f"{payload}")
                requests.post(url,payload)
                return handle_timers(context, component,"GET", parameter, text, config,ctx)
                
            else:
                return ""
        except Exception as e:
            print(traceback.format_exc())
            return ""



    if action == "GET":
        try:
            resp = requests.get(url, timeout=60.0)
            resp.raise_for_status()
            data = resp.json()

            best_match = None
            best_ratio = 0.0
            search_name = normalize_text(text)

            for t in data:
                best_match = t
                best_ratio = 1
                break

            if best_match:
                ctx.Context["timer name"]="1"
                if best_match["Status"] == True:
                    ctx.Context["timer status"]=config['answers']['started']
                    elapsed = int(time.time() - best_match["Epoch"])
                    print(f"{time.time()} - {best_match["Epoch"]}")
                    remaining = int(best_match["CountDown"] - elapsed)
                    if(remaining>0):
                        ctx.Context["timer count"]=f"{int(remaining/60)} {config['answers']['minutes']} {config['answers']['and']} {int(remaining%60)} {config['answers']['seconds']}"
                    else:
                        ctx.Context["timer count"]=f"{int(elapsed/60)} {config['answers']['minutes']} {config['answers']['and']} {int(elapsed%60)} {config['answers']['seconds']}"

                else:
                    ctx.Context["timer count"]=f"{int(best_match["CountDown"]/60)} {config['answers']['minutes']} {config['answers']['and']} {int(best_match["CountDown"]%60)} {config['answers']['seconds']}"
                    ctx.Context["timer status"]=config['answers']['stopped']
                    
                ctx.Context["timer fired"]="no"
                print(f"{json.dumps(ctx.Context).encode("utf-8")}")
                template = comp_cfg['endpoints']['timer']['reply_template']
                print(f"U {template}::{ctx.Context}")
                return template.format(**ctx.Context)
            else:
                return ""
        except Exception as e:
            print(traceback.format_exc())
            return ""
    return ""


def handle_airfields(context: str, component: str, action: str, parameter: str, text: str, config: Dict,ctx: SessionContext) -> str:
    comp_cfg = config['components']['airfields']
    base_url = config['base_url'].rstrip('/')
    path = comp_cfg['endpoints']['find']['path']
    url = f"{base_url}/{path}"

    if action == "GET":
        try:
            resp = requests.get(url, timeout=60.0)
            resp.raise_for_status()
            data = resp.json()
            if not isinstance(data, list):
                return config['answers']['didnotfindairfield']

            best_match = None
            best_ratio = 0.0
            search_name = normalize_text(text)

            for airfield in data:
                airfield_name = normalize_text(airfield.get('name', ''))
                ratio = similarity_ratio(search_name, airfield_name)
                print(f"X {search_name},{airfield_name},{ratio}")
                if ratio > best_ratio:
                    best_ratio = ratio
                    best_match = airfield

            if best_match:
                for k,v in best_match.items():
                    ctx.Context[k]=v
                template = comp_cfg['endpoints']['find']['reply_template']


                urlSituation = f"{base_url}/getSituation"
                mySituation = requests.get(urlSituation, timeout=60.0)
                mySituation.raise_for_status()
                mySituationData = mySituation.json()
                dist, bearing = gps_distance_bearing(mySituationData["GPSLatitude"], mySituationData["GPSLongitude"], best_match["Lat"], best_match["Lon"])
                ctx.Context["POIDISTANCE"]=dist
                ctx.Context["POIBEARING"]=bearing

                print(f"{json.dumps(ctx.Context).encode("utf-8")}")
                return template.format(**ctx.Context)
            else:
                return config['answers']['didnotfindairfield']
        except Exception as e:
            return config['answers']['didnotfindairfield']

    return ""

class ValueHelper:
    def __init__(self, config):
        self.config = config

    def helper_EMS_EngineStatus(self,parameter,values_dict) -> any:
        if isinstance(values_dict, dict) and values_dict:
            evaluator = RuleEvaluator.from_file("/boot/firmware/rb/rbvoice-aircraft-rules.json")
            results = evaluator.evaluate_rules(values_dict)
            for r in results:
                print(f"Aircraft Rule '{r.rule_name}': {r.color.value} (value: {r.result_value:.2f})")
                values_dict[r.rule_name]=self.config['answers'][r.color.value]
                if r.probe_name != None and r.probe_name != "":
                    values_dict[r.rule_name+"PROBELABEL"]=self.config['answers'][r.probe_name]
                else:
                    values_dict[r.rule_name+"PROBELABEL"]=""
                values_dict[r.rule_name+"PROBE"]=r.probe_name
                values_dict[r.rule_name+"VALUE"]=r.result_value
            return values_dict
        else:
            return values_dict

    def helper_GPSTime(self,parameter,values_dict) -> any:
        if isinstance(values_dict, dict) and values_dict:
            return values_dict
        else:
            match = re.search(r'T(\d{2}):(\d{2}):', values_dict)
            if match:
                hour, minute = match.groups()
                return f"{hour}:{minute}"
            return values_dict


    def helper_AutopilotDestination(self,parameter,values_dict) -> any:
        if isinstance(values_dict, dict) and values_dict:
            values_dict["Cmt"]="Pippo"
            values_dict["MinutesToDestination"]=12
            values_dict["NMToDestination"]=24
            return values_dict
        else:
            return values_dict


def handle_utterance(raw_text: str, ctx: SessionContext, config: Dict[str, Any]) -> str:
  try:
    if raw_text == None or raw_text == "":
        return ""
    valueHelper = ValueHelper(config)
    skip_words = config.get('skip_words', [])
    text_without_skip = remove_skip_words(raw_text, skip_words)

    wwscore, wwword, text = detect_wake_word(text_without_skip,config['wake_words'])
    if wwscore < 0.1 or wwword == None or  text == None:
        print(f"607 {raw_text}:{text_without_skip} No wake up word")
        return ""


    print(f"U {ctx.Context}")

    #parameterFoundInContext,text = detect_incontext(text, ctx.Context, skip_words)
    #if parameterFoundInContext:
    #    return ctx.Context[parameterFoundInContext]

    action, text = detect_action(text, config['actions'], config['defaults']['action'], skip_words)
    print(f"A {text}:{action}")
    component = None
    parameter = None
    
    #component, early_param, text = detect_component(text, config['components'], skip_words)
    best_score = 0.0
    out_text = text

    for componentIn in config['components']:
        print(f"B {text}:{action}:{component}")
        newParameter, newText, score = detect_parameter(text, componentIn, config['components'], skip_words)
        print(f"C {text}:{action}:{componentIn}:{newParameter}:{score}>{best_score}")
        if newParameter and score > best_score:
            best_score = score
            parameter = newParameter
            component = componentIn
            out_text = newText

    text = out_text
    component = component or ctx.Context["last_component"]
    parameter = parameter or ctx.Context["last_parameter"]

    ctx.Context["last_component"] = component
    ctx.Context["last_parameter"] = parameter
    ctx.Context["last_wwword"] = wwword

    print(f"{component}::{parameter}")

    if not component or not parameter:
        return config['answers']['didnotget']

    # SPECIAL HANDLERS
    if component == "autopilot":
        return handle_autopilot(raw_text, component, action, parameter, text, config, ctx)
    elif component == "airfields":
        return handle_airfields(raw_text, component, action, parameter, text, config, ctx)
    elif component == "timers":
        return handle_timers(raw_text, component, action, parameter, text, config, ctx)
    elif component == "traffic":
        return handle_traffic(raw_text, component, action, parameter, text, config, ctx)
    elif component == "display":
        return handle_display(raw_text, component, action, parameter, text, config, ctx)
    elif component == "radio":
        return handle_radio(raw_text, component, action, parameter, text, config, ctx)

    # STANDARD HANDLER for ems, situation
    comp_cfg = config['components'][component]
    endpoint_cfg = comp_cfg['endpoints'].get(parameter)
    if not endpoint_cfg:
        return f"{parameter} not available for {component}."

    base_url = config['base_url'].rstrip('/').rstrip('/')
    url = base_url + endpoint_cfg['path']

    resp = requests.get(url, timeout=60.0)
    resp.raise_for_status()
    data = resp.json()
    values_dict = extract_json_path(data, endpoint_cfg['json_path'])
    if endpoint_cfg['method'] != "" and hasattr(valueHelper,endpoint_cfg['method']):
        method = getattr(valueHelper,endpoint_cfg['method'])
        values_dict = method(parameter,values_dict)
    print(f"Data: {data}")
    print(f"Key: {endpoint_cfg['json_path']}")
    print(f"{json.dumps(ctx.Context).encode("utf-8")}")
    print(f"Ritorno: {values_dict}")
    template = endpoint_cfg['reply_template']
    if isinstance(values_dict, dict) and values_dict:
        print(f"? {template}::{values_dict}")
        return template.format(**values_dict)  # Multiple: {cht1}, {cht2}
    else:
        # Fallback for single value
        key = endpoint_cfg['json_path'].split('.')[-1]
        print(f"! {template}::{key}")
        return template.format(**{key: values_dict})    
  except Exception as e:
        print(traceback.format_exc())
        return ""
  return ""




