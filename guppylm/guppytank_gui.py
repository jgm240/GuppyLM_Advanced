"""guppytank GUI — a smooth, antialiased aquarium for GuppyLM (macOS / any Qt platform).

    python -m guppylm tank-gui              # trained model
    python -m guppylm tank-gui --no-model   # scenery + canned replies

Click the water to drop food. Double-click to tap the glass. Type to chat, or use:
  /give_flake [n]  /drop_in <thing>  /remove <thing|all>  /tap  /bubbles
  /clean  /light on|off  /forget  /help
"""

import argparse
import math
import random
import sys
import textwrap
import time

from PySide6.QtCore import QObject, QPointF, QRectF, QRunnable, QThreadPool, QTimer, Qt, Signal
from PySide6.QtGui import (QBrush, QColor, QFont, QLinearGradient, QPainter, QPainterPath, QPen,
                           QPolygonF, QRadialGradient)
from PySide6.QtWidgets import QApplication, QLabel, QLineEdit, QVBoxLayout, QWidget

from .brain import Brain
from .world import MATE_NAMES, PERSONAS, SPECIES, TRICKS

FOOD_WORDS = ("food", "eat", "hungry", "flake", "feed", "snack", "dinner", "breakfast")

HELP = ("/give_flake [n]  drop food          /drop_in <thing>  add an object\n"
        "/remove <thing|all>  take out        /tap  tap the glass     /bubbles\n"
        "/clean  water change                 /light on|off           /forget\n"
        "/add_fish [species] [name]  new tank mate   /remove_fish <name>\n"
        "@name hello  or  /talk name hello   chat with a tank mate (click a fish too)\n"
        "/trick [spin|loop|jump|dance]        /event [snail|storm|blackout|shadow|feeder|current]\n"
        '/train "fact"  teach Guppy something   /lessons   /untrain <n|all>\n'
        "Click water: drop food · double-click: tap glass")
KNOWN = ["rock", "pebble", "log", "shell", "castle", "chest", "treasure", "anchor", "plant",
         "skull", "snail", "ball", "bridge", "mirror"]


def C(r, g, b, a=255):
    return QColor(r, g, b, a)


def say_any(*options):
    return random.choice(options)


class Replier(QObject):
    done = Signal(str, str)          # fish name, text
    taught = Signal(str)             # result text
    progress = Signal(int, int)
    banter = Signal(list)            # [(fish name, text), ...]


class ReplyJob(QRunnable):
    def __init__(self, brain, prompt, state, fish, sig):
        super().__init__()
        self.brain, self.prompt, self.state, self.fish, self.sig = brain, prompt, state, fish, sig

    def run(self):
        try:
            text = self.brain.reply(self.prompt, state=self.state, fish=self.fish)
        except Exception as e:  # keep the UI alive
            text = f"(my brain slipped: {e})"
        self.sig.done.emit(self.fish, text)


class TeachJob(QRunnable):
    def __init__(self, brain, fact, sig, untrain=None):
        super().__init__()
        self.brain, self.fact, self.sig, self.untrain = brain, fact, sig, untrain

    def run(self):
        try:
            cb = lambda a, b: self.sig.progress.emit(a, b)
            if self.untrain is not None:
                ok = self.brain.untrain(self.untrain, cb)
                self.sig.taught.emit("lessons forgotten" if ok else "usage: /untrain <n|all>")
            else:
                ans = self.brain.train(self.fact, cb)
                self.sig.taught.emit("learned! " + ans)
        except Exception as e:
            self.sig.taught.emit(f"(training failed: {e})")


class BanterJob(QRunnable):
    """Two fish chat: A opens, B answers (via the model), A answers B."""
    def __init__(self, brain, a, b, opener, st_a, st_b, sig):
        super().__init__()
        self.brain, self.a, self.b, self.opener, self.st_a, self.st_b, self.sig = brain, a, b, opener, st_a, st_b, sig

    def run(self):
        try:
            r1 = self.brain.reply(self.opener, state=self.st_b, fish=self.b, remember=False)
            r2 = self.brain.reply(r1, state=self.st_a, fish=self.a, remember=False)
            self.sig.banter.emit([(self.a, self.opener), (self.b, r1), (self.a, r2)])
        except Exception:
            pass


OPENERS = ["hi. want to swim together", "i found a nice spot", "did you see the food", "this is my rock",
           "i'm bored. race me", "look at the bubbles", "are you hungry", "i love this tank",
           "the big shape is looking at us", "want to hide behind the plant"]

PALETTES = {
    "guppy": dict(top=(255, 120, 30), mid=(255, 165, 50), bot=(255, 225, 150), tail0=(255, 110, 40), tail1=(240, 50, 70),
                  fin=(240, 70, 60), size=1.0, tail_len=1.0, body_h=1.0, stripe=None),
    "neon tetra": dict(top=(30, 70, 190), mid=(70, 140, 230), bot=(235, 245, 255), tail0=(200, 70, 70), tail1=(230, 100, 100),
                       fin=(220, 80, 80), size=0.55, tail_len=0.8, body_h=0.8, stripe=(80, 255, 240)),
    "betta": dict(top=(120, 20, 160), mid=(190, 50, 190), bot=(250, 150, 230), tail0=(200, 40, 160), tail1=(90, 20, 180),
                  fin=(210, 60, 170), size=0.9, tail_len=1.7, body_h=0.85, stripe=None),
    "goldfish": dict(top=(255, 140, 10), mid=(255, 190, 60), bot=(255, 245, 220), tail0=(255, 150, 40), tail1=(255, 80, 40),
                     fin=(255, 120, 40), size=1.15, tail_len=1.2, body_h=1.25, stripe=None),
}
SPEED = {"cheerful": 2.4, "grumpy": 1.8, "shy": 1.6, "greedy": 2.6}


class Fish:
    def __init__(self, name, species="guppy", persona=None, x=300, y=200):
        self.name, self.species, self.persona = name, species, persona
        self.pal = PALETTES.get(species, PALETTES["guppy"])
        self.x, self.y, self.vx, self.vy, self.dirn = float(x), float(y), 0.0, 0.0, 1
        self.tx, self.ty = self.x, self.y
        self.say, self.say_until = "", 0.0
        self.last_ate = time.time()
        self.rot, self.roll = 0.0, 0.0
        self.trick = None
        self.dash = 0
        self.eating = False
        self.speed = SPEED.get(persona, 2.2)


class TankView(QWidget):
    def __init__(self):
        super().__init__()
        self.setMinimumSize(700, 440)
        self.setMouseTracking(True)
        self.t = 0.0
        self.guppy = Fish("guppy", "guppy", None, 300, 200)
        self.fish = [self.guppy]
        self.bubbles, self.flakes, self.items, self.particles = [], [], [], []
        self.eaten, self.dark = 0, False
        self.flash = 0.0
        self.help_until = 0.0
        self.progress = None          # (step, total) while learning
        self.lessons = 0
        self.ev = None             # {"name","t0","dur", ...}
        self.snail = None
        self.current_until = 0.0
        self.on_fish_click = None
        rnd = random.Random(11)
        self.plants = [(rnd.random(), rnd.uniform(0.18, 0.34), rnd.choice([(46, 160, 90), (70, 190, 110), (30, 130, 100)]),
                        rnd.uniform(0, 6)) for _ in range(9)]
        self.pebbles = [(rnd.random(), rnd.random(), rnd.uniform(2, 6), rnd.choice([(200, 170, 120), (170, 140, 100), (225, 200, 150), (150, 130, 110)]))
                        for _ in range(140)]
        self.rocks = [(rnd.uniform(0.08, 0.9), rnd.uniform(34, 70)) for _ in range(4)]
        self.timer = QTimer(self, timeout=self.tick)
        self.timer.start(16)

    # ── geometry ───────────────────────────────────────────────────────
    @property
    def floor(self):
        return self.height() * 0.84

    def unit(self):
        return max(0.6, min(self.width() / 960, self.height() / 600))

    def mates(self):
        return [f for f in self.fish if f is not self.guppy]

    def find(self, name):
        name = name.lower()
        return next((f for f in self.fish if f.name == name), None)

    # ── actions ────────────────────────────────────────────────────────
    def drop_food(self, n=None, x=None):
        for _ in range(n or random.randint(6, 10)):
            cx = x if x is not None else random.uniform(40, self.width() - 40)
            self.flakes.append([cx + random.uniform(-25, 25), random.uniform(10, 50), 0.0, random.uniform(0, 6)])

    def drop_in(self, name):
        self.items.append({"name": name, "x": random.uniform(80, max(100, self.width() - 140)),
                           "y": 20.0, "ph": random.uniform(0, 6)})
        self.guppy.tx, self.guppy.ty = self.items[-1]["x"], self.floor - 120

    def remove(self, name):
        n = len(self.items)
        self.items = [] if name == "all" else [i for i in self.items if i["name"] != name]
        return n - len(self.items)

    def add_fish(self, species=None, name=None):
        species = species if species in PALETTES else random.choice([s for s in SPECIES if s != "guppy"])
        taken = {f.name for f in self.fish}
        name = (name or random.choice([n for n in MATE_NAMES if n not in taken] or ["fishy"])).lower()
        f = Fish(name, species, random.choice(PERSONAS), random.uniform(80, self.width() - 80), random.uniform(60, self.floor - 100))
        self.fish.append(f)
        self.burst(10)
        return f

    def remove_fish(self, name):
        f = self.find(name)
        if f and f is not self.guppy:
            self.fish.remove(f)
            return True
        return False

    def tap(self):
        self.flash = 1.0
        for f in self.fish:
            f.dash = 70

    def burst(self, n=40):
        for _ in range(n):
            self.bubbles.append([self.guppy.x + random.uniform(-60, 60), self.floor - random.uniform(0, 30),
                                 random.uniform(1.0, 2.6), random.uniform(3, 11), random.uniform(0, 6)])

    def clean(self):
        self.flakes.clear()
        self.burst(70)
        self.flash = 1.0

    def speak(self, text, secs=7, fish=None):
        f = fish or self.guppy
        f.say, f.say_until = text, time.time() + secs

    # ── tricks ─────────────────────────────────────────────────────────
    def start_trick(self, name):
        g = self.guppy
        dur = {"spin": 1.4, "loop": 2.2, "jump": 1.6, "dance": 3.0}[name]
        g.trick = {"name": name, "t0": self.t, "dur": dur, "x0": g.x, "y0": min(g.y, self.floor - 170),
                   "dir": g.dirn, "last": (g.x, g.y)}
        g.tx, g.ty = g.x, g.y

    def run_trick(self, g):
        tr = g.trick
        u = (self.t - tr["t0"]) / tr["dur"]
        if u >= 1:
            g.trick, g.rot, g.roll = None, 0.0, 0.0
            g.tx, g.ty = g.x, g.y
            return
        x0, y0, d, name = tr["x0"], tr["y0"], tr["dir"], tr["name"]
        g.dirn = d
        if name == "spin":
            g.x, g.y = x0, y0
            g.roll = u * 2 * math.pi
            g.rot = 0
            if random.random() < 0.3:
                self.bubbles.append([g.x, g.y, 1.5, random.uniform(3, 6), random.uniform(0, 6)])
        elif name == "loop":
            R = 85 * self.unit()
            th = u * 2 * math.pi
            g.x, g.y = x0 + d * R * math.sin(th), y0 + R - R * math.cos(th) - R * 0.0
            vx, vy = d * math.cos(th), math.sin(th) * -1 + 0
            heading = math.degrees(math.atan2(-math.sin(th), d * math.cos(th)))
            g.rot = heading if d > 0 else heading - 180
            g.roll = 0
        elif name == "jump":
            top = 26
            g.x = x0 + d * u * 140 * self.unit()
            g.y = y0 + (top - y0) * (4 * u * (1 - u))
            dy = (top - y0) * (4 - 8 * u)
            heading = math.degrees(math.atan2(dy, d * 140 * self.unit() / (tr["dur"] * 60)))
            g.rot = max(-50, min(50, heading * 0.5 if d > 0 else -heading * 0.5))
            if 0.45 < u < 0.55 and random.random() < 0.6:
                for _ in range(2):
                    self.bubbles.append([g.x + random.uniform(-30, 30), 24, random.uniform(0.5, 1.5), random.uniform(3, 6), 0])
        else:  # dance
            g.x = x0 + math.sin(u * 14) * 28 * self.unit()
            g.y = y0 + math.sin(u * 28) * 8
            g.rot = math.sin(u * 14) * 14
            g.roll = 0
            if random.random() < 0.08:
                self.particles.append([g.x, g.y - 40, random.choice(["♪", "♫", "♥"]), self.t])
        g.x = max(70, min(self.width() - 70, g.x))

    # ── events ─────────────────────────────────────────────────────────
    EVENTS = {
        "snail": ["there's a snail in the tank", "would you like a snail friend", "do you know what a snail is"],
        "storm": ["there's a thunderstorm", "the lightning is scary", "it's so windy outside"],
        "blackout": ["the power went out", "the light flickers"],
        "shadow": ["the cat is looking at you", "there's a cat by the tank", "do you see the cat"],
        "feeder": ["want some food?", "dinner time", "the feeder is open"],
        "current": ["the filter is making bubbles", "the filter is loud", "there are so many bubbles"],
    }

    def start_event(self, name):
        dur = {"snail": 28, "storm": 7, "blackout": 6, "shadow": 8, "feeder": 3, "current": 9}[name]
        self.ev = {"name": name, "t0": self.t, "dur": dur}
        if name == "snail":
            d = random.choice([-1, 1])
            self.snail = {"x": -30.0 if d > 0 else self.width() + 30.0, "dir": d}
        elif name == "feeder":
            self.drop_food(14)
        elif name == "current":
            self.current_until = self.t + dur
        elif name == "storm":
            self.tap()
        elif name == "blackout":
            self.dark_prev = self.dark
        return random.choice(self.EVENTS[name])

    def tick_event(self):
        ev = self.ev
        if not ev:
            return
        u = (self.t - ev["t0"]) / ev["dur"]
        n = ev["name"]
        if n == "storm" and random.random() < 0.03:
            self.flash = 1.0
        if n == "blackout":
            self.dark = (u < 0.9) and (u > 0.15 or int(self.t * 12) % 2 == 0)
            if u >= 0.9:
                self.dark = self.dark_prev
        if u >= 1:
            if n == "blackout":
                self.dark = self.dark_prev
            self.ev = None

    def mood_of(self, f):
        ev = self.ev["name"] if self.ev else ""
        if f.dash > 0 or ev in ("storm", "shadow"):
            return "scared"
        if self.dark:
            return "sleepy"
        ate = time.time() - f.last_ate
        return "full" if ate < 40 else "hungry" if ate > 120 else "happy"

    def state_of(self, f):
        names = [i["name"] for i in self.items][-3:]
        others = [x.name for x in self.fish if x is not f]
        doing = "spinning" if f.trick else "eating" if f.eating else "hiding" if f.dash > 0 else "swimming"
        return {"you": {"name": f.name, "species": None if f is self.guppy else f.species,
                        "persona": None if f is self.guppy else f.persona},
                "items": names, "mates": others, "mood": self.mood_of(f), "doing": doing}

    # ── simulation ─────────────────────────────────────────────────────
    def tick(self):
        self.t += 0.016
        w, fl, u = self.width(), self.floor, self.unit()
        side = 1.6 if self.t < self.current_until else 0.0
        if random.random() < 0.05:
            self.bubbles.append([random.choice([self.guppy.x, random.uniform(20, w - 20)]), fl - 5,
                                 random.uniform(0.6, 1.6), random.uniform(2, 7), random.uniform(0, 6)])
        for b in self.bubbles:
            b[1] -= b[2]
            b[0] += math.sin(self.t * 2 + b[4]) * 0.3 + side
        self.bubbles = [b for b in self.bubbles if b[1] > 14 and b[0] < w + 20]
        for f in self.flakes:
            if f[1] < fl - 6:
                f[1] += 0.9
                f[0] += math.sin(self.t * 2 + f[3]) * 0.35 + side * 0.3
            else:
                f[2] += 0.016
        self.flakes = [f for f in self.flakes if f[2] < 3]
        for it in self.items:
            rest = fl + 4
            if it["y"] < rest:
                it["y"] = min(rest, it["y"] + 2.6)
                it["x"] += math.sin(self.t * 3 + it["ph"]) * 0.5
        self.particles = [p for p in self.particles if self.t - p[3] < 1.6]
        if self.snail:
            self.snail["x"] += 0.55 * self.snail["dir"]
            if not -60 < self.snail["x"] < w + 60:
                self.snail = None
        self.flash = max(0.0, self.flash - 0.02)
        self.tick_event()
        for f in self.fish:
            self.step_fish(f, w, fl, u)
        self.update()

    def step_fish(self, f, w, fl, u):
        if f is self.guppy and f.trick:
            self.run_trick(f)
            return
        live = [x for x in self.flakes if x[2] == 0] or self.flakes
        speed = f.speed
        f.eating = False
        if live:
            x = min(live, key=lambda x: abs(x[0] - f.x) + abs(x[1] - f.y))
            f.tx, f.ty = x[0], x[1]
            speed = f.speed * (2.2 if f.persona == "greedy" else 2.0)
            f.eating = True
            if math.hypot(x[0] - f.x - 45 * u * f.dirn * f.pal["size"], x[1] - f.y) < 30 * u:
                self.flakes.remove(x)
                f.last_ate = time.time()
                if f is self.guppy:
                    self.eaten += 1
        elif f.dash > 0:
            speed = f.speed * 3
            if int(f.dash) % 22 == 0:
                f.tx, f.ty = random.uniform(80, w - 80), random.uniform(60, fl - 100)
        elif math.hypot(f.tx - f.x, f.ty - f.y) < 25 or random.random() < 0.004:
            f.tx, f.ty = random.uniform(80, w - 80), random.uniform(70, fl - 110)
            if f.persona == "cheerful" and f is not self.guppy:     # cheerful fish tag along with Guppy
                f.tx, f.ty = self.guppy.x + random.uniform(-160, 160), self.guppy.y + random.uniform(-70, 70)
        f.dash = max(0, f.dash - 1)
        for o in self.fish:                    # personal space
            if o is not f and math.hypot(o.x - f.x, o.y - f.y) < 70 * u:
                f.vx -= (o.x - f.x) * 0.002
                f.vy -= (o.y - f.y) * 0.002
        dx, dy = f.tx - f.x, f.ty - f.y
        d = math.hypot(dx, dy) or 1
        want_vx, want_vy = dx / d * speed * min(1, d / 80), dy / d * speed * min(1, d / 80)
        f.vx += (want_vx - f.vx) * 0.05
        f.vy += (want_vy - f.vy) * 0.05
        f.x += f.vx
        f.y += f.vy + math.sin(self.t * 2.2 + len(f.name)) * 0.15
        f.x = max(70, min(w - 70, f.x))
        f.y = max(40, min(fl - 40, f.y))
        if abs(f.vx) > 0.35:
            f.dirn = 1 if f.vx > 0 else -1
        f.rot = max(-18, min(18, f.vy * 3))

    # ── input ──────────────────────────────────────────────────────────
    def mousePressEvent(self, e):
        x, y = e.position().x(), e.position().y()
        for f in reversed(self.fish):
            if math.hypot(x - f.x, y - f.y) < 70 * self.unit() * f.pal["size"]:
                if self.on_fish_click:
                    self.on_fish_click(f)
                return
        if y < self.floor:
            self.drop_food(5, x)

    def mouseDoubleClickEvent(self, e):
        self.tap()

    # ── painting ───────────────────────────────────────────────────────
    def paintEvent(self, _):
        p = QPainter(self)
        p.setRenderHints(QPainter.Antialiasing | QPainter.TextAntialiasing | QPainter.SmoothPixmapTransform)
        w, h, fl, t, u = self.width(), self.height(), self.floor, self.t, self.unit()
        if self.ev and self.ev["name"] == "storm" and self.flash > 0.5:
            p.translate(random.uniform(-4, 4), random.uniform(-3, 3))
        g = QLinearGradient(0, 0, 0, h)
        g.setColorAt(0, C(70, 190, 230)); g.setColorAt(0.45, C(25, 110, 175)); g.setColorAt(1, C(8, 40, 90))
        p.fillRect(self.rect(), g)
        p.setPen(Qt.NoPen)
        for i in range(6):
            x0 = (i + 0.5) / 6 * w + math.sin(t * 0.4 + i) * 40
            sway = math.sin(t * 0.3 + i * 1.7) * 60
            ray = QPolygonF([QPointF(x0 - 30, 0), QPointF(x0 + 30, 0),
                             QPointF(x0 + 120 + sway, fl), QPointF(x0 - 80 + sway, fl)])
            rg = QLinearGradient(0, 0, 0, fl)
            rg.setColorAt(0, C(255, 255, 255, 46)); rg.setColorAt(1, C(255, 255, 255, 0))
            p.setBrush(rg); p.drawPolygon(ray)
        self.paint_floor(p, w, h, fl, u)
        strong = 2.2 if t < self.current_until else 1.0
        for px, ph, col, off in self.plants:
            self.paint_plant(p, px * w, fl, ph * h, col, off, u, strong)
        self.paint_castle(p, w * 0.30, fl + 6, u)
        for rx, size in self.rocks:
            self.paint_rock(p, rx * w, fl + 8, size * u)
        for it in self.items:
            self.paint_item(p, it, u)
        if self.snail:
            self.paint_snail(p, self.snail, fl, u)
        for bx, by, _, r, _ in self.bubbles:
            self.paint_bubble(p, bx, by, r * u)
        for fx, fy, rest, ph in self.flakes:
            p.setPen(Qt.NoPen)
            p.setBrush(C(240, 190, 70, int(255 * (1 - rest / 3))))
            p.save(); p.translate(fx, fy); p.rotate(math.degrees(t * 2 + ph))
            p.drawEllipse(QRectF(-4, -2.5, 8, 5)); p.restore()
        for f in sorted(self.fish, key=lambda f: f.pal["size"]):
            self.paint_fish(p, f, u)
        for x, y, ch, t0 in self.particles:
            age = t - t0
            p.setPen(C(255, 120, 160, int(255 * (1 - age / 1.6)))); p.setFont(QFont("Helvetica Neue", 20))
            p.drawText(QPointF(x + math.sin(age * 5) * 10, y - age * 50), ch)
        self.paint_event_overlay(p, w, h, fl, u)
        path = QPainterPath(QPointF(0, 0))
        for x in range(0, w + 8, 8):
            path.lineTo(x, 8 + math.sin(x * 0.02 + t * 1.6) * 3 + math.sin(x * 0.05 - t) * 1.5)
        path.lineTo(w, 0); path.closeSubpath()
        sg = QLinearGradient(0, 0, 0, 14)
        sg.setColorAt(0, C(255, 255, 255, 190)); sg.setColorAt(1, C(180, 235, 255, 40))
        p.setBrush(sg); p.setPen(Qt.NoPen); p.drawPath(path)
        if self.flash:
            p.fillRect(self.rect(), C(255, 255, 255, int(70 * self.flash)))
            p.setPen(QPen(C(255, 255, 255, int(160 * self.flash)), 3))
            r = (1 - self.flash) * 120 * u + 20
            p.setBrush(Qt.NoBrush); p.drawEllipse(QPointF(self.guppy.x, self.guppy.y), r, r * 0.6)
        if self.dark:
            og = QRadialGradient(self.guppy.x, self.guppy.y, 260 * u)
            og.setColorAt(0, C(0, 0, 25, 60)); og.setColorAt(1, C(0, 0, 18, 225))
            p.fillRect(self.rect(), og)
        for f in self.fish:
            self.paint_speech(p, f, u)
        p.setPen(C(255, 255, 255, 200)); p.setFont(QFont("Helvetica Neue", 12))
        p.drawText(14, 28, f"🍽 {self.eaten}   mood: {self.mood_of(self.guppy)}   🧠 lessons: {self.lessons}")
        if self.progress:
            a, b = self.progress
            p.setPen(Qt.NoPen); p.setBrush(C(10, 25, 50, 190)); p.drawRoundedRect(QRectF(14, 40, 220, 12), 6, 6)
            p.setBrush(C(255, 190, 80)); p.drawRoundedRect(QRectF(14, 40, 220 * a / max(1, b), 12), 6, 6)
            p.setPen(C(255, 220, 160)); p.drawText(244, 51, "learning…")
        if time.time() < self.help_until:
            self.paint_panel(p, HELP, 20, 66)
        p.end()

    def paint_event_overlay(self, p, w, h, fl, u):
        ev = self.ev
        if not ev:
            return
        x = (self.t - ev["t0"]) / ev["dur"]
        if ev["name"] == "shadow":
            cx = -150 + x * (w + 300)
            p.setPen(Qt.NoPen); p.setBrush(C(10, 10, 25, 110))
            p.drawEllipse(QPointF(cx, 70), 130, 70)
            p.drawPolygon(QPolygonF([QPointF(cx - 110, 30), QPointF(cx - 70, -20), QPointF(cx - 40, 25)]))
            p.drawPolygon(QPolygonF([QPointF(cx + 110, 30), QPointF(cx + 70, -20), QPointF(cx + 40, 25)]))
            p.setBrush(C(255, 230, 80, 200))
            for dx in (-45, 45):
                p.save(); p.translate(cx + dx, 70); p.drawEllipse(QRectF(-12, -20, 24, 40)); p.restore()
            p.setBrush(C(10, 10, 20))
            for dx in (-45, 45):
                p.save(); p.translate(cx + dx, 70); p.drawEllipse(QRectF(-3, -18, 6, 36)); p.restore()
        elif ev["name"] == "feeder" and x < 0.7:
            p.setPen(QPen(C(40, 50, 70), 2)); p.setBrush(C(190, 200, 215))
            p.drawRoundedRect(QRectF(w / 2 - 50, 8, 100, 34), 6, 6)
            p.setPen(Qt.NoPen); p.setBrush(C(240, 190, 70))
            p.drawRect(QRectF(w / 2 - 12, 40, 24, 5))

    def paint_snail(self, p, sn, fl, u):
        x, y, d = sn["x"], fl + 16 * u, sn["dir"]
        p.save(); p.translate(x, y); p.scale(d * u, u)
        p.setPen(QPen(C(60, 45, 30, 180), 1.5)); p.setBrush(C(210, 190, 160))
        p.drawRoundedRect(QRectF(-26, -8, 52, 10), 5, 5)
        g = QRadialGradient(-4, -22, 22); g.setColorAt(0, C(255, 220, 140)); g.setColorAt(1, C(170, 110, 60))
        p.setBrush(g); p.drawEllipse(QPointF(-4, -16), 15, 15)
        p.setBrush(Qt.NoBrush); p.setPen(QPen(C(120, 70, 30), 1.5)); p.drawArc(QRectF(-12, -24, 16, 16), 0, 270 * 16)
        p.setPen(QPen(C(190, 170, 140), 2)); p.drawLine(QPointF(22, -6), QPointF(27, -18)); p.drawLine(QPointF(25, -6), QPointF(32, -15))
        p.restore()

    def paint_floor(self, p, w, h, fl, u):
        g = QLinearGradient(0, fl - 10, 0, h)
        g.setColorAt(0, C(224, 196, 140)); g.setColorAt(1, C(150, 120, 80))
        path = QPainterPath(QPointF(0, h))
        for x in range(0, w + 10, 10):
            path.lineTo(x, fl + math.sin(x * 0.015) * 6)
        path.lineTo(w, h); path.closeSubpath()
        p.setPen(Qt.NoPen); p.setBrush(g); p.drawPath(path)
        for fx, fy, r, col in self.pebbles:
            x, y = fx * w, fl + 10 + fy * (h - fl - 14)
            p.setBrush(C(*col)); p.drawEllipse(QPointF(x, y), r * u, r * u * 0.7)

    def paint_plant(self, p, x, y, ht, col, off, u, strong=1.0):
        for k in range(3):
            sway = math.sin(self.t * 1.2 + off + k) * 22 * u * strong
            path = QPainterPath(QPointF(x + (k - 1) * 12 * u, y + 6))
            hh = ht * (0.75 + 0.25 * ((k + 1) % 3) / 2)
            path.cubicTo(x + (k - 1) * 12 * u - sway, y - hh * 0.4, x + (k - 1) * 18 * u + sway, y - hh * 0.7,
                         x + (k - 1) * 10 * u + sway * 1.5, y - hh)
            pen = QPen(C(col[0] - 12 * k, col[1] - 14 * k, col[2], 235), 9 * u)
            pen.setCapStyle(Qt.RoundCap)
            p.setPen(pen); p.setBrush(Qt.NoBrush); p.drawPath(path)

    def paint_rock(self, p, x, y, s):
        g = QRadialGradient(x - s * 0.2, y - s * 0.5, s)
        g.setColorAt(0, C(170, 170, 180)); g.setColorAt(1, C(70, 75, 90))
        p.setPen(QPen(C(40, 45, 60, 150), 1.5)); p.setBrush(g)
        path = QPainterPath()
        path.moveTo(x - s, y)
        path.cubicTo(x - s, y - s * 0.9, x - s * 0.3, y - s * 1.1, x + s * 0.2, y - s * 0.8)
        path.cubicTo(x + s * 0.9, y - s * 0.8, x + s * 1.1, y - s * 0.2, x + s, y)
        path.closeSubpath()
        p.drawPath(path)

    def paint_castle(self, p, x, y, u):
        s = u
        body = C(150, 120, 190); dark = C(105, 80, 150); light = C(185, 160, 220)
        p.setPen(QPen(C(70, 50, 110), 2));
        def block(rx, ry, rw, rh, col):
            g = QLinearGradient(rx, ry, rx + rw, ry)
            g.setColorAt(0, col.lighter(115)); g.setColorAt(1, col.darker(115))
            p.setBrush(g); p.drawRect(QRectF(rx, ry, rw, rh))
        block(x, y - 110 * s, 160 * s, 110 * s, body)
        for tx in (x - 18 * s, x + 128 * s):
            block(tx, y - 150 * s, 50 * s, 150 * s, light)
            for k in range(3):
                block(tx + k * 18 * s, y - 166 * s, 14 * s, 18 * s, light)
            p.setBrush(C(210, 60, 80)); p.drawPolygon(QPolygonF([QPointF(tx + 25 * s, y - 166 * s), QPointF(tx + 25 * s, y - 196 * s), QPointF(tx + 52 * s, y - 184 * s)]))
        for k in range(5):
            block(x + 8 * s + k * 30 * s, y - 126 * s, 18 * s, 18 * s, body)
        p.setBrush(C(25, 20, 50)); p.setPen(Qt.NoPen)
        path = QPainterPath(); path.moveTo(x + 62 * s, y); path.lineTo(x + 62 * s, y - 50 * s)
        path.arcTo(QRectF(x + 62 * s, y - 80 * s, 36 * s, 60 * s), 180, -180); path.lineTo(x + 98 * s, y)
        p.drawPath(path)
        p.setBrush(C(255, 230, 120, 200))
        p.drawEllipse(QPointF(x + 14 * s, y - 80 * s), 6 * s, 9 * s); p.drawEllipse(QPointF(x + 146 * s, y - 80 * s), 6 * s, 9 * s)

    def paint_bubble(self, p, x, y, r):
        g = QRadialGradient(x - r * 0.3, y - r * 0.3, r * 1.2)
        g.setColorAt(0, C(255, 255, 255, 120)); g.setColorAt(0.7, C(190, 235, 255, 40)); g.setColorAt(1, C(255, 255, 255, 110))
        p.setBrush(g); p.setPen(QPen(C(255, 255, 255, 130), 1))
        p.drawEllipse(QPointF(x, y), r, r)
        p.setPen(Qt.NoPen); p.setBrush(C(255, 255, 255, 190))
        p.drawEllipse(QPointF(x - r * 0.35, y - r * 0.35), r * 0.22, r * 0.22)

    def paint_item(self, p, it, u):
        name, x, y = it["name"], it["x"], it["y"]
        s = u
        p.setPen(QPen(C(30, 30, 40, 160), 1.5))
        if name in ("rock", "pebble"):
            self.paint_rock(p, x, y, (22 if name == "pebble" else 40) * s)
        elif name == "log":
            g = QLinearGradient(x, y - 24 * s, x, y); g.setColorAt(0, C(150, 100, 60)); g.setColorAt(1, C(90, 55, 30))
            p.setBrush(g); p.drawRoundedRect(QRectF(x - 60 * s, y - 28 * s, 120 * s, 28 * s), 12 * s, 12 * s)
            p.setBrush(C(200, 150, 100)); p.drawEllipse(QPointF(x + 56 * s, y - 14 * s), 8 * s, 14 * s)
        elif name == "shell":
            p.setBrush(C(250, 190, 200))
            path = QPainterPath(QPointF(x, y)); path.arcTo(QRectF(x - 28 * s, y - 56 * s, 56 * s, 112 * s), 0, 180); path.closeSubpath()
            p.drawPath(path)
            p.setPen(QPen(C(200, 120, 140), 1.5))
            for k in range(-2, 3): p.drawLine(QPointF(x, y), QPointF(x + k * 11 * s, y - 40 * s + abs(k) * 6 * s))
        elif name in ("chest", "treasure"):
            p.setBrush(C(140, 90, 40)); p.drawRoundedRect(QRectF(x - 40 * s, y - 36 * s, 80 * s, 36 * s), 4 * s, 4 * s)
            p.setBrush(C(170, 110, 50)); p.drawChord(QRectF(x - 40 * s, y - 62 * s, 80 * s, 52 * s), 0, 180 * 16)
            p.setBrush(C(255, 215, 60)); p.drawRect(QRectF(x - 6 * s, y - 40 * s, 12 * s, 16 * s))
            for k in range(4): p.drawEllipse(QPointF(x - 24 * s + k * 16 * s, y - 38 * s - (k % 2) * 4 * s), 6 * s, 4 * s)
        elif name == "anchor":
            pen = QPen(C(100, 110, 125), 7 * s); pen.setCapStyle(Qt.RoundCap); p.setPen(pen); p.setBrush(Qt.NoBrush)
            p.drawLine(QPointF(x, y), QPointF(x, y - 70 * s)); p.drawLine(QPointF(x - 18 * s, y - 56 * s), QPointF(x + 18 * s, y - 56 * s))
            p.drawEllipse(QPointF(x, y - 78 * s), 7 * s, 7 * s); p.drawArc(QRectF(x - 34 * s, y - 40 * s, 68 * s, 56 * s), 200 * 16, 140 * 16)
        elif name == "plant":
            self.paint_plant(p, x, y, 110 * s, (60, 180, 100), it["ph"], u)
        elif name == "skull":
            p.setBrush(C(235, 232, 220)); p.drawEllipse(QPointF(x, y - 26 * s), 24 * s, 22 * s)
            p.drawRect(QRectF(x - 12 * s, y - 14 * s, 24 * s, 14 * s))
            p.setBrush(C(25, 25, 30)); p.drawEllipse(QPointF(x - 9 * s, y - 28 * s), 6 * s, 7 * s); p.drawEllipse(QPointF(x + 9 * s, y - 28 * s), 6 * s, 7 * s)
        elif name == "snail":
            p.setBrush(C(235, 200, 120)); p.drawEllipse(QPointF(x, y - 20 * s), 20 * s, 20 * s)
            p.setBrush(C(200, 160, 150)); p.drawRoundedRect(QRectF(x - 34 * s, y - 10 * s, 70 * s, 12 * s), 6 * s, 6 * s)
            p.setPen(QPen(C(200, 160, 150), 2)); p.drawLine(QPointF(x + 32 * s, y - 8 * s), QPointF(x + 38 * s, y - 24 * s))
        elif name == "ball":
            g = QRadialGradient(x - 8 * s, y - 40 * s, 36 * s); g.setColorAt(0, C(255, 140, 140)); g.setColorAt(1, C(190, 40, 60))
            p.setBrush(g); p.drawEllipse(QPointF(x, y - 26 * s), 26 * s, 26 * s)
        elif name == "bridge":
            p.setBrush(C(140, 140, 155)); path = QPainterPath(QPointF(x - 70 * s, y)); path.lineTo(x - 70 * s, y - 20 * s)
            path.arcTo(QRectF(x - 70 * s, y - 70 * s, 140 * s, 100 * s), 180, -180); path.lineTo(x + 70 * s, y); path.closeSubpath()
            p.drawPath(path)
        elif name == "mirror":
            g = QLinearGradient(x, y - 70 * s, x + 30 * s, y); g.setColorAt(0, C(220, 245, 255, 200)); g.setColorAt(1, C(130, 180, 210, 160))
            p.setBrush(g); p.drawRoundedRect(QRectF(x - 24 * s, y - 80 * s, 48 * s, 80 * s), 8 * s, 8 * s)
        else:  # anything else: a labelled crate
            p.setBrush(C(190, 150, 90)); p.drawRoundedRect(QRectF(x - 38 * s, y - 52 * s, 76 * s, 52 * s), 5 * s, 5 * s)
            p.setPen(QPen(C(120, 85, 45), 2)); p.drawLine(QPointF(x - 38 * s, y - 26 * s), QPointF(x + 38 * s, y - 26 * s))
            p.setPen(C(60, 40, 20)); p.setFont(QFont("Helvetica Neue", max(8, int(11 * s)), QFont.Bold))
            p.drawText(QRectF(x - 38 * s, y - 52 * s, 76 * s, 52 * s), Qt.AlignCenter | Qt.TextWordWrap, name[:14])

    def paint_fish(self, p, f, u):
        pal = f.pal
        L = 150 * u * pal["size"]
        fast = abs(f.vx) + (4 if f.trick and f.trick["name"] == "dance" else 0)
        wag = math.sin(self.t * (5 + fast * 2.5)) * (0.07 + fast * 0.03) * L * pal["tail_len"] ** 0.5
        tl = pal["tail_len"]
        bh = pal["body_h"]
        p.save()
        p.translate(f.x, f.y)
        p.rotate(f.rot)
        p.scale(f.dirn, 1)
        if f.roll:
            p.scale(1, math.cos(f.roll))
        p.setPen(Qt.NoPen)
        tg = QLinearGradient(-0.3 * L, 0, -0.62 * L * tl, 0)
        tg.setColorAt(0, C(*pal["tail0"])); tg.setColorAt(1, C(*pal["tail1"], 200))
        tail = QPainterPath(QPointF(-0.28 * L, 0))
        tail.cubicTo(-0.45 * L * tl, -0.10 * L + wag * 0.4, -0.62 * L * tl, -0.30 * L * bh + wag, -0.66 * L * tl, -0.26 * L * bh + wag)
        tail.cubicTo(-0.55 * L * tl, -0.08 * L + wag * 0.6, -0.55 * L * tl, 0.08 * L + wag * 0.6, -0.66 * L * tl, 0.26 * L * bh + wag)
        tail.cubicTo(-0.62 * L * tl, 0.30 * L * bh + wag, -0.45 * L * tl, 0.10 * L + wag * 0.4, -0.28 * L, 0)
        p.setBrush(tg); p.drawPath(tail)
        fin = QPainterPath(QPointF(0.12 * L, -0.2 * L * bh))
        fin.cubicTo(0.0, -0.42 * L * bh + wag * 0.3, -0.18 * L, -0.40 * L * bh + wag * 0.5, -0.24 * L, -0.12 * L * bh)
        p.setBrush(C(*pal["fin"], 220)); p.drawPath(fin)
        body = QPainterPath(QPointF(0.5 * L, 0.02 * L))
        body.cubicTo(0.38 * L, -0.30 * L * bh, -0.12 * L, -0.34 * L * bh, -0.32 * L, -0.05 * L)
        body.lineTo(-0.32 * L, 0.05 * L)
        body.cubicTo(-0.12 * L, 0.34 * L * bh, 0.38 * L, 0.30 * L * bh, 0.5 * L, 0.02 * L)
        bg = QLinearGradient(0, -0.3 * L * bh, 0, 0.3 * L * bh)
        bg.setColorAt(0, C(*pal["top"])); bg.setColorAt(0.55, C(*pal["mid"])); bg.setColorAt(1, C(*pal["bot"]))
        p.setBrush(bg); p.setPen(QPen(C(*[int(c * 0.55) for c in pal["top"]], 200), 2)); p.drawPath(body)
        if pal["stripe"]:
            p.setPen(QPen(C(*pal["stripe"], 230), max(2, 0.05 * L)))
            p.drawLine(QPointF(0.38 * L, 0.0), QPointF(-0.3 * L, 0.0))
        p.setPen(QPen(C(255, 255, 255, 55), 2)); p.setBrush(Qt.NoBrush)
        for k in range(4):
            p.drawArc(QRectF(-0.22 * L + k * 0.14 * L, -0.15 * L * bh, 0.2 * L, 0.3 * L * bh), 100 * 16, 160 * 16)
        p.setPen(Qt.NoPen); p.setBrush(C(*pal["fin"], 200))
        p.save(); p.translate(0.12 * L, 0.08 * L); p.rotate(math.sin(self.t * 6) * 14 - 20)
        p.drawEllipse(QRectF(-0.14 * L, -0.04 * L, 0.18 * L, 0.09 * L)); p.restore()
        ex, ey = 0.33 * L, -0.07 * L
        p.setBrush(C(255, 255, 255)); p.setPen(QPen(C(60, 30, 20), 1.5)); p.drawEllipse(QPointF(ex, ey), 0.062 * L, 0.062 * L)
        look = max(-1, min(1, (f.tx - f.x) * f.dirn / 200))
        p.setBrush(C(15, 15, 25)); p.setPen(Qt.NoPen)
        p.drawEllipse(QPointF(ex + look * 0.02 * L, ey + 0.004 * L), 0.032 * L, 0.032 * L)
        p.setBrush(C(255, 255, 255)); p.drawEllipse(QPointF(ex + look * 0.02 * L + 0.01 * L, ey - 0.008 * L), 0.01 * L, 0.01 * L)
        p.setPen(QPen(C(150, 40, 30), 2)); p.setBrush(Qt.NoBrush)
        if f.persona == "grumpy":                      # frown + angry brow
            p.drawArc(QRectF(0.42 * L, 0.03 * L, 0.08 * L, 0.06 * L), 20 * 16, 140 * 16)
            p.setPen(QPen(C(60, 30, 20), 2)); p.drawLine(QPointF(ex - 0.06 * L, ey - 0.09 * L), QPointF(ex + 0.06 * L, ey - 0.05 * L))
        else:
            p.drawArc(QRectF(0.42 * L, 0.0 * L, 0.08 * L, 0.06 * L), 200 * 16, 140 * 16)
        p.setPen(Qt.NoPen); p.setBrush(C(255, 120, 130, 110)); p.drawEllipse(QPointF(0.30 * L, 0.05 * L), 0.04 * L, 0.025 * L)
        p.restore()
        if f is not self.guppy:                         # name tag
            p.setFont(QFont("Helvetica Neue", 11, QFont.Bold))
            p.setPen(C(255, 255, 255, 220))
            p.drawText(QRectF(f.x - 60, f.y + 0.34 * L * bh + 4, 120, 18), Qt.AlignCenter, f.name)

    def paint_speech(self, p, f, u):
        if not f.say or time.time() > f.say_until:
            return
        left = f.say_until - time.time()
        alpha = int(255 * min(1, left / 0.8))
        p.setFont(QFont("Helvetica Neue", max(11, int(15 * u))))
        lines = textwrap.wrap(f.say, 30)
        fm = p.fontMetrics()
        tw = max(fm.horizontalAdvance(l) for l in lines)
        bw, bh = tw + 28, fm.height() * len(lines) + 20
        sz = 110 * u * f.pal["size"]
        bx = max(10, min(f.x - bw / 2, self.width() - bw - 10))
        by = f.y - sz - bh
        above = by >= 18
        if not above:
            by = f.y + sz * 0.8
        r = QRectF(bx, by, bw, bh)
        edge = {"grumpy": C(190, 60, 60, alpha), "shy": C(150, 110, 190, alpha), "greedy": C(200, 140, 40, alpha),
                "cheerful": C(60, 170, 90, alpha)}.get(f.persona, C(40, 60, 90, alpha))
        p.setPen(QPen(edge, 2)); p.setBrush(C(255, 255, 255, int(alpha * 0.96)))
        p.drawRoundedRect(r, 14, 14)
        tx = max(bx + 18, min(f.x, bx + bw - 18))
        y0 = r.bottom() - 1 if above else r.top() + 1
        tail = QPolygonF([QPointF(tx - 9, y0), QPointF(tx + 9, y0), QPointF(f.x, f.y - sz * 0.55 if above else f.y + sz * 0.5)])
        p.setPen(Qt.NoPen); p.drawPolygon(tail)
        p.setPen(C(30, 45, 70, alpha))
        p.drawText(r.adjusted(14, 10, -14, -10), Qt.AlignLeft | Qt.AlignVCenter, "\n".join(lines))

    def paint_panel(self, p, text, x, y):
        p.setFont(QFont("Menlo", 11))
        fm = p.fontMetrics()
        lines = text.split("\n")
        w = max(fm.horizontalAdvance(l) for l in lines) + 24
        h = fm.height() * len(lines) + 18
        p.setPen(Qt.NoPen); p.setBrush(C(10, 25, 50, 210)); p.drawRoundedRect(QRectF(x, y, w, h), 12, 12)
        p.setPen(C(230, 245, 255))
        for i, l in enumerate(lines):
            p.drawText(x + 12, y + 14 + fm.ascent() + i * fm.height() - 4, l)


class Window(QWidget):
    def __init__(self, brain):
        super().__init__()
        self.brain, self.busy_fish, self.banter_busy = brain, set(), False
        self.setWindowTitle("guppytank")
        self.resize(1040, 720)
        self.setStyleSheet("QWidget{background:#0b1a2e;color:#dff3ff;} "
                           "QLineEdit{background:#12304f;border:1px solid #2b6ea8;border-radius:10px;padding:9px 12px;font-size:15px;} "
                           "QLabel{font-size:14px;color:#ffb866;}")
        self.tank = TankView()
        self.tank.on_fish_click = self.fish_clicked
        self.tank.lessons = len(brain.lessons)
        self.last = QLabel("guppy> hello. i am guppy.")
        self.box = QLineEdit(placeholderText='talk to guppy · @name to talk to a mate · /help for commands')
        self.box.returnPressed.connect(self.submit)
        lay = QVBoxLayout(self); lay.setContentsMargins(10, 10, 10, 10); lay.setSpacing(8)
        lay.addWidget(self.tank, 1); lay.addWidget(self.last); lay.addWidget(self.box)
        self.sig = Replier()
        self.sig.done.connect(self.got_reply)
        self.sig.taught.connect(self.got_taught)
        self.sig.progress.connect(lambda a, b: setattr(self.tank, "progress", (a, b)))
        self.sig.banter.connect(self.show_banter)
        self.pool = QThreadPool.globalInstance()
        self.tank.speak("hello. i am guppy.", 5)
        self.ev_timer = QTimer(self, timeout=self.random_event)
        self.ev_timer.start(random.randint(70, 140) * 1000)
        self.banter_timer = QTimer(self, timeout=self.start_banter)
        self.banter_timer.start(random.randint(30, 60) * 1000)

    # ── replies ────────────────────────────────────────────────────────
    def ask(self, prompt, fish=None):
        fish = fish or self.tank.guppy
        if fish.name in self.busy_fish:
            return
        self.busy_fish.add(fish.name)
        self.tank.speak("...", 60, fish)
        self.pool.start(ReplyJob(self.brain, prompt, self.tank.state_of(fish), fish.name, self.sig))

    def got_reply(self, name, text):
        self.busy_fish.discard(name)
        f = self.tank.find(name)
        if not f:
            return
        self.tank.speak(text, 7, f)
        self.last.setText(f"{name}> {text}")

    def got_taught(self, text):
        self.tank.progress = None
        self.tank.lessons = len(self.brain.lessons)
        self.tank.speak(text, 12)
        self.last.setText("guppy> " + text)

    # ── fish-to-fish banter ────────────────────────────────────────────
    def start_banter(self):
        self.banter_timer.start(random.randint(30, 60) * 1000)
        if self.banter_busy or self.busy_fish or not self.tank.mates() or self.tank.dark or self.brain.learning:
            return
        a, b = random.sample(self.tank.fish, 2)
        self.banter_busy = True
        self.pool.start(BanterJob(self.brain, a.name, b.name, random.choice(OPENERS),
                                  self.tank.state_of(a), self.tank.state_of(b), self.sig))

    def show_banter(self, lines):
        for i, (name, text) in enumerate(lines):
            QTimer.singleShot(i * 3300, lambda n=name, t=text: self._say_line(n, t))
        QTimer.singleShot(len(lines) * 3300, lambda: setattr(self, "banter_busy", False))

    def _say_line(self, name, text):
        f = self.tank.find(name)
        if f:
            self.tank.speak(text, 3.2, f)
            self.last.setText(f"{name}> {text}")

    # ── events ─────────────────────────────────────────────────────────
    def random_event(self):
        self.ev_timer.start(random.randint(70, 140) * 1000)
        if self.tank.ev or self.busy_fish or self.brain.learning:
            return
        self.trigger_event(random.choice(list(TankView.EVENTS)))

    def trigger_event(self, name):
        prompt = self.tank.start_event(name)
        self.last.setText(f"· event: {name}")
        self.ask(prompt)

    # ── commands ───────────────────────────────────────────────────────
    def fish_clicked(self, f):
        self.box.setText("" if f is self.tank.guppy else f"@{f.name} ")
        self.box.setFocus()
        self.last.setText(f"· talking to {f.name}")

    @staticmethod
    def _unquote(s):
        s = s.strip()
        for a, b in (('"', '"'), ("'", "'"), ("“", "”")):
            if len(s) >= 2 and s[0] == a and s[-1] == b:
                return s[1:-1].strip()
        return s

    def command(self, line):
        """Returns (note, prompt, fish_or_None)."""
        cmd, _, arg = line[1:].partition(" ")
        cmd, arg = cmd.lower(), arg.strip()
        T, B = self.tank, self.brain
        if cmd in ("give_flake", "give_flakes", "flake", "flakes", "feed"):
            T.drop_food(max(1, min(int(arg), 40)) if arg.isdigit() else None); return "flakes dropped", say_any("want some food?", "i have food for you", "dinner time", "here comes some food", "snack time"), None
        if cmd in ("drop_in", "drop", "add", "put"):
            if not arg: return "usage: /drop_in <object>  e.g. " + ", ".join(KNOWN[:6]), None, None
            T.drop_in(arg.lower()); return f"dropped a {arg} in", say_any(f"i put a new {arg.lower()} in the tank", f"i got you a {arg.lower()}", f"look. a {arg.lower()}", f"i added a {arg.lower()}"), None
        if cmd in ("remove", "rm", "take_out"):
            if not arg: return "usage: /remove <object|all>", None, None
            n = T.remove(arg.lower()); return (f"removed {n}", f"i took the {arg} out", None) if n else (f"no {arg} in the tank", None, None)
        if cmd == "tap": T.tap(); return "tap tap", say_any("tap tap tap", "i tapped the glass", "knock knock", "can you see me"), None
        if cmd == "bubbles": T.burst(); return "bubbles!", say_any("look at those bubbles", "there are so many bubbles", "the filter is making bubbles"), None
        if cmd == "clean": T.clean(); return "fresh water", say_any("time for a water change", "i'm cleaning the tank", "i'm adding new water"), None
        if cmd == "light":
            if arg not in ("on", "off"): return "usage: /light on|off", None, None
            T.dark = arg == "off"; return f"light {arg}", "the light is off now" if T.dark else "the light is on now", None
        if cmd in ("forget", "reset"):
            B.forget(); T.speak("what were we talking about. i forgot."); return "memory cleared", None, None
        if cmd == "help":
            T.help_until = time.time() + 25; return "", None, None
        if cmd == "add_fish":
            species, name = None, arg.lower()
            for sp in sorted(PALETTES, key=len, reverse=True):
                if name.startswith(sp):
                    species, name = sp, name[len(sp):].strip(); break
            if len(T.mates()) >= 3: return "the tank is full (3 mates max)", None, None
            f = T.add_fish(species, name or None)
            self.ask("who are you", f)
            return f"{f.name} the {f.persona} {f.species} joined", say_any("meet your new tank mate", "there's a new fish in the tank", "i got you a new friend"), None
        if cmd == "remove_fish":
            return (f"{arg} left the tank", None, None) if T.remove_fish(arg) else (f"no fish called {arg}", None, None)
        if cmd == "talk":
            name, _, rest = arg.partition(" ")
            f = T.find(name)
            return (f"talking to {f.name}", rest.strip(), f) if f and rest.strip() else ("usage: /talk <name> <text>", None, None)
        if cmd == "trick":
            name = arg.lower() or random.choice(TRICKS)
            if name not in TRICKS: return "tricks: " + ", ".join(TRICKS), None, None
            T.start_trick(name)
            return f"trick: {name}", say_any(*{"spin": ["do a spin", "do a barrel roll", "do a flip"],
                                               "loop": ["do a loop", "do a loop the loop"],
                                               "jump": ["jump out of the water", "jump"],
                                               "dance": ["dance for me", "can you dance", "show me a trick"]}[name]), None
        if cmd == "event":
            name = arg.lower() or random.choice(list(TankView.EVENTS))
            if name not in TankView.EVENTS: return "events: " + ", ".join(TankView.EVENTS), None, None
            self.trigger_event(name); return f"event: {name}", None, None
        if cmd == "train":
            fact = self._unquote(arg)
            if not fact: return '/train "Money is a currency. You can get anything with it."', None, None
            if B.engine is None: return "no model loaded (--no-model), so nothing to train", None, None
            if B.learning: return "still learning the last thing…", None, None
            T.progress = (0, 1); T.speak("learning… hold on.", 90)
            self.pool.start(TeachJob(B, fact, self.sig))
            return f"learning: {fact}", None, None
        if cmd == "untrain":
            if B.engine is None or B.learning: return "can't untrain right now", None, None
            T.progress = (0, 1)
            self.pool.start(TeachJob(B, "", self.sig, untrain=arg or "all"))
            return "unlearning…", None, None
        if cmd == "lessons":
            return (" | ".join(f"{i+1}. {x}" for i, x in enumerate(B.lessons)) or 'no lessons yet. try /train "..."'), None, None
        return f"unknown command /{cmd} (try /help)", None, None

    def submit(self):
        msg = self.box.text().strip()
        self.box.clear()
        if not msg:
            return
        fish = None
        if msg.startswith("/"):
            note, prompt, fish = self.command(msg)
            if note: self.last.setText("· " + note)
            if prompt: self.ask(prompt, fish)
            return
        if msg.startswith("@"):
            name, _, rest = msg[1:].partition(" ")
            fish = self.tank.find(name)
            if not fish or not rest.strip():
                self.last.setText("· usage: @name <text>  (fish here: " + ", ".join(f.name for f in self.tank.fish) + ")")
                return
            msg = rest.strip()
        if any(w in msg.lower() for w in FOOD_WORDS):
            self.tank.drop_food()
        self.ask(msg, fish)


def main(argv=None):
    ap = argparse.ArgumentParser(description="guppytank GUI")
    ap.add_argument("--checkpoint", default="checkpoints/best_model.pt")
    ap.add_argument("--tokenizer", default="data/tokenizer.json")
    ap.add_argument("--device", default="cpu")
    ap.add_argument("--replay", default="data_world/train.jsonl", help="data used to avoid forgetting during /train")
    ap.add_argument("--no-model", action="store_true")
    args = ap.parse_args(argv)
    brain = Brain(args)
    app = QApplication(sys.argv[:1])
    win = Window(brain)
    win.show()
    sys.exit(app.exec())


if __name__ == "__main__":
    main()
