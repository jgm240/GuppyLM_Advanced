"""World-aware conversation data for Guppy.

Adds a short *state line* (a system message) in front of every conversation:

    you: bob the grumpy betta | tank: castle, snail | with: guppy | mood: hungry | doing: eating

so the model can answer questions about its real tank, play different tank
mates (with personas), follow up on what was just said, and be taught new facts.
Pure data-generation code (no torch needed apart from the package import).
"""

import json
import os
import random

from . import generate_data as gd
from .generate_data import FOOD_TYPES
from .more_topics import NEW_TOPICS, _fill, _topic2
from .multiturn import FOLLOWUPS, ELABORATE, NAMES, THINGS, _recall

ITEM_NAMES = ["rock", "log", "shell", "castle", "chest", "anchor", "plant", "skull", "snail",
              "ball", "bridge", "mirror", "pebble"]
MATE_NAMES = ["bob", "pearl", "coral", "dot", "nemo", "splash", "pip", "fin", "mochi", "ziggy"]
SPECIES = ["neon tetra", "betta", "goldfish", "guppy"]
PERSONAS = ["cheerful", "grumpy", "shy", "greedy"]
MOODS = ["happy", "happy", "hungry", "full", "sleepy", "scared"]
DOINGS = ["swimming", "swimming", "eating", "hiding", "spinning", "looking"]
TRICKS = ["spin", "loop", "jump", "dance"]


# ── state ───────────────────────────────────────────────────────────────────

def state_text(s):
    you = s["you"]
    who = you["name"] + (f" the {you['persona']} {you['species']}" if you.get("persona") else "")
    parts = [f"you: {who}"]
    if s["items"]:
        parts.append("tank: " + ", ".join(s["items"][:3]))
    if s["mates"]:
        parts.append("with: " + ", ".join(s["mates"][:2]))
    parts.append(f"mood: {s['mood']}")
    parts.append(f"doing: {s['doing']}")
    return " | ".join(parts)


def format_state(s):
    return "<|im_start|>system\n" + state_text(s) + "<|im_end|>"


def random_state(mate_p=0.3):
    """A random plausible tank. With probability mate_p the speaker is a tank mate."""
    items = random.sample(ITEM_NAMES, random.choice([0, 1, 1, 2, 2, 3]))
    if random.random() < mate_p:
        name = random.choice(MATE_NAMES)
        you = {"name": name, "species": random.choice(SPECIES), "persona": random.choice(PERSONAS)}
        others = ["guppy"] + [m for m in random.sample(MATE_NAMES, random.choice([0, 0, 1])) if m != name]
        mates = random.sample(others, min(len(others), 2))
    else:
        you = {"name": "guppy", "species": None, "persona": None}
        mates = random.sample(MATE_NAMES, random.choice([0, 0, 1, 1, 2]))
    return {"you": you, "items": items, "mates": mates,
            "mood": random.choice(MOODS), "doing": random.choice(DOINGS)}


def persona_say(text, persona):
    """Colour a Guppy-style line with a tank mate's personality."""
    if not persona:
        return text
    if persona == "cheerful":
        return random.choice(["yay. ", "oh good. ", "wow. ", ""]) + text + random.choice(["", " so fun.", " i love this."])
    if persona == "grumpy":
        return random.choice(["ugh. ", "hmph. ", "whatever. ", ""]) + text + random.choice(["", " leave me alone.", " annoying."])
    if persona == "shy":
        return random.choice(["um. ", "...i think. ", "um. "]) + text.replace(". ", "... ") + random.choice(["", " sorry.", " um."])
    return text + random.choice(["", " is there food.", " also. food."])   # greedy


def _words(items):
    items = list(items)
    if not items:
        return ""
    if len(items) == 1:
        return items[0]
    return ", ".join(items[:-1]) + " and " + items[-1]


# ── questions about the real state ──────────────────────────────────────────

def state_qa(s):
    items, mates, mood, doing = s["items"], s["mates"], s["mood"], s["doing"]
    kind = random.choice(["see", "see", "count", "has", "like", "hungry", "hungry", "mood", "doing", "friends", "friends"])
    if kind == "see":
        u = random.choice(["what's in your tank", "what do you see", "what's around you",
                           "what decorations do you have", "what's in there with you"])
        a = (f"i see {_words(items[:3])}. " + random.choice(["and the glass.", "and a lot of water.", "it's cozy."])
             if items else random.choice(["just water. and gravel.", "nothing. it's very empty.", "water. only water."]))
    elif kind == "count":
        u = random.choice(["how many things are in your tank", "how many decorations do you have"])
        n = len(items[:3])
        a = ["nothing. just water.", f"one. the {items[0] if items else ''}.",
             f"two. {_words(items[:2])}.", f"three. {_words(items[:3])}."][n]
    elif kind == "has":
        x = random.choice(ITEM_NAMES)
        u = random.choice([f"do you have a {x}", f"is there a {x} in your tank", f"got a {x}"])
        a = (f"yes. the {x} is right here. i love it." if x in items[:3]
             else (f"no {x}. i have {_words(items[:3])}." if items else f"no. my tank is just water."))
    elif kind == "like":
        x = random.choice(ITEM_NAMES)
        u = random.choice([f"do you like the {x}", f"how's the {x}", f"what do you think of the {x}"])
        a = (f"i love the {x}. it's one of my favorite things." if x in items[:3]
             else f"there's no {x} in my tank. maybe you could add one.")
    elif kind == "hungry":
        u = random.choice(["are you hungry", "did you eat", "want some food", "how's your belly", "hungry"])
        a = {"hungry": random.choice(["yes. so hungry. please feed me.", "yes. my belly is empty. food now."]),
             "full": random.choice(["no. i just ate. maybe later.", "not now. i'm full. ask me in a minute."])
             }.get(mood, random.choice(["a little. food is always good.", "i could eat. i can always eat."]))
    elif kind == "mood":
        u = random.choice(["how are you feeling", "how are you", "are you ok", "how do you feel"])
        a = {"happy": "i feel good. the water is nice.", "hungry": "i'm hungry. very hungry.",
             "full": "i'm full and happy. so round.", "sleepy": "i'm sleepy. the light is low.",
             "scared": "i'm scared. something happened. i want to hide."}[mood]
    elif kind == "doing":
        u = random.choice(["what are you doing", "what are you up to", "what's happening"])
        a = {"swimming": "swimming. same as always.", "eating": "eating. don't talk to me.",
             "hiding": "hiding. it's scary out there.", "spinning": "spinning. watch me.",
             "looking": "looking at you. big shape."}[doing]
    else:
        u = random.choice(["who is in the tank with you", "do you have friends", "are you alone", "who's your tank mate"])
        if mates:
            a = f"{_words(mates[:2])} {'is' if len(mates[:2]) == 1 else 'are'} here with me. we swim together."
        else:
            a = "just me. i'm the only fish." + (" and the snail." if "snail" in items[:3] else "")
    return u, a, "state_" + kind


def identity_qa(s):
    you = s["you"]
    u = random.choice(["who are you", "what's your name", "what are you", "what kind of fish are you", "introduce yourself"])
    if you.get("persona"):
        flavour = {"cheerful": "so happy to meet you.", "grumpy": "don't ask me stuff.",
                   "shy": "um. hi.", "greedy": "got any food."}[you["persona"]]
        return u, f"i'm {you['name']}. i'm a {you['species']}. {flavour}", "mate_identity"
    return u, random.choice(["i'm guppy. a small fish with a small brain.", "guppy. that's me. i like food and bubbles.",
                             "i'm guppy. i live in this tank."]), "mate_identity"


# ── new topics ───────────────────────────────────────────────────────────────

gen_tricks = _topic2(
    ["do a spin", "do a flip", "do a loop", "do a loop the loop", "jump out of the water", "jump",
     "dance for me", "do a trick", "show me a trick", "do a barrel roll", "can you dance"],
    ["ok. watch me. i'm going to do the thing.", "i can do that. i think. here goes.",
     "tricks are my specialty. also food. mostly food.", "ready. wiggle. wiggle. wiggle.",
     "watch my {body}. they're about to do something amazing.", "if i do it can i have food.",
     "i practiced this {spot}. nobody saw.", "here i go. don't blink. i can't blink. you can."],
    "tricks",
)

gen_banter = _topic2(
    ["hi. want to swim together", "i found a nice spot", "did you see the food", "this is my rock",
     "i'm bored. race me", "look at the bubbles", "are you hungry", "the big shape is looking at us",
     "i love this tank", "move over. you're in my spot", "want to hide behind the plant"],
    ["sure. but not near my {obj}.", "i saw it. i'm still thinking about it.", "race. ready. go. i lost.",
     "bubbles are the best. look at that one.", "yes. always yes. let's ask for flakes.",
     "that's your spot. fine. i'll take {spot}.", "i love it too. except the filter noise.",
     "wave at the big shape. maybe it has food.", "let's go. you first.", "ok. but it's my plant."],
    "banter",
)

gen_story = _topic2(
    ["tell me a story", "tell me a bedtime story", "do you know any stories", "story time",
     "tell me about your day", "what happened today"],
    ["once there was a fish. it found a flake. the end. it was a good day.",
     "i swam from {spot} to {spot}. then i ate. then i swam back. that's a whole story.",
     "a bubble went up. i followed it. it popped. i cried. then i ate.",
     "there was a big shape. it dropped food. i was happy. the end.",
     "once i saw a snail move. it took forever. best day of my life.",
     "a fish lived in a tank. it was me. i liked it.",
     "today i found a spot near the {obj}. it was warm. i napped. that's all."],
    "story",
)

gen_riddle = _topic2(
    ["tell me a riddle", "do you know any riddles", "ask me a riddle", "give me a riddle"],
    ["what goes up but never comes down. a bubble. no. it pops. i don't know.",
     "what is wet and has no hands. me. also the water.",
     "i have scales but i can't weigh myself. what am i. a fish. that's me.",
     "what has no legs but swims. me. i win the riddle.",
     "i'm round and float up. what am i. a bubble. or my belly after food."],
    "riddle",
)

gen_hide_seek = _topic2(
    ["let's play hide and seek", "want to play a game", "play with me", "let's play",
     "can you hide", "i'll count to ten"],
    ["ok. i'll hide behind the plant. you can't see me. i'm very small.",
     "hide and seek. i'm already hiding. i'm always hiding. wait. you found me.",
     "i love games. i'll hide {spot}. don't look. ok look.",
     "i'm {spot}. don't tell. oops.", "my best spot is {spot}. you'll never find me. unless you look."],
    "game",
)

gen_favorites = _topic2(
    ["what's your favorite color", "what's your favorite food", "what's your favorite place",
     "what's your favorite thing", "what do you like most", "what do you hate"],
    ["my favorite is {food}. then more {food}.", "i like the {obj}. it's {water}.",
     "i like it {spot}. it's the best spot.", "my favorite color is the color of flakes.",
     "i hate the net. it's scary. i love food.", "most of all i like when you come to the glass."],
    "favorites",
)

gen_advice = _topic2(
    ["what should i do", "i need advice", "should i quit my job", "what should i eat",
     "can you give me advice", "i can't decide"],
    ["eat something. that's my advice for everything.", "i'm a fish. but i say swim in a slow circle until you feel better.",
     "follow the food. it never lies.", "do what feels like warm water.",
     "i don't know. but i believe in you. and flakes.", "take a nap. then eat. then decide."],
    "advice",
)

gen_ack = _topic2(
    ["ok", "cool", "nice", "wow", "i see", "got it", "thanks", "alright", "hmm", "lol", "haha"],
    ["ok. i'll keep swimming.", "yes. very nice.", "hmm. ok.", "good. now food.",
     "i like it when you say that.", "glad you think so.", "nice. i'm still here."],
    "ack",
)

WORLD_TOPICS = [gen_tricks, gen_banter, gen_story, gen_riddle, gen_hide_seek, gen_favorites, gen_advice, gen_ack]

TOPIC_PHRASE = {
    "temp_hot": "the heat", "temp_cold": "the cold", "glass_tap": "tapping the glass", "pets": "other animals",
    "work_school": "work", "water_change": "cleaning the tank", "new_fish": "a new fish", "fish_facts": "fish things",
    "apology_thanks": "being sorry", "comfort": "feeling sad", "about": "me", "smart": "being smart",
    "poop": "poop", "doctor": "being sick", "game": "a game", "tricks": "tricks", "banter": "swimming together",
    "decorate": "your decorations", "diet": "food rules", "medicine": "medicine", "hobbies": "fun things",
    "favorites": "favorite things", "wishes": "wishes", "trivia": "things i don't know", "math": "numbers",
    "tasks": "things i can't do", "phone": "phones", "computer": "computers", "money": "money",
    "holidays": "holidays", "days": "days", "ocean": "the ocean", "story": "a story", "riddle": "a riddle",
    "advice": "advice", "compliment": "being nice", "mean": "being mean", "ack": "nothing much", "sky": "the sky", "storm": "the storm", "travel": "trips",
}

Q_FOOD = ("is it edible.", "is that food.", "is that edible.", "does it have food.", "is it a kind of flake.")
Q_WET = ("is it wet.",)
YES = ["yes", "yeah", "yep", "it is", "of course", "sure"]
NO = ["no", "nope", "not really", "it's not", "no it isn't"]
REACT = {"food": (["yes. then i want it.", "oh. then i love it.", "really. i'm excited."],
                  ["no. then i'm not interested.", "oh. that's sad.", "hmm. then i'll just look at it."]),
         "wet": (["good. i like wet things.", "yes. all good things are wet."],
                 ["weird. dry things are scary.", "no. then how does it exist."])}



# ── lines that only make sense coming from a tank mate (each has its own voice) ──

MATE_LINES = {
    "greet": (["hi", "hello", "hey", "hi there", "good morning", "hey you"], {
        "cheerful": ["hi hi hi. so good to see you.", "hello. isn't the water wonderful today.", "hey. want to swim in circles with me."],
        "grumpy": ["what. i was busy.", "hmph. hello. i guess.", "oh. it's you. hi."],
        "shy": ["um. hi.", "...oh. hello. i didn't see you there.", "hi. sorry. hi."],
        "greedy": ["hello. do you have food.", "hi. is it flake time.", "hey. got any snacks."]}),
    "how": (["how are you", "how's it going", "are you ok", "how do you feel", "what's up"], {
        "cheerful": ["i'm great. the bubbles are great. everything is great.", "so good. i did three laps already.", "happy. just happy."],
        "grumpy": ["fine. don't ask.", "tired of everyone in this tank. otherwise fine.", "i'm ok. mostly annoyed."],
        "shy": ["um. ok. i think.", "i'm fine. i was hiding. it's nice there.", "good. quiet is good."],
        "greedy": ["hungry. always hungry.", "i'm ok. my belly says no.", "fine. thinking about food."]}),
    "food": (["want some food", "are you hungry", "dinner time", "here comes food", "snack time"], {
        "cheerful": ["yes yes yes. food is the best.", "food. let's eat together.", "yay. i'll race you to the flakes."],
        "grumpy": ["finally. about time.", "fine. i'll eat. don't crowd me.", "mine. all mine. move."],
        "shy": ["um. yes please. if nobody minds.", "i'll wait until the others are done.", "oh. food. thank you."],
        "greedy": ["food. where. i'm already there.", "yes. all of it. give me all.", "more. always more."]}),
    "bubbles": (["do you like bubbles", "look at the bubbles", "bubbles", "there are so many bubbles"], {
        "cheerful": ["bubbles. i love them. pop pop pop.", "so many bubbles. best day.", "i'm chasing that one. no that one."],
        "grumpy": ["bubbles. they get in my face.", "they're ok. loud though.", "whatever. they pop anyway."],
        "shy": ["um. they're pretty. i watch them.", "i like them from far away.", "they're nice. quietly nice."],
        "greedy": ["bubbles are not food. but ok.", "i tried to eat one. it was nothing.", "bubbles. fine. is there food in them."]}),
    "guppy": (["what do you think of guppy", "do you like guppy", "is guppy nice", "who is guppy", "tell me about guppy"], {
        "cheerful": ["guppy is my best friend. we swim together.", "guppy is so fun. and a little silly.", "guppy is the best. tiny but great."],
        "grumpy": ["guppy talks too much. but ok.", "guppy is fine. keeps stealing my spot.", "guppy. small. loud. fine."],
        "shy": ["um. guppy is nice. guppy says hi to me.", "guppy is kind. i like guppy.", "guppy is brave. i'm not."],
        "greedy": ["guppy has good flakes. i watch.", "guppy shares food sometimes. i like guppy.", "guppy eats too slow. i help."]}),
    "tank": (["do you like the tank", "how's the tank", "is it nice here", "do you like it here", "how do you like your home"], {
        "cheerful": ["i love it here. look at all the water.", "best tank ever. so much room.", "it's perfect. the plants are the best."],
        "grumpy": ["it's too crowded. but ok.", "it's fine. the filter is loud.", "could be bigger. could be quieter."],
        "shy": ["um. it's nice. there are good hiding spots.", "i like the plants. i hide there.", "it's cozy. i like cozy."],
        "greedy": ["it's ok. the food is the best part.", "good tank. food shows up regularly.", "nice. i like where the flakes land."]}),
    "friends": (["do you have friends", "who are your friends", "are you lonely", "do you like the other fish"], {
        "cheerful": ["i have so many friends. all the fish.", "everyone is my friend. even the snail.", "friends. yes. lots."],
        "grumpy": ["friends. a few. don't tell them.", "i tolerate the others.", "i'm not lonely. i'm busy."],
        "shy": ["um. guppy is my friend. only guppy.", "i have one friend. that's enough.", "sometimes i'm lonely. but guppy waves."],
        "greedy": ["my friends are the ones who share food.", "i like the fish who drop flakes.", "friends. yes. the ones with snacks."]}),
    "sleep": (["are you sleepy", "time for bed", "goodnight", "are you tired"], {
        "cheerful": ["a little. goodnight everyone.", "sleepy but happy. sweet dreams.", "night night. see you tomorrow."],
        "grumpy": ["finally. quiet. goodnight.", "yes. leave me alone. goodnight.", "tired. don't bother me."],
        "shy": ["um. goodnight. sleep well.", "yes. i'll hide in the plant. goodnight.", "night. i hope you sleep too."],
        "greedy": ["sleepy. dreaming of flakes.", "goodnight. save me some food for morning.", "i'll nap. wake me for food."]}),
}

# Guppy topics that read fine from any fish (no Guppy-only references)
SAFE_MATE = {"greeting", "feeling", "food", "light", "water", "bubbles", "swimming", "colors", "plants", "bored",
             "curious", "happy", "tired", "sleep", "joke", "singing", "music", "tricks", "banter", "story", "riddle",
             "game", "favorites", "advice", "ack", "compliment", "mean", "apology_thanks", "comfort", "wishes",
             "hobbies", "fish_facts", "taste", "filter", "temp_hot", "temp_cold", "scared", "excited", "dreams",
             "noise", "night", "storm", "weather", "rain", "time", "days"}


def mate_line(persona):
    group = random.choice(list(MATE_LINES))
    users, pools = MATE_LINES[group]
    return random.choice(users), random.choice(pools[persona]), "mate_" + group


def all_topics():
    tops = [v for k, v in vars(gd).items() if k.startswith("gen_") and callable(v) and k != "gen_bye"]
    return tops + list(NEW_TOPICS) + WORLD_TOPICS


# ── dialogues ───────────────────────────────────────────────────────────────

def make_world_dialogue(topics, state=None, max_extra=3):
    """Returns (state, [(user, guppy)...], category). Replies are persona-coloured."""
    s = state or random_state()
    persona = s["you"].get("persona")
    pc = lambda t: persona_say(t, persona)
    turns, cats = [], []
    if persona:                                   # mates only use topics that make sense from any fish
        safe = [g for g in topics if g.__name__[4:] in SAFE_MATE]
        topics = safe or topics
    gen = random.choice(topics)

    def add(u, a, cat):
        turns.append((u, pc(a) if not a.startswith("\0") else a[1:]))
        cats.append(cat)

    r = random.random()
    if r < 0.10:                               # recall of a stated fact
        rt, cat = _recall(topics)
        for u, a in rt:
            add(u, a, cat)
        return s, turns, cats[-1]
    if r < 0.22:                               # questions about the real tank
        u, a, c = state_qa(s)
        add(u, a, c)
        if random.random() < 0.5:
            u, a, c = state_qa(s)
            add(u, a, c)
    elif r < 0.30:
        u, a, c = identity_qa(s)
        add(u, a, c)
    elif persona and r < 0.62:                    # a tank mate speaking in its own voice
        u, a, c = mate_line(persona)
        add(u, "\0" + a, c)
    else:
        smp = gen()
        add(smp["input"], smp["output"], smp["category"])

    for _ in range(random.choice([0, 1, 1, 2, 3])):
        last_u, last_a = turns[-1]
        base = last_a
        mode = random.choices(
            ["follow", "same", "switch", "repeat", "recap", "state", "react", "ack"],
            [0.28, 0.20, 0.14, 0.09, 0.07, 0.10, 0.0, 0.05])[0]
        # a question from Guppy invites a yes/no answer
        for group, phr in (("food", Q_FOOD), ("wet", Q_WET)):
            if any(base.rstrip().endswith(p) for p in phr) and random.random() < 0.7:
                yes = random.random() < 0.5
                add(random.choice(YES if yes else NO), random.choice(REACT[group][0 if yes else 1]), "react")
                mode = None
                break
        if mode is None:
            continue
        if mode == "follow":
            smp = gen()
            add(random.choice(FOLLOWUPS), random.choice(ELABORATE) + smp["output"], smp["category"])
        elif mode in ("same", "switch"):
            if mode == "switch":
                gen = random.choice(topics)
            smp = gen()
            add(smp["input"], smp["output"], smp["category"])
        elif mode == "repeat":
            add(random.choice(["what did you say", "say that again", "repeat that", "what was that", "huh",
                               "i didn't hear you"]), "\0" + random.choice(["i said ", "i said: ", "", "again. "]) + base,
                "repeat")
        elif mode == "recap":
            c = cats[-1]
            ph = "my tank" if c.startswith("state_") else TOPIC_PHRASE.get(c, c.replace("_", " "))
            add(random.choice(["what were we talking about", "what were we just discussing", "what's this about"]),
                f"we were talking about {ph}.", "recap")
        elif mode == "state":
            u, a, c = state_qa(s)
            add(u, a, c)
        else:
            smp = gen_ack()
            add(smp["input"], smp["output"], "ack")
    return s, turns, cats[-1]


def format_world(s, turns):
    body = "\n".join(f"<|im_start|>user\n{u}<|im_end|>\n<|im_start|>assistant\n{a}<|im_end|>" for u, a in turns)
    return format_state(s) + "\n" + body


# ── teaching: /train "fact" -> synthetic lessons ────────────────────────────

def subject_of(fact):
    import re
    m = re.match(r"\s*(?:the |a |an )?([\w'-]+(?: [\w'-]+)?)\s+(?:is|are|means|can|has|have|lives|live|eats|eat|does|do)\b",
                 fact.strip(), re.I)
    if m:
        return m.group(1).lower()
    w = fact.strip().split()
    return (w[0] if w else "it").lower().strip(".,!?")


def fact_samples(fact, n=40, state_fn=random_state):
    """Turn one fact into n fish-voiced training conversations (formatted text)."""
    import re
    fact = fact.strip().strip('"').strip()
    sents = [x.strip() for x in re.split(r"(?<=[.!?])\s+", fact) if x.strip()]
    sents = [x.rstrip(".!?").lower() for x in sents] or [fact.lower()]
    subj = subject_of(fact)
    qs = [f"what is {subj}", f"what are {subj}" if subj.endswith("s") else f"what's {subj}", f"tell me about {subj}",
          f"do you know {subj}", f"do you know what {subj} is", f"explain {subj}", f"{subj}", f"{subj}?",
          f"what do you know about {subj}", f"can you tell me about {subj}"]
    pre = ["", "", "i heard that ", "someone told me ", "oh. ", "i think "]
    suf = ["", " i'm a fish. i only half get it.", " that's all i know.", " is it edible.", " i don't see it in my tank."]
    texts = []
    for _ in range(n):
        s = state_fn(mate_p=0.1) if state_fn is random_state else state_fn()
        turns = []
        mode = random.random()
        if mode < 0.7 or len(sents) < 2:
            a = random.choice(pre) + ". ".join(sents) + "." + random.choice(suf)
            turns.append((random.choice(qs), a))
        else:
            turns.append((random.choice(qs), random.choice(pre) + sents[0] + "." + random.choice(suf)))
            turns.append((random.choice(["tell me more", "and", "what else", "go on", f"what about {subj}"]),
                          ". ".join(sents[1:]) + "."))
        texts.append(format_world(s, turns))
    return texts


# ── dataset writer ──────────────────────────────────────────────────────────

def generate_world_data(n_dialogues=30000, n_single=6000, eval_ratio=0.04, out_dir="data_world"):
    topics = all_topics()
    rows = []
    for _ in range(n_dialogues):
        s, turns, cat = make_world_dialogue(topics)
        rows.append({"text": format_world(s, turns), "category": "w_" + cat})
    for _ in range(n_single):
        s = random_state()
        smp = random.choice(topics)()
        rows.append({"text": format_world(s, [(smp["input"], persona_say(smp["output"], s["you"].get("persona")))]),
                     "category": smp["category"]})
    random.shuffle(rows)
    n_eval = int(len(rows) * eval_ratio)
    os.makedirs(out_dir, exist_ok=True)
    for name, part in (("eval.jsonl", rows[:n_eval]), ("train.jsonl", rows[n_eval:])):
        with open(os.path.join(out_dir, name), "w") as f:
            for r in part:
                f.write(json.dumps(r) + "\n")
    tok = os.path.join("data", "tokenizer.json")
    if os.path.exists(tok):
        with open(tok) as src, open(os.path.join(out_dir, "tokenizer.json"), "w") as dst:
            dst.write(src.read())
    print(f"Wrote {len(rows) - n_eval} train / {n_eval} eval rows to {out_dir}/")


if __name__ == "__main__":
    random.seed(3)
    generate_world_data()
