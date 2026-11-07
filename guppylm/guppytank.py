"""guppytank — a colored ASCII aquarium client for GuppyLM.

Guppy swims around an animated tank (bubbles, swaying plants, castle, gravel,
rippling surface). Type to talk to it; its reply appears in a speech bubble.
Mention food ("want some food?") and flakes rain down; Guppy chases them.

    python -m guppylm tank                 # uses the trained model
    python -m guppylm tank --no-model      # scenery + canned replies (no weights needed)

Keys: type + Enter to talk, Ctrl-F drop food, Esc / Ctrl-C quit.
Commands: /give_flake [n]  /drop_in <thing>  /remove <thing|all>  /tap  /bubbles
          /clean  /light on|off  /say <text>  /help  /quit
"""

import argparse
import threading
import curses
import math
import random
import textwrap
import time

from .brain import Brain, CANNED  # shared brain (state-aware replies, /train)

FOOD_WORDS = ("food", "eat", "hungry", "flake", "feed", "snack", "dinner", "breakfast")

# color pair ids
C_WATER, C_SURF, C_FISH, C_TAIL, C_EYE, C_BUBBLE, C_PLANT, C_PLANT2, \
    C_SAND, C_ROCK, C_CASTLE, C_FOOD, C_SAY, C_UI, C_DIM = range(1, 16)


def init_colors():
    curses.start_color()
    try:
        curses.use_default_colors()
        bg = -1
    except curses.error:
        bg = curses.COLOR_BLACK
    hi = curses.COLORS >= 256

    def pair(i, fg, bgc=bg):
        curses.init_pair(i, fg, bgc)

    pair(C_WATER, 24 if hi else curses.COLOR_BLUE)
    pair(C_SURF, 51 if hi else curses.COLOR_CYAN)
    pair(C_FISH, 208 if hi else curses.COLOR_YELLOW)
    pair(C_TAIL, 196 if hi else curses.COLOR_RED)
    pair(C_EYE, curses.COLOR_WHITE)
    pair(C_BUBBLE, 117 if hi else curses.COLOR_CYAN)
    pair(C_PLANT, 34 if hi else curses.COLOR_GREEN)
    pair(C_PLANT2, 70 if hi else curses.COLOR_GREEN)
    pair(C_SAND, 180 if hi else curses.COLOR_YELLOW)
    pair(C_ROCK, 244 if hi else curses.COLOR_WHITE)
    pair(C_CASTLE, 141 if hi else curses.COLOR_MAGENTA)
    pair(C_FOOD, 178 if hi else curses.COLOR_YELLOW)
    pair(C_SAY, curses.COLOR_BLACK, 255 if hi else curses.COLOR_WHITE)
    pair(C_UI, curses.COLOR_WHITE)
    pair(C_DIM, 240 if hi else curses.COLOR_BLUE)


# Objects for /drop_in: name -> (art lines, color pair). Unknown names become a labelled box.
ITEMS = {
    "rock":     ([" ___ ", "(___)"], C_ROCK),
    "pebble":   (["(_)"], C_ROCK),
    "log":      (["=====@"], C_SAND),
    "shell":    (["<@>"], C_SAND),
    "anchor":   ([" _+_ ", "  |  ", "\\_|_/"], C_ROCK),
    "treasure": (["  ___  ", " /$$$\\ ", "[_____]"], C_FOOD),
    "chest":    (["  ___  ", " /$$$\\ ", "[_____]"], C_FOOD),
    "plant":    (["\\|/", " |/", " |"], C_PLANT),
    "skull":    (["(x_x)"], C_EYE),
    "snail":    (["_@/"], C_FOOD),
    "bridge":   (["  ___  ", " /   \\ ", "/_____\\"], C_ROCK),
    "ball":     (["(O)"], C_TAIL),
    "mirror":   (["|¯|", "|_|"], C_SURF),
    "sub":      ([" __|__ ", "<(o o o)"], C_FOOD),
    "submarine": ([" __|__ ", "<(o o o)"], C_FOOD),
    "thermometer": (["[|]", "[|]", "(_)"], C_TAIL),
}

HELP = [
    "/give_flake [n]   drop n food flakes (default 6)    /feed  same",
    "/drop_in <thing>  drop an object in (rock log shell anchor treasure plant",
    "                  skull snail bridge ball mirror sub ... or anything you name)",
    "/remove <thing>   take an object out (/remove all clears the tank)",
    "/tap              tap the glass     /bubbles  big bubble burst",
    "/light on|off     lights             /clean    water change",
    "/train \"fact\"     teach Guppy a fact   /lessons  /untrain <n|all>",
    "/say <text>       just talk (same as typing)   /forget  reset memory   /help  /quit",
]


class Tank:
    def __init__(self, w, h):
        self.resize(w, h)
        self.t = 0
        self.fx, self.fy = w / 2, h / 2
        self.vx = 0.0
        self.dir = 1
        self.tx, self.ty = self.fx, self.fy
        self.bubbles = []   # [x, y, speed]
        self.flakes = []    # [x, y, rest_ticks]
        self.say = ""
        self.say_until = 0
        self.eaten = 0
        self.mood = ""
        self.items = []      # dicts: name, x, y, art, col
        self.dark = False
        self.dash = 0        # frames of startled darting after a tap
        self.flash = 0

    def resize(self, w, h):
        self.w, self.h = w, h
        self.floor = h - 3            # first gravel row
        rnd = random.Random(7)
        self.plants = [(rnd.randrange(2, max(3, w - 3)), rnd.randrange(3, 7),
                        rnd.choice((C_PLANT, C_PLANT2))) for _ in range(max(3, w // 14))]
        self.rocks = [(rnd.randrange(2, max(3, w - 8)), rnd.randrange(3, 7)) for _ in range(max(2, w // 22))]
        self.castle_x = max(4, w // 3)
        self.sand = [rnd.choice(".:,'`") for _ in range(w * 3)]

    # ── simulation ──────────────────────────────────────────────────────
    def drop_food(self, n=None):
        for _ in range(n or random.randint(5, 9)):
            self.flakes.append([random.uniform(3, self.w - 4), 1.0 + random.random() * 2, 0])

    def drop_in(self, name):
        art, col = ITEMS.get(name, (None, None))
        if art is None:
            label = "[" + name[:14] + "]"
            art, col = [label], C_CASTLE
        x = random.randint(2, max(3, self.w - max(len(l) for l in art) - 3))
        self.items.append({"name": name, "x": x, "y": 1.0, "art": art, "col": col})
        self.tx, self.ty = x + 2, self.floor - 3   # fish goes to look
        return len(self.items)

    def remove(self, name):
        n = len(self.items)
        self.items = [] if name == "all" else [i for i in self.items if i["name"] != name]
        return n - len(self.items)

    def tap(self):
        self.dash = 25
        self.flash = 4
        self.tx = random.choice([3, self.w - 8])
        self.ty = random.uniform(3, self.floor - 3)

    def burst_bubbles(self, n=25):
        for _ in range(n):
            self.bubbles.append([self.fx + random.uniform(-4, 4), self.floor - 1 - random.random() * 2,
                                 random.uniform(0.2, 0.6)])

    def clean(self):
        self.flakes.clear()
        self.burst_bubbles(40)
        self.flash = 6

    def speak(self, text, secs=7):
        self.say, self.say_until = text, time.time() + secs

    def step(self):
        self.t += 1
        # bubbles
        if random.random() < 0.12:
            self.bubbles.append([random.choice([self.fx, random.uniform(2, self.w - 3)]),
                                 self.floor - 1, random.uniform(0.15, 0.4)])
        for b in self.bubbles:
            b[1] -= b[2]
            b[0] += math.sin(self.t / 4 + b[1]) * 0.15
        self.bubbles = [b for b in self.bubbles if b[1] > 1]
        for it in self.items:
            rest = self.floor - len(it["art"])
            if it["y"] < rest:
                it["y"] = min(rest, it["y"] + 0.35)
        self.flash = max(0, self.flash - 1)
        # flakes fall, settle, vanish
        for f in self.flakes:
            if f[1] < self.floor - 1:
                f[1] += 0.2
                f[0] += math.sin(self.t / 5 + f[0]) * 0.1
            else:
                f[2] += 1
        self.flakes = [f for f in self.flakes if f[2] < 150]
        # fish brain: chase nearest food, else wander
        falling = [f for f in self.flakes if f[2] == 0] or self.flakes
        if falling:
            f = min(falling, key=lambda f: abs(f[0] - self.fx) + abs(f[1] - self.fy))
            self.tx, self.ty = f[0], f[1]
            if abs(f[0] - self.fx) < 3 and abs(f[1] - self.fy) < 1.5:
                self.flakes.remove(f)
                self.eaten += 1
        elif abs(self.tx - self.fx) < 2 and abs(self.ty - self.fy) < 1 or random.random() < 0.01:
            self.tx = random.uniform(5, self.w - 6)
            self.ty = random.uniform(3, self.floor - 3)
        speed = 0.9 if falling else 0.35
        if self.dash > 0:
            self.dash -= 1
            speed = 1.6
            if self.dash % 8 == 0:
                self.tx = random.uniform(4, self.w - 8)
                self.ty = random.uniform(3, self.floor - 3)
        dx, dy = self.tx - self.fx, self.ty - self.fy
        self.vx = max(-speed, min(speed, dx * 0.1))
        self.fx += self.vx
        self.fy += max(-0.25, min(0.25, dy * 0.1))
        if abs(self.vx) > 0.08:
            self.dir = 1 if self.vx > 0 else -1
        self.fx = max(5, min(self.w - 8, self.fx))
        self.fy = max(2, min(self.floor - 2, self.fy))

    # ── drawing ─────────────────────────────────────────────────────────
    def put(self, scr, y, x, s, attr):
        if 0 <= y < self.h and 0 <= x < self.w - 1:
            s = s[: max(0, self.w - 1 - x)]
            if self.dark:
                attr |= curses.A_DIM
            try:
                scr.addstr(y, x, s, attr)
            except curses.error:
                pass

    def draw(self, scr):
        w, h, t = self.w, self.h, self.t
        P = curses.color_pair
        # water texture
        for y in range(1, self.floor) if not self.dark else ():
            row = "".join("~" if (x + y * 3 + t // 6) % 17 == 0 else " " for x in range(w - 1))
            self.put(scr, y, 0, row, P(C_DIM))
        # surface
        self.put(scr, 0, 0, "".join("~≈"[(x + t // 3) % 2] if (x // 2 + t // 4) % 3 else "-"
                                    for x in range(w - 1)), P(C_SURF) | curses.A_BOLD)
        # gravel
        for r in range(3):
            self.put(scr, self.floor + r, 0,
                     "".join(self.sand[(r * w + x) % len(self.sand)] for x in range(w - 1)),
                     P(C_SAND))
        # rocks
        for x, sz in self.rocks:
            self.put(scr, self.floor - 1, x, "(" + "_" * (sz - 2) + ")", P(C_ROCK))
            self.put(scr, self.floor - 2, x + 1, "_" * max(1, sz - 2), P(C_ROCK))
        # castle
        cx, fl = self.castle_x, self.floor
        art = ["  _|~|_   _|~|_", " | [] |___| [] |", " |  _ ___ _  |", " |_| |_ _| |_|"]
        for i, line in enumerate(art):
            self.put(scr, fl - len(art) + i, cx, line, P(C_CASTLE))
        # plants (sway)
        for x, ht, col in self.plants:
            for i in range(ht):
                sway = round(math.sin(t / 8 + x + i * 0.6) * 1.2)
                self.put(scr, fl - 1 - i, x + sway, ")" if (i + t // 10) % 2 else "(", P(col) | curses.A_BOLD)
        # dropped objects
        for it in self.items:
            for i, line in enumerate(it["art"]):
                self.put(scr, int(it["y"]) + i, it["x"], line, P(it["col"]) | curses.A_BOLD)
        # bubbles
        for bx, by, _ in self.bubbles:
            ch = "o" if by > self.floor / 2 else "O"
            self.put(scr, int(by), int(bx), ch, P(C_BUBBLE))
        # flakes
        for fx, fy, rest in self.flakes:
            self.put(scr, int(fy), int(fx), "*" if rest == 0 else ".", P(C_FOOD) | curses.A_BOLD)
        if self.flash:
            self.put(scr, 1, 0, ("!" if self.dash else "~") * (w - 1), P(C_SURF) | curses.A_BOLD)
        self.draw_fish(scr)
        self.draw_speech(scr)

    def draw_fish(self, scr):
        P, x, y = curses.color_pair, int(self.fx), int(self.fy)
        wag = (self.t // 3) % 2
        if self.dir > 0:
            tail = "><" if wag else ">-"
            self.put(scr, y, x, tail, P(C_TAIL) | curses.A_BOLD)
            self.put(scr, y, x + 2, "(((", P(C_FISH) | curses.A_BOLD)
            self.put(scr, y, x + 5, "°", P(C_EYE) | curses.A_BOLD)
            self.put(scr, y, x + 6, ">", P(C_FISH) | curses.A_BOLD)
            self.put(scr, y - 1, x + 3, "_", P(C_TAIL))
        else:
            self.put(scr, y, x, "<", P(C_FISH) | curses.A_BOLD)
            self.put(scr, y, x + 1, "°", P(C_EYE) | curses.A_BOLD)
            self.put(scr, y, x + 2, ")))", P(C_FISH) | curses.A_BOLD)
            tail = "><" if wag else "-<"
            self.put(scr, y, x + 5, tail, P(C_TAIL) | curses.A_BOLD)
            self.put(scr, y - 1, x + 3, "_", P(C_TAIL))

    def draw_speech(self, scr):
        if not self.say or time.time() > self.say_until:
            return
        lines = textwrap.wrap(self.say, min(34, max(12, self.w // 2)))
        bw = max(len(l) for l in lines) + 2
        x = max(1, min(int(self.fx) - bw // 2, self.w - bw - 2))
        top = int(self.fy) - len(lines) - 2
        if top < 1:  # no room above: put it below the fish
            top = int(self.fy) + 2
        top = max(1, min(top, self.floor - len(lines) - 1))
        a = curses.color_pair(C_SAY)
        for i, l in enumerate(lines):
            self.put(scr, top + i, x, " " + l.ljust(bw - 2) + " ", a)
        # tail of bubble
        ty = top + len(lines)
        if ty < int(self.fy):
            self.put(scr, ty, min(x + bw // 2, self.w - 3), "o", curses.color_pair(C_BUBBLE))


def handle_command(tank, line):
    """Run a /command. Returns (note, prompt): a status note for the user and an
    optional message to send to Guppy so it can react (None = no reaction)."""
    cmd, _, arg = line[1:].partition(" ")
    cmd, arg = cmd.lower(), arg.strip()
    if cmd in ("give_flake", "give_flakes", "flake", "flakes", "feed"):
        n = int(arg) if arg.isdigit() else None
        tank.drop_food(max(1, min(n, 40)) if n else None)
        return "flakes dropped", "want some food?"
    if cmd in ("drop_in", "drop", "add", "put"):
        if not arg:
            return "usage: /drop_in <object>   e.g. /drop_in rock", None
        name = arg.lower()
        tank.drop_in(name)
        return f"dropped a {name} in", f"i put a new {name} in the tank"
    if cmd in ("remove", "take_out", "rm"):
        if not arg:
            return "usage: /remove <object|all>", None
        n = tank.remove(arg.lower())
        return (f"removed {n} {arg}", f"i took the {arg} out") if n else (f"no {arg} in the tank", None)
    if cmd == "tap":
        tank.tap()
        return "tap tap", "tap tap tap"
    if cmd == "bubbles":
        tank.burst_bubbles()
        return "bubbles!", "look at those bubbles"
    if cmd == "clean":
        tank.clean()
        return "fresh water", "time for a water change"
    if cmd == "light":
        if arg not in ("on", "off"):
            return "usage: /light on|off", None
        tank.dark = arg == "off"
        return f"light {arg}", "the light is off now" if tank.dark else "the light is on now"
    if cmd == "say":
        return "", arg or None
    if cmd in ("forget", "reset"):
        return "forget", "FORGET"
    if cmd == "help":
        return "see help in the tank", "HELP"
    return f"unknown command /{cmd} (try /help)", None


def tank_state(tank, last_ate):
    """What Guppy knows about its tank, for the model's state line."""
    names = [i["name"] for i in tank.items][-3:]
    ate = time.time() - last_ate
    mood = ("scared" if tank.dash > 0 else "sleepy" if tank.dark else "full" if ate < 40
            else "hungry" if ate > 120 else "happy")
    doing = "eating" if tank.flakes else "hiding" if tank.dash > 0 else "swimming"
    return {"you": {"name": "guppy", "species": None, "persona": None}, "items": names, "mates": [],
            "mood": mood, "doing": doing}


def run(stdscr, brain):
    curses.curs_set(0)
    init_colors()
    stdscr.timeout(80)
    stdscr.keypad(True)
    h, w = stdscr.getmaxyx()
    tank = Tank(w, h - 3)
    buf, last, help_until = "", "", 0.0
    last_ate, prev_eaten = time.time(), 0
    job = {"t": None, "out": None}
    tank.speak("hello. i am guppy.", 5)
    while True:
        if tank.eaten != prev_eaten:
            prev_eaten, last_ate = tank.eaten, time.time()
        if job["out"] is not None:
            last = job["out"]; tank.speak(last, 12); job["out"] = None
        H, W = stdscr.getmaxyx()
        if (W, H - 3) != (tank.w, tank.h):
            tank.resize(W, max(10, H - 3))
        stdscr.erase()
        tank.step()
        tank.draw(stdscr)
        ui = curses.color_pair(C_UI)
        status = f" guppytank · eaten: {tank.eaten} · /help · Ctrl-F food · Esc quit "
        try:
            stdscr.addstr(H - 3, 0, ("─" * (W - 1)), curses.color_pair(C_DIM))
            stdscr.addstr(H - 2, 0, ("guppy> " + last)[: W - 1], curses.color_pair(C_FISH) | curses.A_BOLD)
            stdscr.addstr(H - 1, 0, ("you> " + buf + "▏")[: W - 1], ui)
            stdscr.addstr(H - 3, 2, status[: W - 4], ui | curses.A_DIM)
        except curses.error:
            pass
        if time.time() < help_until:
            for i, line in enumerate(HELP):
                tank.put(stdscr, 2 + i, 2, " " + line.ljust(76) + " ", curses.color_pair(C_SAY))
        stdscr.refresh()

        k = stdscr.getch()
        if k == -1:
            continue
        if k in (27, 3):          # Esc, Ctrl-C
            return
        if k == 6:                # Ctrl-F
            tank.drop_food()
        elif k in (10, 13, curses.KEY_ENTER):
            msg = buf.strip()
            buf = ""
            if msg.lower() in ("quit", "exit", "q", "/quit", "/exit"):
                return
            if msg:
                prompt, note = msg, ""
                head, _, rest = msg.partition(" ")
                if head.lower() in ("/train", "/untrain", "/lessons"):
                    if head.lower() == "/lessons":
                        last = " | ".join(f"{i+1}. {f}" for i, f in enumerate(brain.lessons)) or "no lessons yet. try /train \"...\""
                    elif brain.learning:
                        last = "still learning the last thing..."
                    else:
                        def work(h=head.lower(), r=rest.strip()):
                            if h == "/train":
                                ans = brain.train(r)
                                job["out"] = "learned. " + ans
                            else:
                                job["out"] = "forgot lessons" if brain.untrain(r or "all") else "usage: /untrain <n|all>"
                        last = "learning... (about a minute)" if head.lower() == "/train" else "unlearning..."
                        tank.speak(last, 60)
                        threading.Thread(target=work, daemon=True).start()
                    prompt = None
                elif msg.startswith("/"):
                    note, prompt = handle_command(tank, msg)
                    if prompt == "FORGET":
                        brain.forget()
                        prompt = "FORGOT"
                    if prompt == "HELP":
                        help_until = time.time() + 20
                        prompt = None
                    last = note
                elif any(wd in msg.lower() for wd in FOOD_WORDS):
                    tank.drop_food()
                if prompt == "FORGOT":
                    tank.speak("what were we talking about. i forgot.")
                    prompt = None
                if prompt:
                    tank.speak("...", 30)
                    tank.draw(stdscr); stdscr.refresh()
                    last = brain.reply(prompt, state=tank_state(tank, last_ate))
                    tank.speak(last)
        elif k in (curses.KEY_BACKSPACE, 127, 8):
            buf = buf[:-1]
        elif 32 <= k < 127 and len(buf) < 120:
            buf += chr(k)


def main(argv=None):
    p = argparse.ArgumentParser(description="guppytank — colored ASCII aquarium for GuppyLM")
    p.add_argument("--checkpoint", default="checkpoints/best_model.pt")
    p.add_argument("--tokenizer", default="data/tokenizer.json")
    p.add_argument("--device", default="cpu")
    p.add_argument("--no-model", action="store_true", help="scenery + canned replies, no trained weights needed")
    args = p.parse_args(argv)
    brain = Brain(args)
    try:
        curses.wrapper(run, brain)
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    main()
