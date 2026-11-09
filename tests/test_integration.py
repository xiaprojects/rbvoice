"""
Integration tests: utterances through handle_utterance(), against the fake RB-01 of conftest.py.

The phrase lists (rbvoice-test.<lang>.txt) must route as expected and get a spoken answer, the
handlers must write to the RB-01 what its HMI writes, and in every language every endpoint must
render its reply: a template that raises leaves the pilot with silence.
"""
import re
import time

import pytest

import assistant
import voicetest
from conftest import FIXTURE, LANGUAGES, ROOT, config_for, load_cases

PHRASES = [(path.name.split(".")[1], *phrase)
           for path in sorted(ROOT.glob("rbvoice-test.*.txt")) for phrase in voicetest.read_phrases(path)]
# Answers that mean the request failed
ERROR_ANSWERS = ("didnotget", "autopilotError", "didnotfindairfield", "radioNotFound")
AIRFIELDS = FIXTURE["/settings/db.airfields.json"]


def say(rb, text, lang="it", ctx=None):
    config = config_for(lang, rb)
    ctx = ctx or assistant.SessionContext()
    return assistant.handle_utterance(text, ctx, config), config, ctx


def airfield(code):
    [match] = [a for a in AIRFIELDS if code in assistant.airfield_codes(a)]
    return match


def order(rb, *calls):
    """The positions of the first request of each (method, path)."""
    made = [(m, p) for m, p, _ in rb.requests]
    return [made.index(call) for call in calls]


@pytest.mark.parametrize("lang, spoken, expected, value", PHRASES, ids=[f"{p[0]}: {p[1]}" for p in PHRASES])
def test_phrase(rb, lang, spoken, expected, value):
    reply, config, ctx = say(rb, spoken, lang)
    assert (ctx.Context["last_component"], ctx.Context["last_parameter"]) == expected
    if value:
        rewritten = assistant.apply_aliases(spoken, config.get("aliases", {}))
        assert voicetest.value_of(config, expected[0], rewritten) == value
    assert reply, "no answer: the handler raised"
    assert reply not in [config["answers"][a] for a in ERROR_ANSWERS]


@pytest.mark.parametrize("heard", ["copiate il secondo", "tipo di aeromobile", "acceso il computer"])
def test_radio_traffic_is_ignored(rb, heard):
    """The RB hears the intercom: radio calls near a wake word get no answer and no request."""
    assert say(rb, heard)[0] == ""
    assert rb.requests == []


def test_radio_frequency(rb):
    reply, _, _ = say(rb, "computer imposta frequenza radio centodiciannove punto cinquecentocinquantacinque")
    [radio] = rb.calls("POST", "/radio/0")
    # The standby frequency, labelled with the first known station on it; the active one is kept
    assert (radio["FrequencyStandby"], radio["LabelStandby"]) == ("119.555", "Foligno Airfield")
    assert radio["FrequencyActive"] == FIXTURE["/radio"][0]["FrequencyActive"]
    assert "119.555" in reply


def test_radio_station_name(rb):
    say(rb, "computer imposta frequenza radio perugia approach")
    [radio] = rb.calls("POST", "/radio/0")
    assert (radio["FrequencyStandby"], radio["LabelStandby"]) == ("125.600", "Perugia Approach")


def test_radio_unknown_station(rb):
    reply, config, _ = say(rb, "computer imposta frequenza radio zanzibar")
    assert reply == config["answers"]["radioNotFound"]
    assert rb.calls("POST", "/radio/0") == []


@pytest.mark.parametrize("spoken, code", [
    ("lima india romeo zulu", "LIRZ"),
    ("papa golf zero due", "PG02"),
    ("india tango zero zero zero tre", "IT0003"),
    ("perugia", "LIRZ"),
])
def test_autopilot_direct_to(rb, spoken, code):
    """The plan of the HMI's directToAirfield(): here (passed), then the airfield (target)."""
    reply, config, _ = say(rb, f"computer imposta destinazione {spoken}")
    expected = airfield(code)
    [(here, target)] = rb.calls("POST", "/autopilot")
    situation = FIXTURE["/getSituation"]
    assert (here["Lat"], here["Lon"], here["Status"]) == (situation["GPSLatitude"], situation["GPSLongitude"], 2)
    assert (target["Lat"], target["Lon"], target["Status"]) == (expected["Lat"], expected["Lon"], 1)
    assert target["Cmt"] == ", ".join(expected[k] for k in ("name", "local_code", "gps_code") if expected.get(k))
    # Waypoint.Ele is an int32: a float makes the server drop the whole plan
    assert type(here["Ele"]) is int and type(target["Ele"]) is int
    post, put = order(rb, ("POST", "/autopilot"), ("PUT", "/autopilot"))
    assert post < put
    assert reply.startswith(config["components"]["autopilot"]["endpoints"]["destination"]["reply_template_set"].split("{")[0])


@pytest.mark.parametrize("spoken", [
    "imposta destinazione lima alfa foxtrot",   # a misheard LIAF: a code that matches nothing
    "imposta destinazione campo",               # a generic word matching many airfields
])
def test_autopilot_keeps_plan_without_clear_match(rb, spoken):
    reply, config, _ = say(rb, f"computer {spoken}")
    assert reply == config["answers"]["didnotfindairfield"]
    assert rb.calls("POST", "/autopilot") == rb.calls("PUT", "/autopilot") == []


def test_radio_never_engages_autopilot(rb):
    """The word "radio" is also an airfields keyword: direct to needs an autopilot keyword."""
    say(rb, "computer imposta radio foligno")
    assert rb.calls("POST", "/autopilot") == rb.calls("PUT", "/autopilot") == []


@pytest.mark.parametrize("spoken", [
    "avvia autopilota",
    pytest.param("avvia pilota automatico", marks=pytest.mark.xfail(strict=True, reason=(
        "known defect: only one keyword word is removed, so \"automatico\" is taken as an airfield name"))),
])
def test_autopilot_engage_loaded_plan(rb, spoken):
    """SET with no airfield name engages the plan already loaded."""
    reply, config, _ = say(rb, f"computer {spoken}")
    assert rb.calls("POST", "/autopilot") == []
    assert len(rb.calls("PUT", "/autopilot")) == 1
    assert reply not in [config["answers"][a] for a in ERROR_ANSWERS]


def test_autopilot_stop(rb):
    reply, config, _ = say(rb, "computer stop pilota automatico")
    assert len(rb.calls("DELETE", "/autopilot")) == 1
    assert reply == config["components"]["autopilot"]["endpoints"]["destination"]["reply_template_stop"]


@pytest.mark.parametrize("spoken, running", [("avvia il cronometro", True), ("ferma il cronometro", False)])
def test_timer(rb, spoken, running):
    """As the HMI's timerStart(): the first timer, Epoch now, Fired reset so that it can fire."""
    before = int(time.time())
    reply, config, _ = say(rb, f"computer {spoken}")
    [body] = rb.calls("POST", "/timers")
    assert list(body) == ["0"]
    assert (body["0"]["Status"], body["0"]["Fired"]) == (running, False)
    assert before <= body["0"]["Epoch"] <= time.time()
    assert config["answers"]["started" if running else "stopped"] in reply


def test_display_page(rb):
    say(rb, "computer visualizza la bussola")
    assert rb.calls("PUT", "/remote/voice") == [{"source": "voice", "target": "computer", "key": "heading", "status": 0}]


def follow_up(cases):
    """A wake word and a question with no keyword: the reply reuses the last component and parameter."""
    wake = next(w for w in cases["wake_words"] if " " not in w)
    # One plain word: the matcher removes one word, and "what's" would leave "s" behind
    question = next(t for t in cases["actions"]["GET"] if re.fullmatch(r"\w+", t) and t not in cases["skip_words"])
    return f"{wake} {question}"


def endpoints_to_answer(lang):
    cases = load_cases(lang)
    for name in ("ems", "situation"):
        for parameter in cases["components"][name]["endpoints"]:
            yield name, parameter
    yield from [("traffic", "traffic"), ("timers", "timer"), ("display", "heading"), ("autopilot", "destination")]


FOLLOW_UPS = [(lang, name, parameter) for lang in LANGUAGES for name, parameter in endpoints_to_answer(lang)]


@pytest.mark.parametrize("lang, component, parameter", FOLLOW_UPS, ids=[" ".join(f) for f in FOLLOW_UPS])
@pytest.mark.parametrize("ground_speed", [0.2, 90], ids=["parked", "flying"])
def test_every_endpoint_answers(rb, lang, component, parameter, ground_speed):
    rb.data["/getSituation"]["GPSGroundSpeed"] = ground_speed
    cases = load_cases(lang)
    ctx = assistant.SessionContext()
    ctx.Context.update(last_component=component, last_parameter=parameter)
    reply, config, ctx = say(rb, follow_up(cases), lang, ctx)
    assert (ctx.Context["last_component"], ctx.Context["last_parameter"]) == (component, parameter)
    assert reply, "no answer: the reply template or the handler raised"
    assert reply not in [config["answers"][a] for a in ERROR_ANSWERS]


@pytest.mark.parametrize("lang", [lang for lang in LANGUAGES if load_cases(lang).get("display_targets")])
def test_display_targets(rb, lang):
    """A translated wake word names the RB-01 display by the name its voice page knows."""
    cases = load_cases(lang)
    question = follow_up(cases).split(" ", 1)[1]
    for wake, target in cases["display_targets"].items():
        rb.reset()
        ctx = assistant.SessionContext()
        ctx.Context.update(last_component="display", last_parameter="heading")
        say(rb, f"{wake} {question}", lang, ctx)
        assert [body["target"] for body in rb.calls("PUT", "/remote/voice")] == [target], wake
