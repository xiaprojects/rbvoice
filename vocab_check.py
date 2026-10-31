#!/usr/bin/env python3
"""
Offline check of a case file against a Vosk model vocabulary.

Vosk only writes words of its model vocabulary: graph/words.txt in the big models, the symbol
table inside graph/Gr.fst in the small ones. A keyword word outside it is never heard as written
and needs an alias that rewrites what Vosk does write ("vario metro" -> "variometro").

  python3 vocab_check.py <model dir> <cases.json> [word ...]

Reports, and exits 1 if there is any:
  - an alias whose spoken words are outside the vocabulary (it can never fire);
  - a wake word, skip word or action trigger outside the vocabulary;
  - a keyword word outside the vocabulary that no alias produces;
  - a letter of the spelling alphabet with no spelling in the vocabulary or alias.
Words given after the case file are only looked up.
"""
import json
import struct
import sys
from pathlib import Path

FST_MAGIC = 2125659606
SYMBOL_TABLE_MAGIC = 2125658996


def read_fst_symbols(path):
    """Output symbols of an OpenFst binary file (the vocabulary inside Gr.fst)."""
    with open(path, 'rb') as f:
        def i32(): return struct.unpack('<i', f.read(4))[0]
        def i64(): return struct.unpack('<q', f.read(8))[0]
        def string(): return f.read(i32()).decode('utf-8')

        def symbol_table():
            if i32() != SYMBOL_TABLE_MAGIC:
                raise ValueError(f"{path}: no symbol table")
            string()  # name
            i64()     # available key
            symbols = []
            for _ in range(i64()):
                symbols.append(string())
                i64()  # key
            return symbols

        if i32() != FST_MAGIC:
            raise ValueError(f"{path}: not an OpenFst file")
        string(); string()        # fst type, arc type
        i32(); flags = i32()      # version, flags
        i64(); i64(); i64(); i64()  # properties, start, states, arcs
        input_symbols = symbol_table() if flags & 1 else []
        output_symbols = symbol_table() if flags & 2 else []
        return output_symbols or input_symbols


def load_vocabulary(model_dir):
    graph = Path(model_dir) / "graph"
    if (graph / "words.txt").is_file():
        with open(graph / "words.txt", encoding="utf-8") as f:
            return {line.split()[0] for line in f if line.strip()}
    if (graph / "Gr.fst").is_file():
        return set(read_fst_symbols(graph / "Gr.fst"))
    raise FileNotFoundError(f"{model_dir}: neither graph/words.txt nor graph/Gr.fst")


def missing(phrase, vocabulary):
    return [w for w in phrase.split() if w not in vocabulary]


def check(config, vocabulary):
    problems = []
    aliases = config.get('aliases', {})
    for spoken in aliases:
        for w in missing(spoken, vocabulary):
            problems.append(f"alias '{spoken}': '{w}' not in vocabulary, the alias never fires")

    for section, phrases in (("wake word", config.get('wake_words', [])),
                             ("skip word", config.get('skip_words', [])),
                             ("action", [t for triggers in config.get('actions', {}).values() for t in triggers])):
        for phrase in phrases:
            if phrase.endswith("'"):
                continue  # an elided article ("l'") is written attached to the next word
            for w in missing(phrase, vocabulary):
                problems.append(f"{section} '{phrase}': '{w}' not in vocabulary")

    produced = {w for term in aliases.values() for w in term.split()}
    for comp_name, comp in config.get('components', {}).items():
        for kw in comp.get('keywords', {}):
            for w in missing(kw, vocabulary):
                if w not in produced:
                    problems.append(f"keyword '{kw}' ({comp_name}): '{w}' not in vocabulary and no alias produces it")

    try:
        sys.path.insert(0, str(Path(__file__).resolve().parent))
        from assistant import SPELLING_ALPHABET
    except ImportError as e:
        print(f"Spelling alphabet not checked: {e}")
    else:
        letters = {}
        for word, symbol in SPELLING_ALPHABET.items():
            letters.setdefault(symbol, []).append(word)
        for symbol, words in sorted(letters.items()):
            if not symbol.isalpha():
                continue  # digits are the number words of the language ("nove"), "niner" is a bonus
            if not any(w in vocabulary for w in words) and not any(w in aliases.values() for w in words):
                problems.append(f"spelling '{symbol}': none of {words} in vocabulary")
    return problems


def main():
    if len(sys.argv) < 3:
        print(__doc__)
        sys.exit(2)
    vocabulary = load_vocabulary(sys.argv[1])
    with open(sys.argv[2], encoding="utf-8") as f:
        config = json.load(f)
    print(f"{len(vocabulary)} words in the {sys.argv[1]} vocabulary")
    for word in sys.argv[3:]:
        print(f"{word}: {'in vocabulary' if word.lower() in vocabulary else 'NOT in vocabulary'}")
    problems = check(config, vocabulary)
    for p in problems:
        print(p)
    print(f"{len(problems)} problem(s)")
    sys.exit(1 if problems else 0)


if __name__ == "__main__":
    main()
