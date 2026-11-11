# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## What this is

Offline voice assistant for RB-Avionics instruments (RB-01 / RB-03 / RB-06) running on a Raspberry Pi with Stratux. Pilot speech → Vosk STT → keyword/fuzzy NLU → HTTP calls to the local Stratux/RB API → spoken reply through Piper TTS. All recognition and synthesis run on-device; there is no LLM in the loop.

There is no build step or linter. The code only runs end to end on the target device (it needs audio hardware, `/boot/firmware/rb/*` and a reachable Stratux server). The pytest suite in `tests/` covers the NLU, the handlers and the case files without any of that.

## Commands

Deployment, run as root on the Pi (see `Makefile`):

```bash
make venv                                   # create /opt/addons/rbvoice venv + pip install text2num vosk piper-tts
make vosk-model VOICELANG=fr                # default it → vosk-model-<lang>-0.22
make piper-model PIPER_VOICE=fr/fr_FR/mls/medium/fr_FR-mls-medium   # default: Italian "paola"
make install-dependencies-standalone        # sox (needed for noise reduction); also -bluetooth, -android
make install                                # copy *.sh/*.py to /opt/addons/rbvoice/bin, install + enable systemd unit
```

Running:

```bash
systemctl restart rbvoice      # unit runs /opt/addons/rbvoice/bin/rbvoice.sh as user pi, WorkingDirectory=/opt/addons/rbvoice
journalctl -u rbvoice -f       # all output goes to syslog; the code logs via print()
```

Tests, on a dev machine (CI runs the same in `.github/workflows/ci.yml`, on Python 3.11):

```bash
pip install pytest text2num requests
python3 -m pytest tests                     # unit, case-file and integration tests, a few seconds
python3 ci/fetch_vocab.py vosk-model-it-0.22 <dir>   # only graph/words.txt, by HTTP range requests
```

- `tests/test_unit.py` covers the pure functions: frequencies, spelled codes, wake words, aliases, skip words and the engine rules.
- `tests/test_cases.py` runs the case-file checklist below on every language. Breaches that are already in the files are listed in `KNOWN_SKIP_TRIGGERS` / `KNOWN_SKIP_IN_KEYWORDS`. A test fails on a new breach, and also on a fixed one still listed, so the lists only shrink.
- `tests/test_integration.py` calls `handle_utterance` against the fake RB-01 in `tests/conftest.py`:
  - every `rbvoice-test.<lang>.txt` phrase must route as expected and get an answer;
  - the radio, autopilot, timer and display writes must match what the HMI sends;
  - every endpoint in every language must render its reply, both parked and flying.

  The fake serves `tests/fixtures/rb01.json`, trimmed from a real RB-01 capture (roastbeef `test/display/fixtures/rb01-device.json`) plus airfields around Perugia. Like Go's ServeMux, it answers `//path` with a 307. A known defect is a strict `xfail`: it fails CI once fixed, until the marker is removed.

The CI vocabulary job reads each language's Vosk model from the `rbvoice.sh` blocks and runs `vocab_check.py` on it. Problems fail the job for Italian and are warnings for the other languages.

Other offline checks:

```bash
python3 -m py_compile assistant.py rbvoice.py RuleEvaluator.py vocab_check.py voicetest.py
python3 -c "from RuleEvaluator import RuleEvaluator
for r in RuleEvaluator.from_file('rbvoice-aircraft-rules.json').evaluate_rules({'cht1':150,'cht2':160,'cht4':200}):
    print(r.rule_name, r.probe_name, r.color.value, r.result_value)"
# Words Vosk can never write; the model dir needs only graph/words.txt (big) or graph/Gr.fst (small)
python3 vocab_check.py /opt/addons/rbvoice/vosk-models/vosk-model-it-0.22 rbvoice-cases.it.json [word ...]
# Route the test phrases as text (needs text2num + requests only)
python3 voicetest.py rbvoice-cases.it.json --phrases rbvoice-test.it.txt
# Same phrases spoken by Piper and heard by Vosk; or real recordings
python3 voicetest.py rbvoice-cases.it.json --phrases rbvoice-test.it.txt --model <vosk model> --piper <voice.onnx> [--piper ...]
python3 voicetest.py rbvoice-cases.it.json --model <vosk model> --wav /opt/stratux/www/playback/vrin_*.wav
```

`voicetest.py` runs the NLU against a closed port, so it never commands an RB-01, and reads back only the routing. It also reads the value the handler would act on: the radio frequency, or the spelled airfield code. `rbvoice-test.<lang>.txt` lines are `phrase | component | parameter [| value]`, written as a pilot says them, so the same list serves Piper and a cockpit session. Piper adds random variation to each synthesis, so results vary between runs: trust a failure that repeats across runs and voices. An Italian voice reads English loanwords with Italian sounds, so a failure on "heading" or "waypoint" is a reason to record, not to add an alias.

To exercise the NLU without audio, load a case file, set `CONFIG["base_url"]` to a live Stratux, and call `assistant.handle_utterance(text, SessionContext(), CONFIG)`. You need `pip install text2num requests` (the package is `text2num`, the import is `text_to_num`). Keep the code parseable by Python 3.11, which Raspberry Pi OS Bookworm ships: inside an f-string's `{…}`, never reuse the f-string's own quote character.

After editing a case file, verify all language files against the Italian one:
- **Same structure:** the same components, endpoints and `answers` keys, with every component directly under `components`.
- **Same placeholders:** each `reply_template*` uses the same `{placeholders}` with the same number formats. A misspelt placeholder raises `KeyError`, and the pilot hears silence.
- **Valid mappings:** every keyword is lowercase and points to an existing endpoint, and every format spec is valid.
- **Reachable words:** `vocab_check.py` against the language's Vosk model reports no problem.

Then route the test phrases with `voicetest.py … --phrases` (text mode). The matcher's quirks (see the case-file section) mean a translation can be correct and still route badly.

## Architecture

### Audio loop: `rbvoice.py`

`rbvoice.sh` is the real runtime config: language, Vosk/Piper model paths, case file, ALSA in/out devices, Bluetooth MACs, server URL and RMS `--threshold`. Language is chosen by reassigning the variables, so the **last block wins** (the blocks are French, English, Spanish, German, Italian, so Italian is active; each block's comment holds its `make vosk-model` / `make piper-model` command). It prepends its own directory to `PATH`, because `rbvoice.py` calls `pipe-rec.sh` / `pipe-play.sh` by name. To use PipeWire (Android/Waydroid), swap in `pipewire-*.sh` under those names.

Per utterance:
1. `pipe-rec.sh` (arecord at 16 kHz S16_LE mono, piped through `sox noisered` when `/boot/firmware/rb/noise.prof` exists) feeds a reader thread and a queue.
2. An RMS energy gate (`THRESHOLD`, with a `TRAILLEN`=50-chunk hangover) decides when audio goes to Vosk.
3. When Vosk returns a final result, recording is torn down (it restarts on every loop). The input is saved as `vrin_<ts>.wav` and `handle_utterance()` is called.
4. The reply goes to a **persistent** `piper --output_raw` process (22.5 kHz). Its stdout is streamed to `pipe-play.sh`, saved as `vrout_<ts>.wav`, and the conversation is appended to `vr_<startup>.json` in `--rec`.
5. `voiceEvent()` sends SPEAKING/VOICE status asynchronously as a PUT to the `display` component's endpoint (`/remote/voice`), so the RB display can show it.

Each saved WAV fires `$PWD/bin/remotecopy.sh`, which depends on the service's working directory. The script is currently disabled by an early `exit 0`.

**The Vosk grammar.** `buildGrammar()` in `rbvoice.py` (also used by `voicetest.py`) builds the grammar Vosk receives. It is serialized with `ensure_ascii=False`, because Vosk does not decode `\u` escapes and would drop every accented word ("velocità", "température"). A missing `db.airfields.json` only leaves the airfield names out. The grammar holds:
- `extractCommandsFromConfig()`: `wake_words`, `skip_words`, `aliases` keys, `actions.*` triggers, every `components.*.keywords` key and the `SPELLING_ALPHABET` words;
- each word of every airfield name (`/boot/firmware/rb/db.airfields.json`) and frequency name (`db.frequencies.json`).

Text in `answers` and in reply templates is not part of it. How Vosk uses the grammar (from `vosk-api/src/recognizer.cc`):
- It applies only to models that ship a runtime graph (`HCLr.fst`, typical of the small models). With such a model, a word outside the grammar cannot be recognized.
- A model with `HCLG.fst` logs "Runtime graphs are not supported by this model", ignores the grammar and recognizes its full vocabulary.
- Grammar words are looked up in the model vocabulary exactly as written, and the vocabulary is lowercase. A capitalized word ("Foligno", the French keywords "RPM" / "HSI") is dropped with "Ignoring word missing in vocabulary", which is why the name extractors lowercase.
- Number words are not in the grammar, so spoken frequencies only work with a full-vocabulary model.

**The vocabulary cannot grow.** `vosk-model-it-0.22` (447,804 words, static `HCLG.fst`) and `vosk-model-small-it-0.22` (about 200k words, inside `Gr.fst`) cannot take new words. Alpha Cephei publishes no `-compile` package for Italian (it does for `de-0.21` and `ru-0.42`), and the zips hold no tree or lexicon to rebuild the graph. A term outside the vocabulary ("variometro", "sbandometro", "qnh", "squawk") arrives split or spelled ("vario metro", "cu enne acca") and is reached through `aliases`. Italian elided articles are written attached: "l'altimetro" is one word. The lexicon is not shipped either, so an English loanword in the vocabulary ("heading", "waypoint") may still be stored with an Italian pronunciation: confirm it with recordings.

What the Piper tests of the big model showed:
- **Spelled acronyms are unreliable.** The open vocabulary prefers real words: "e gi ti" → "reggiti", "o a ti" → "dorothy". The aliases help when Vosk does write the letters, but each acronym also needs an Italian keyword ("temperatura scarichi", "temperatura esterna").
- **The vocabulary holds misspelled numbers** that `text2num` cannot parse, and Vosk preferred "centodicotto" over "centodiciotto" in every run. In the COM band these are centodicotto (118), centoventiuno (121) and centrotrenta (130), now aliased. Channel decimals end in 0 or 5, and all of those forms parse.
- **"variometro"** (not in the vocabulary) was once heard as "barometro", which routes to the altimeter.

### NLU pipeline: `assistant.handle_utterance()`

1. Rewrite `aliases`, strip `skip_words`, then fuzzy-match a **wake word** against the first one to three words, scoring at least `WAKE_WORD_MIN` (0.85). Aliases come first because spelled acronyms contain skip words ("e gi ti" is EGT). A skip word ending in an apostrophe ("l'", "dell'") is also stripped from the word it is attached to. If none matches, return `""` and stay silent. The matched wake word is stored as `last_wwword`, which replies speak.

   The RB listens on the intercom feed, so it hears radio calls and passengers too: in flight recordings, 356 of 360 utterances were not meant for it. Matching anywhere in the sentence at 0.80 answered radio traffic ("copiate" ~ copriwater, "il secondo" ~ secondario, "tipo di aeromobile").

   The display "target" sent to `/remote/voice` is `last_target`, which is `display_targets[wake word]` or else the wake word itself. On its voice page (`roastbeef/web/RB-01/plates/voice.html`) an RB-01 display can only be named `primario`, `secondario` or `display`, and a named display ignores commands whose target differs (`servicekeypad.js`). So a translated wake word ("primary", "sekundär") must map to one of those three names.
2. `detect_action`: fuzzy-match `actions.GET|SET|DEL` triggers, falling back to `defaults.action`.
3. `detect_parameter` runs against **every** component's `keywords` (`{spoken phrase: parameter}`) and keeps the best score above 0.6. If nothing matches, it reuses `last_component` / `last_parameter` from `SessionContext`, which is how follow-up questions work.
4. Dispatch:
   - Special handlers are hard-coded by component name in an if/elif chain: `autopilot`, `airfields`, `timers`, `traffic`, `display`, `radio`. All share the signature `(context, component, action, parameter, text, config, ctx) -> str`.
   - Every other component (`ems`, `situation`) goes through the generic path:
     1. GET `base_url + endpoints[parameter].path`.
     2. Apply `extract_json_path(json_path)`.
     3. Optionally call the `ValueHelper` method named in `method`.
     4. Return `reply_template.format(**values)`.
5. Any exception is printed and returns `""`, so the failure is silent to the pilot. Check the logs.

Fuzzy matching goes through `similarity_ratio` → `improved_similarity` (a symmetric partial ratio with a prefix boost). Thresholds (0.6 / 0.7 / 0.8) are scattered inline.

### RB-01 API (server side lives in `/home/stefano/roastbeef`)

The special handlers talk to the RB-01 / Stratux daemon. Its routes are registered in `roastbeef/main/managementinterface.go`, and the RB-01 HMI in `roastbeef/web/RB-01/plates/js/` is the reference for how each API is meant to be used. When changing a handler, copy what the HMI does.

- **Timers** (`/timers`): GET returns a list of `{Epoch, CountDown, Status, Fired}`. POST takes a map keyed by index (`{"0": {...}}`). Voice acts **only on the first timer** (by design). As in the HMI's `timerStart()`, every start or stop sets `Epoch` to now and resets `Fired` to false. The server only fires a timer whose `Fired` is false.
- **Radio**: GET `path`, then POST the edited first radio to `path + "/0"`, which sets the **standby** frequency and its label. `handle_radio` writes only a valid VHF COM frequency (`is_com_frequency`, 118.000–136.990, format `"118.500"`). It accepts either:
  - **Numbers**, parsed by `parse_frequency`, as in "centodiciotto punto cinquecento";
  - **A known station name**, as in "imposta frequenza radio perugia approach".

  Known stations are merged the way the HMI's `convertAirFieldDBToAirFrequencies()` does: `frequencies_path` (`db.frequencies.json`, `{"global": {freq: {gps, name}}}`) first, then the airfields' `freq`. For a spoken number, the first entry with that frequency gives the label.

  Names match **word by word**: every spoken word must match a word of the name (≥ `FREQUENCY_NAME_WORD_MIN`). Keyword words, number words and decimal markers are dropped first. `similarity_ratio` is not used here because it scores short stray words too high ("radio" ~ "PADOVA" 0.73). Near-equal matches go to the name the words cover best ("milano" is MILANO, not Milano Linate Airport), then to the nearest station. When nothing qualifies, the reply is `answers.radioNotFound` and the radio is not written.
- **Autopilot** (`/autopilot`, `roastbeef/main/autopilot.go`):
  - GET returns the plan as a list of `Waypoint {Lat, Lon, Ele, Status, Cmt}`, where `Status` is 0 = on the path, 1 = target, 2 = passed.
  - POST replaces the plan, PUT starts it, and DELETE stops it but keeps the plan.
  - `Ele` is an `int32`: a float makes Go reject the whole POST without any error status (it still answers `[]`).
  - Live status (`Active`, GPRMB distance) exists only on the WebSocket. Voice therefore computes distance, bearing and ETE itself from `/getSituation`. `GPSGroundSpeed` there is in **knots**.

How voice uses the autopilot (`handle_autopilot`):
- **SET with an airfield name** ("imposta destinazione perugia") sends the same plan the HMI's `directToAirfield()` sends: current position marked as passed, then the airfield as target, POST then PUT. The `Cmt` label uses the HMI's "name, local_code, gps_code" format.
- **SET with a spelled code** ("imposta destinazione lima india romeo zulu") does the same. `find_airfields` (shared with the airfields GET) first takes the longest run of `SPELLING_ALPHABET` words and single-digit number words of the language, as in "papa golf zero due" → PG02. A code needs at least `SPELLED_CODE_MIN` symbols, one of them a letter, so "lima" or "golf" alone stay names. It must equal an `icao_code`, `gps_code`, `local_code` or `ident` without the dash (IT-0003 is "india tango zero zero zero tre"). All code matches score 1.0, so a code shared by two airfields fails the clear-match rule below. A code that matches no airfield ("lima alfa foxtrot", a misheard LIAF) returns no airfield at all, never a fuzzy name match. A code heard with fewer than 3 symbols falls back to names, where the clear-match rule refused every misheard code tested (best score 0.62). This works in every language.
- **SET without a name** ("avvia autopilota") re-engages the loaded plan.
- **DEL** stops the autopilot.
- **GET** reports the target, or the last waypoint not yet passed.

Two safety rules apply:
1. **Explicit intent.** Direct-to is reachable only through the `autopilot` keywords, never through `airfields`, because `radio` is also an airfields keyword and "imposta radio foligno" would otherwise engage the autopilot.
2. **Clear match.** The airfield must score at least `AIRFIELD_MIN_SCORE` and beat the runner-up by `AIRFIELD_MIN_MARGIN`. Otherwise the reply is `didnotfindairfield` and the plan is left alone.

Autopilot failures reply `answers.autopilotError`, not silence.

### Language case files: `rbvoice-cases.<lang>.json`

These hold all language-specific behaviour and the API mapping:

- `language` is passed to `text2num`.
- `numbers.main_words` are the decimal-point words used by `parse_frequency` for radio frequencies.
- `answers` holds localized fragments used by the handlers. These include the colour names `GREEN/YELLOW/RED/OFF` and the probe labels `cht1..egt4` used by the EMS helper.
- `base_url` in the file is ignored, because `--server` overwrites it.
- `aliases` maps what Vosk writes to the term the keywords use: `{"vario metro": "variometro", "ci acca ti": "cht", "x ray": "xray"}`.
  - Keys must be in the model vocabulary, or the alias never fires; values need not be.
  - Matching uses whole words, longest alias first, in a single pass, and also right after an elision ("l'età" → "l'eta").
  - Only Italian has entries so far. The other files carry an empty map.

Each value in `keywords` must match a key under that component's `endpoints`. When `json_path` is `""`, the template receives the whole JSON object; when it is a dotted path, the template receives that one value, keyed by the last path segment.

Languages: `it` (reference), `fr`, `en`, `es`, `de`. Only change the spoken text when translating:
- **Translate:** `wake_words`, `skip_words`, `aliases`, action triggers, keyword **keys**, `answers` and template text.
- **Keep unchanged:** keyword **values**, endpoint keys, `path` / `method` / `json_path`, and every `{placeholder:format}`.
- **German** EMS replies avoid inflecting the colour words ("Zylinder grün").
- **Spanish** probe labels carry the article ("el primer escape").

The matcher (`detect_action`, `detect_parameter`, `improved_similarity`) is fuzzy and greedy. These rules come from routing tests:
- **No skip word may also be an action trigger.** `detect_action` always removes one word; with no verb left it removes the keyword itself. English lost "what is the altitude" this way.
- **Avoid short or generic triggers.** They fuzzy-match nouns and delete them: "request" ~ "frequency", "enter" ~ "altimeter", "starte" ~ "karte", "para" ~ "mapa", "trova" ~ "sbandometro" (every "-metro" instrument). A trigger inside a noun deletes it too: "andamento" eats "sbandamento". Keep only triggers a pilot actually says.
- **No keyword may be a long prefix of another.** An exact match and a prefix match both score 1.2, and the earliest keyword or component wins. This is why English GPS time avoids "time" (it stole "timer"). German compounds were renamed for the same reason ("kraftstoffmenge" vs "kraftstoffdruck").
- **A keyword that opens the utterance gets the prefix boost,** and it grows as skip words shorten the text. "carburante secondo serbatoio" went to `carburante` (1.03) instead of `secondo serbatoio` (1.0), so the full phrase is now a keyword of its own.
- **Skip words cover the prepositions combined with an article.** Without "dei", "le temperature dei cilindri" tied "temperature motore" with "temperature cilindri". "segna" ("quanto segna il variometro") is a skip word for the same reason. "al" is not one, because "velocità al suolo" contains it.
- **No skip words inside keywords,** because they are stripped from the utterance but not from the keyword. **No wake words inside keywords** either ("display map").
- **No wake word may be a word of radio phraseology** ("strumento", "aeromobile", "aircraft", "Flugzeug"). A radio call that opens with it gets an answer.
- **A display verb can be made an action word** (German "zeige"). The matcher then removes the verb instead of eating the noun.

Spoken frequencies reach `parse_frequency` word by word. Digit-by-digit readings ("one one eight decimal five") and single-word compounds ("centodiciotto", "hundertachtzehn") work. Multi-word numbers ("one hundred eighteen", "cent dix huit", "ciento dieciocho") are refused, never mis-set.

To add a language:
1. Copy the Italian file and translate it, following the rules above.
2. Add a `test -f … || cp` line to `make install`.
3. Add a variable block to `rbvoice.sh` before the Italian one.
4. Fetch the models. Vosk model names don't always follow the Makefile default, so pass `MODEL=`: `vosk-model-en-us-0.22`, `vosk-model-es-0.42`, `vosk-model-de-0.21`. Piper voice paths come from `rhasspy/piper-voices` `voices.json`.
5. Run `vocab_check.py` against the model, and add `aliases` for keyword words Vosk cannot write.

### Engine health: `RuleEvaluator.py` + `rbvoice-aircraft-rules.json`

The `helper_EMS_EngineStatus` method evaluates colour-arc rules over EMS probes. Algorithms are `average`, `max`, `min` and `averageDiff`. For each rule it injects `<RULE>`, `<RULE>PROBELABEL`, `<RULE>PROBE` and `<RULE>VALUE` into the values dict, which is what templates such as `{CHTAVG}` and `{CHTMAXPROBELABEL}` reference. `max`, `min` and `averageDiff` report a probe name, and that name must exist as a key in `answers`, or the whole reply fails.

## Deployed file locations

| Path | Contents |
|---|---|
| `/opt/addons/rbvoice/` | venv. `bin/` holds the copied scripts (so `rbvoice.sh` can `source activate`); also `vosk-models/` and `piper-models/` |
| `/boot/firmware/rb/` | case files, aircraft rules, `db.airfields.json`, `db.frequencies.json` (absent on older images), optional `noise.prof` (create with `CreateNoiseProfile.sh <dir>`). On the RB the web `/settings` folder is a symlink to this folder, so `/settings/db.*.json` on the server is the same data. `assistant.py` hard-codes the rules path. `rbvoice.py` hard-codes the noise-profile, airfields and frequencies paths. |
| `/opt/stratux/www/playback/` | recordings and conversation history (`--rec`) |

`make install` copies the JSON configs only if they are **absent** from `/boot/firmware/rb/`. Editing the JSON in the repo does not update a device that already has them; copy them over by hand.

## Known quirks

- `handle_traffic`, `handle_airfields` and the autopilot call a hard-coded `/getSituation` for own-ship position.
- `handle_radio` still replies "impostata" when the RB-01 has no radio configured (empty `/radio` list).
- `SessionContext.bobby` is unused sample data.
- Some display commands get answered instead of switching the page, in every language. When the display noun is also a `situation` or `ems` keyword, "mostrami altimetro" / "show altimeter" reads the altitude, because the action matcher eats the verb. Italian "mostrami sbandometro" / "mostrami variometro" even switch to the attitude page: "trova" eats the noun, and the leftover "mostrami" matches "mostrami assetto". Making "visualizza" / "mostrami" action words (as German does) only turns these into value readouts, so display synonyms such as "visualizza vario" were left out until the matcher is fixed.
- A bare keyword with no verb ("computer traffico") usually routes nowhere, because `detect_action` removes the keyword itself.
- French keywords still route worse than the others (about 96 of 126 in a keyword sweep). For example, "dis-moi le moteur" switches to the engine page, and "échappements" reads exhaust one only.
- `handle_traffic` computes the clock position as `(12 + int(rel/30)) % 12`. It can say "0" instead of 12 and truncates instead of rounding.
