"""Custom local NLU for the ValueAnchor copilot.

A small, fully offline intent-and-entity layer built on three open-source
projects, wired together for this domain:

* fastapi-adjacent service code calls in through ``engine.IntentEngine``
* ``rapidfuzz`` matches messy text against the active sector's catalogue
* ``symspellpy`` repairs domain vocabulary typos before classification
* ``scikit-learn`` classifies intent from TF-IDF character and word n-grams

``planner`` turns a parsed utterance plus live database rows into an answer
with an explicit reasoning trace. No model weights are downloaded and nothing
leaves the machine.
"""

from .dataset import INTENTS, build_training_data, INTENT_HOW
from .engine import IntentEngine, ParseResult
from .lexicon import Lexicon, build_lexicon

__all__ = ["INTENTS", "IntentEngine", "Lexicon", "ParseResult", "build_lexicon", "build_training_data", "INTENT_HOW"]
