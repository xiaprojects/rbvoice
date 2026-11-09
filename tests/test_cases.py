"""
Case-file checks, from the CLAUDE.md checklist: every language file has the structure, the
placeholders and the API mapping of the Italian reference, and avoids the known matcher traps.
"""
import json
import re
import string

import pytest

from conftest import LANGUAGES, REFERENCE, RULES, load_cases

# Keys a translation may add: display_targets maps a translated wake word to an RB-01 display name
OPTIONAL_KEYS = {"display_targets"}
# The names an RB-01 display can have on its voice page (roastbeef web/RB-01/plates/voice.html)
DISPLAY_NAMES = {"primario", "secondario", "display"}
# Endpoint fields that are API mapping, never translated
API_FIELDS = ("path", "frequencies_path", "method", "json_path")
# Endpoint fields no handler reads: Italian only
UNUSED_FIELDS = {"reply_template_old"}
# Rule algorithms that report a probe, whose name the EMS reply speaks from answers
PROBE_ALGORITHMS = {"max", "min", "averageDiff"}
# Components with a handler of their own in handle_utterance(); the others take the generic path,
# which reads endpoints[parameter]
SPECIAL_HANDLERS = {"autopilot", "airfields", "timers", "traffic", "display", "radio"}

# Known breaches of the matcher rules, kept so that CI fails on a new one and on a fixed one still
# listed here: this list should only shrink
KNOWN_SKIP_TRIGGERS = {
    "it": {"dove", "cosa"},
    "fr": {"où", "est", "quoi"},
}
KNOWN_SKIP_IN_KEYWORDS = {
    "it": {"cerca per"},
    "en": {"time to destination", "search for"},
    "fr": {"vue indicateur de virage", "affiche indicateur de virage", "indicateur de virage",
           "taux de virage", "température de l'air"},
    "de": {"such mir"},
}

reference = load_cases(REFERENCE)
translations = [lang for lang in LANGUAGES if lang != REFERENCE]


def fields(endpoint):
    return set(endpoint) - UNUSED_FIELDS


def templates(endpoint):
    return {k: v for k, v in endpoint.items() if k.startswith("reply_template") and k not in UNUSED_FIELDS}


def placeholders(template):
    """The sorted (name, conversion, format) of every {placeholder}; raises on a malformed template."""
    return sorted((name, conversion or "", spec or "")
                  for _, name, spec, conversion in string.Formatter().parse(template) if name is not None)


def test_languages_found():
    assert REFERENCE in LANGUAGES and len(LANGUAGES) > 1


@pytest.mark.parametrize("lang", translations)
def test_same_structure(lang):
    cases = load_cases(lang)
    assert set(cases) - OPTIONAL_KEYS == set(reference) - OPTIONAL_KEYS
    for key in ("answers", "actions", "numbers"):
        assert set(cases[key]) == set(reference[key]), key
    assert cases["defaults"] == reference["defaults"]
    assert set(cases["components"]) == set(reference["components"])
    for name, component in reference["components"].items():
        translated = cases["components"][name]
        assert set(translated) == set(component), name
        assert set(translated["endpoints"]) == set(component["endpoints"]), name
        for key, endpoint in component["endpoints"].items():
            assert fields(translated["endpoints"][key]) == fields(endpoint), f"{name}.{key}"


@pytest.mark.parametrize("lang", translations)
def test_same_api_mapping(lang):
    cases = load_cases(lang)
    for name, component in reference["components"].items():
        for key, endpoint in component["endpoints"].items():
            for field in API_FIELDS:
                if field in endpoint:
                    assert cases["components"][name]["endpoints"][key][field] == endpoint[field], f"{name}.{key}.{field}"


@pytest.mark.parametrize("lang", translations)
def test_same_placeholders(lang):
    """A misspelt placeholder raises KeyError on the device, and the pilot hears silence."""
    cases = load_cases(lang)
    for name, component in reference["components"].items():
        for key, endpoint in component["endpoints"].items():
            for field, template in templates(endpoint).items():
                translated = cases["components"][name]["endpoints"][key][field]
                assert placeholders(translated) == placeholders(template), f"{name}.{key}.{field}: {translated!r}"


@pytest.mark.parametrize("lang", LANGUAGES)
def test_format_specs_valid(lang):
    cases = load_cases(lang)
    for name, component in cases["components"].items():
        for key, endpoint in component["endpoints"].items():
            for field, template in templates(endpoint).items():
                for placeholder, _, spec in placeholders(template):
                    if spec:
                        format(1.5, spec)  # every spec in use is numeric


@pytest.mark.parametrize("lang", LANGUAGES)
def test_keywords(lang):
    cases = load_cases(lang)
    for name, component in cases["components"].items():
        for keyword, parameter in component["keywords"].items():
            # Vosk drops grammar words that are not lowercase, and so never writes them
            assert keyword == keyword.lower(), f"{name}: {keyword!r}"
            if name not in SPECIAL_HANDLERS:
                assert parameter in component["endpoints"], f"{name}: {keyword!r} -> {parameter!r}"
            # Keyword values are not translated: display ones are RB-01 page names
            assert parameter in reference["components"][name]["keywords"].values(), f"{name}: {keyword!r} -> {parameter!r}"


@pytest.mark.parametrize("lang", LANGUAGES)
def test_grammar_words_lowercase(lang):
    cases = load_cases(lang)
    phrases = cases["wake_words"] + cases["skip_words"] + list(cases["aliases"])
    phrases += [t for triggers in cases["actions"].values() for t in triggers]
    assert [p for p in phrases if p != p.lower()] == []


@pytest.mark.parametrize("lang", LANGUAGES)
def test_display_targets(lang):
    cases = load_cases(lang)
    targets = cases.get("display_targets", {})
    assert set(targets) <= set(cases["wake_words"])
    assert set(targets.values()) <= DISPLAY_NAMES


@pytest.mark.parametrize("lang", LANGUAGES)
def test_rule_labels_in_answers(lang):
    """helper_EMS_EngineStatus speaks the colour and the probe of each rule from answers."""
    answers = load_cases(lang)["answers"]
    rules = json.loads(RULES.read_text())["rules"]
    for colour in ("GREEN", "YELLOW", "RED", "OFF"):
        assert colour in answers
    for rule in rules:
        if rule["algorithm"] in PROBE_ALGORITHMS:
            for probe in rule["probeNames"]:
                assert probe in answers, f"{rule['name']}: {probe}"


def known_diff(found, known_set):
    new, fixed = found - known_set, known_set - found
    return f"new: {sorted(new)}; fixed, remove from the known list: {sorted(fixed)}"


@pytest.mark.parametrize("lang", LANGUAGES)
def test_no_skip_word_is_a_trigger(lang):
    """detect_action always removes a word: with no verb left it removes the keyword itself."""
    cases = load_cases(lang)
    triggers = {t for triggers in cases["actions"].values() for t in triggers}
    found = set(cases["skip_words"]) & triggers
    assert found == KNOWN_SKIP_TRIGGERS.get(lang, set()), known_diff(found, KNOWN_SKIP_TRIGGERS.get(lang, set()))


@pytest.mark.parametrize("lang", LANGUAGES)
def test_no_skip_word_inside_keywords(lang):
    """Skip words are stripped from the utterance, not from the keyword."""
    cases = load_cases(lang)
    skip = set(cases["skip_words"])
    found = {kw for component in cases["components"].values() for kw in component["keywords"] if skip & set(kw.split())}
    assert found == KNOWN_SKIP_IN_KEYWORDS.get(lang, set()), known_diff(found, KNOWN_SKIP_IN_KEYWORDS.get(lang, set()))


@pytest.mark.parametrize("lang", LANGUAGES)
def test_no_wake_word_inside_keywords(lang):
    """The wake word is consumed first, so a keyword holding one can lose it ("display map")."""
    cases = load_cases(lang)
    for name, component in cases["components"].items():
        for keyword in component["keywords"]:
            for w in cases["wake_words"]:
                assert not re.search(rf"(?<!\S){re.escape(w)}(?!\S)", keyword), f"{name}: {keyword!r} holds {w!r}"
