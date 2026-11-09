"""Unit tests: the pure functions of the NLU and the engine rules, no case file or server needed."""
import pytest

import assistant
from RuleEvaluator import RuleEvaluator
from conftest import RULES

IT_NUMBERS = {"main_words": ["punto", "virgola", "decimali", "decimale", "e"]}
EN_NUMBERS = {"main_words": ["point", "decimal", "dot"]}


@pytest.mark.parametrize("text, lang, numbers, frequency", [
    ("centodiciotto punto cinque", "it", IT_NUMBERS, "118.500"),
    ("centodiciannove punto cinquecentocinquantacinque", "it", IT_NUMBERS, "119.555"),
    ("centotrentadue e cinquanta", "it", IT_NUMBERS, "132.500"),
    ("uno uno otto punto uno", "it", IT_NUMBERS, "118.100"),
    ("one one eight decimal five", "en", EN_NUMBERS, "118.500"),
])
def test_parse_frequency(text, lang, numbers, frequency):
    assert assistant.parse_frequency(text, lang, numbers) == frequency


@pytest.mark.parametrize("text, lang, numbers", [
    # Multi-word numbers are refused, never mis-set
    ("one hundred eighteen decimal five", "en", EN_NUMBERS),
    # Misspelt in the Vosk vocabulary and unknown to text2num: reached through an alias
    ("centodicotto punto cinque", "it", IT_NUMBERS),
])
def test_parse_frequency_refused(text, lang, numbers):
    assert not assistant.is_com_frequency(assistant.parse_frequency(text, lang, numbers))


@pytest.mark.parametrize("frequency, valid", [
    ("118.000", True), ("136.990", True), ("121.500", True),
    ("117.975", False), ("137.000", False), ("118.5", False), ("0000000", False), (".500000", False),
])
def test_is_com_frequency(frequency, valid):
    assert assistant.is_com_frequency(frequency) is valid


@pytest.mark.parametrize("text, code", [
    ("lima india romeo zulu", "LIRZ"),
    ("imposta destinazione lima india romeo zulu", "LIRZ"),
    ("papa golf zero due", "PG02"),
    ("india tango zero zero zero tre", "IT0003"),
    # Fewer than SPELLED_CODE_MIN symbols, or no letter: an ordinary word, not a code
    ("lima", ""), ("hotel alfa", ""), ("uno due tre", ""),
])
def test_spelled_code(text, code):
    assert assistant.spelled_code(text, "it") == code


def test_airfield_codes_drop_the_dash():
    airfield = {"gps_code": "IT-0003", "ident": "IT-0003", "local_code": "AR01"}
    assert assistant.airfield_codes(airfield) == {"IT0003", "AR01"}


@pytest.mark.parametrize("text, wake_word, remaining", [
    ("computer dimmi la quota", "computer", "dimmi la quota"),
    ("ehi computer dimmi", "ehi computer", "dimmi"),
    # Vosk splits a wake word: the first three words are tried together
    ("copri water dimmi", "copriwater", "dimmi"),
])
def test_wake_word(text, wake_word, remaining):
    score, word, rest = assistant.detect_wake_word(text, ["computer", "copriwater", "secondario", "ehi computer"])
    assert (word, rest) == (wake_word, remaining)
    assert score >= assistant.WAKE_WORD_MIN


@pytest.mark.parametrize("text", [
    # Radio traffic heard in flight: close to a wake word, or with the wake word later in the sentence
    "copiate il secondo",
    "acceso il computer",
    "tipo di aeromobile",
])
def test_no_wake_word(text):
    assert assistant.detect_wake_word(text, ["computer", "copriwater", "secondario"]) == (0, None, None)


def test_aliases_whole_words_longest_first():
    aliases = {"vario metro": "variometro", "metro": "metri", "e ti a": "eta"}
    assert assistant.apply_aliases("Quanto segna il vario metro", aliases) == "quanto segna il variometro"
    assert assistant.apply_aliases("dieci metro", aliases) == "dieci metri"
    # Not inside a word, but right after an elision
    assert assistant.apply_aliases("metronomo", aliases) == "metronomo"
    assert assistant.apply_aliases("dimmi l'e ti a", aliases) == "dimmi l'eta"


def test_skip_words():
    skip = ["il", "l'", "dell'"]
    assert assistant.remove_skip_words("dimmi l'altimetro", skip) == "dimmi altimetro"
    assert assistant.remove_skip_words("temperatura dell'olio", skip) == "temperatura olio"
    assert assistant.remove_skip_words("quanto segna il variometro", skip) == "quanto segna variometro"
    assert assistant.remove_skip_words("velocità al suolo", skip) == "velocità al suolo"


def test_extract_json_path():
    data = {"a": {"b": 3}, "c": 0}
    assert assistant.extract_json_path(data, "") == data
    assert assistant.extract_json_path(data, "a.b") == 3
    assert assistant.extract_json_path(data, "c") == 0
    assert assistant.extract_json_path(data, "missing") is None


def test_gps_distance_bearing():
    distance, bearing = assistant.gps_distance_bearing(43, 12, 44, 12)
    assert distance == pytest.approx(111.19, abs=0.01)
    assert bearing == pytest.approx(0)
    distance, bearing = assistant.gps_distance_bearing(43, 12, 43, 11)
    assert bearing == pytest.approx(270, abs=0.5)


@pytest.mark.parametrize("statuses, expected", [
    ([2, 1, 0], 1),     # the target
    ([2, 0, 0], 2),     # else the last waypoint not passed
    ([2, 2], None),     # all passed
    ([], None),
])
def test_autopilot_target(statuses, expected):
    waypoints = [{"Status": s, "Cmt": str(i)} for i, s in enumerate(statuses)]
    target = assistant.autopilot_target(waypoints)
    assert (target and int(target["Cmt"])) == expected


def test_engine_rules():
    results = {r.rule_name: r for r in RuleEvaluator.from_file(RULES).evaluate_rules({"cht1": 150, "cht2": 160, "cht4": 200})}
    assert (results["CHTAVG"].color.value, results["CHTAVG"].result_value) == ("GREEN", 170.0)
    assert (results["CHTMAX"].probe_name, results["CHTMAX"].color.value) == ("cht4", "YELLOW")
    assert (results["CHTMIN"].probe_name, results["CHTMIN"].color.value) == ("cht1", "GREEN")
    assert (results["CHTDIFF"].probe_name, results["CHTDIFF"].color.value) == ("cht4", "RED")
    # Probes with no reading are OFF
    assert results["OILAVG"].color.value == "OFF"
