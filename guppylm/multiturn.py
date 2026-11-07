"""Multi-turn conversation data, so Guppy can carry context.

Builds 2-3 turn dialogues in four flavours:
  follow-up   "why" / "tell me more"  -> Guppy elaborates on the SAME topic
  same-topic  a second question on the topic
  switch      an unrelated topic (so Guppy doesn't get stuck)
  recall      user states a name / object / food, later asks Guppy to repeat it

The tokenizer is NOT retrained (existing weights stay valid), so this writes
data/ctx_train.jsonl and data/ctx_eval.jsonl for fine-tuning:

    python -m guppylm context-data
    python -m guppylm train --init checkpoints/best_model.pt --data data_ctx ...
"""

import json
import os
import random

from . import generate_data as gd
from .more_topics import NEW_TOPICS
from .generate_data import FOOD_TYPES, format_sample

FOLLOWUPS = [
    "why", "why is that", "really", "are you sure", "tell me more", "what do you mean",
    "and then", "what else", "go on", "how come", "oh really", "interesting", "i see",
    "can you explain", "what happened next", "hmm", "is that true", "how so",
]
ELABORATE = ["", "", "well. ", "because that's how it is. ", "i think so. ", "also. ",
             "yes. ", "hmm. ", "and. ", "honestly. "]

NAMES = ["mia", "leo", "sam", "zoe", "max", "ava", "ben", "eli", "kai", "lily", "noah", "ruby",
         "finn", "nora", "owen", "ivy", "jack", "luna", "theo", "maya", "sky", "pip", "dave",
         "anna", "tom", "rosa", "hugo", "nina", "omar", "yuki", "sara", "ali", "jin", "elsa"]
THINGS = ["rock", "log", "shell", "castle", "plant", "anchor", "treasure chest", "skull",
          "bridge", "ball", "mirror", "snail", "cave", "tunnel", "pot", "sock", "spoon", "lego"]

NAME_SET = ["my name is {n}", "i'm {n}", "call me {n}", "hi guppy. i'm {n}", "this is {n}"]
NAME_ACK = ["hi {n}. nice to meet you. is that edible.", "{n}. i like it. it sounds bubbly.",
            "hello {n}. i will try to remember.", "{n}. ok. {n}. got it.",
            "hi {n}. you're the big shape named {n}."]
NAME_ASK = ["what's my name", "do you remember my name", "who am i", "what did i say my name was",
            "do you know who i am", "say my name"]
NAME_ANS = ["you're {n}.", "{n}. i remember.", "{n}. i remember things for at least a minute.",
            "your name is {n}. see. i listen.", "{n}. you told me. i held onto it."]

THING_SET = ["i put a {t} in the tank", "i got you a {t}", "look. a {t}", "i added a {t}"]
THING_ACK = ["a {t}. i will inspect it.", "a {t}. new thing. i like new things.",
             "oh. a {t}. is it edible.", "a {t}. i'll swim around it."]
THING_ASK = ["what did i put in the tank", "what did i just give you", "what was the new thing",
             "do you remember what i added", "what was it again"]
THING_ANS = ["a {t}.", "the {t}. i remember.", "it was a {t}. i checked it twice.",
             "a {t}. i'm still looking at it."]

FOOD_SET = ["i fed you {f}", "here's some {f}", "today you get {f}", "i have {f} for you"]
FOOD_ACK = ["{f}. yes. i love {f}.", "{f}. thank you. eating now.", "oh. {f}. my favorite today."]
FOOD_ASK = ["what did i feed you", "what did you eat", "what was the food", "what was it you ate"]
FOOD_ANS = ["{f}. it was good.", "{f}. i'm still thinking about it.", "i ate {f}. remember."]


def _topics():
    tops = [v for k, v in vars(gd).items()
            if k.startswith("gen_") and callable(v) and k != "gen_bye"]
    return tops + list(NEW_TOPICS)


def _turn(gen):
    s = gen()
    return s["input"], s["output"]


def _recall(topics):
    kind = random.choice(["name", "name", "thing", "food"])
    if kind == "name":
        v = {"n": random.choice(NAMES)}; sets, acks, asks, ans = NAME_SET, NAME_ACK, NAME_ASK, NAME_ANS
    elif kind == "thing":
        v = {"t": random.choice(THINGS)}; sets, acks, asks, ans = THING_SET, THING_ACK, THING_ASK, THING_ANS
    else:
        v = {"f": random.choice(FOOD_TYPES)}; sets, acks, asks, ans = FOOD_SET, FOOD_ACK, FOOD_ASK, FOOD_ANS
    turns = [(random.choice(sets).format(**v), random.choice(acks).format(**v))]
    if random.random() < 0.45:      # a distractor turn between the fact and the question
        turns.append(_turn(random.choice(topics)))
    turns.append((random.choice(asks), random.choice(ans).format(**v)))
    return turns, "recall_" + kind


def make_dialogue(topics):
    if random.random() < 0.18:
        return _recall(topics)
    gen = random.choice(topics)
    turns = [_turn(gen)]
    cat = gen.__name__[4:]
    for _ in range(random.choice([1, 1, 2])):
        mode = random.choices(["follow", "same", "switch"], [0.45, 0.35, 0.20])[0]
        if mode == "follow":
            for _try in range(5):
                _, out = _turn(gen)
                if out != turns[-1][1]:
                    break
            turns.append((random.choice(FOLLOWUPS), random.choice(ELABORATE) + out))
        elif mode == "same":
            turns.append(_turn(gen))
        else:
            gen = random.choice(topics)
            turns.append(_turn(gen))
    return turns, cat


def format_dialogue(turns):
    return "\n".join(
        f"<|im_start|>user\n{u}<|im_end|>\n<|im_start|>assistant\n{a}<|im_end|>" for u, a in turns)


def generate_context_data(n_multi=24000, n_single=16000, eval_ratio=0.05, out_dir="data_ctx"):
    topics = _topics()
    rows = []
    for _ in range(n_multi):
        turns, cat = make_dialogue(topics)
        rows.append({"text": format_dialogue(turns), "category": "ctx_" + cat})
    for _ in range(n_single):            # keep single-turn skills sharp
        s = random.choice(topics)()
        rows.append({"text": format_sample(s), "category": s["category"]})
    random.shuffle(rows)
    n_eval = int(len(rows) * eval_ratio)
    os.makedirs(out_dir, exist_ok=True)
    for name, part in (("eval.jsonl", rows[:n_eval]), ("train.jsonl", rows[n_eval:])):
        with open(os.path.join(out_dir, name), "w") as f:
            for r in part:
                f.write(json.dumps(r) + "\n")
    # reuse the existing tokenizer so the trained weights stay valid
    tok = os.path.join("data", "tokenizer.json")
    if os.path.exists(tok):
        with open(tok) as src, open(os.path.join(out_dir, "tokenizer.json"), "w") as dst:
            dst.write(src.read())
    print(f"Wrote {len(rows) - n_eval} train / {n_eval} eval rows to {out_dir}/ "
          f"({n_multi} multi-turn, {n_single} single-turn)")


if __name__ == "__main__":
    random.seed(1)
    generate_context_data()
