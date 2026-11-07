"""Shared brain for the guppytank clients: state-aware replies, per-fish memory, and /train.

/train "fact" teaches Guppy a new fact by really fine-tuning the model: the fact is
turned into fish-voiced practice conversations, mixed with replay data (so it doesn't
forget how to be a fish), and trained from the base weights on every lesson taught so far.
Lessons persist in lessons.json (+ lessons_model.pt) next to the checkpoint.
"""

import copy
import io
import contextlib
import json
import os
import random
import re
import threading

from .world import fact_samples, format_world, random_state, state_text, subject_of

CANNED = ["bubbles. i love bubbles.", "is that food. it is food. yes.", "the water is nice today.",
          "i was just looking at the rock.", "hello big shape.", "i swim. then i swim back. it's a good life."]

DEFAULT_STATE = {"you": {"name": "guppy", "species": None, "persona": None},
                 "items": [], "mates": [], "mood": "happy", "doing": "swimming"}


IDENT_RE = re.compile(r"who are you|what are you|your name|what kind of fish|what species|what type of fish|"
                      r"introduce yourself|who am i talking to|tell me about yourself", re.I)


class Brain:
    def __init__(self, args):
        self.engine = None
        self.lock = threading.RLock()
        self.histories = {}
        self.recent = {}
        self.learning = False
        self.lessons, self.base_state = [], None
        ckpt = getattr(args, "checkpoint", "checkpoints/best_model.pt")
        self.dir = os.path.dirname(os.path.abspath(ckpt))
        self.lessons_path = os.path.join(self.dir, "lessons.json")
        self.user_weights = os.path.join(self.dir, "lessons_model.pt")
        self.replay_path = getattr(args, "replay", "data_world/train.jsonl")
        if os.path.exists(self.lessons_path):
            try:
                self.lessons = json.load(open(self.lessons_path))
            except Exception:
                self.lessons = []
        if args.no_model:
            return
        from .inference import GuppyInference
        with contextlib.redirect_stdout(io.StringIO()):
            self.engine = GuppyInference(args.checkpoint, args.tokenizer, args.device)
        self.base_state = {k: v.detach().cpu().clone() for k, v in self.engine.model.state_dict().items()}
        if self.lessons and os.path.exists(self.user_weights):
            import torch
            from .model import load_state_compat
            load_state_compat(self.engine.model, torch.load(self.user_weights, map_location="cpu"))

    # ── chatting ────────────────────────────────────────────────────────
    def reply(self, text, state=None, fish="guppy", remember=True):
        st = state or DEFAULT_STATE
        if self.engine is None:
            if "tank" in text.lower() and st["items"]:
                return "i see " + ", ".join(st["items"][:3]) + "."
            return random.choice(CANNED)
        hist = self.histories.setdefault(fish, [])
        msgs = [{"role": "system", "content": state_text(st)}] + hist + [{"role": "user", "content": text}]
        recent = self.recent.setdefault(fish, [])
        you = st["you"]
        need = []                              # identity questions must mention the fish's real name (and species)
        if IDENT_RE.search(text):
            need = [you["name"]] + ([you["species"]] if you.get("species") else [])
        reply, fallback = "", None
        for attempt in range(8):               # resample until fresh (not a recent repeat) and grounded
            with self.lock:
                out = self.engine.chat_completion(msgs, temperature=0.8 + 0.06 * attempt, top_k=60)
            reply = out["choices"][0]["message"]["content"] or "..."
            grounded = all(n.lower() in reply.lower() for n in need)
            if grounded and reply not in recent:
                break
            if grounded and fallback is None:
                fallback = reply
        else:
            reply = fallback or reply
            if need:                           # the model couldn't ground itself: introduce from the state line
                flavour = {"cheerful": "so happy to meet you.", "grumpy": "don't ask me stuff.", "shy": "um. hi.",
                           "greedy": "got any food."}.get(you.get("persona"), "")
                reply = (f"i'm {you['name']}. i'm a {you['species']}. {flavour}" if you.get("species")
                         else f"i'm {you['name']}. i live in this tank.").strip()
        recent.append(reply)
        del recent[:-10]
        if remember:
            hist += [{"role": "user", "content": text}, {"role": "assistant", "content": reply}]
            del hist[:-12]
        return reply

    def forget(self, fish=None):
        if fish:
            self.histories.pop(fish, None)
        else:
            self.histories.clear()

    # ── teaching ────────────────────────────────────────────────────────
    def _save_lessons(self):
        json.dump(self.lessons, open(self.lessons_path, "w"), indent=2)

    def _replay(self, n=1500):
        rows = []
        if os.path.exists(self.replay_path):
            with open(self.replay_path) as f:
                for i, line in enumerate(f):
                    if i >= 20000:
                        break
                    rows.append(json.loads(line)["text"])
        return random.sample(rows, min(n, len(rows)))

    def train(self, fact, progress=None):
        """Learn `fact` (blocking, run it in a thread). Returns Guppy's answer to 'what is <subject>'."""
        if self.engine is None:
            return "(no model loaded: nothing to train)"
        fact = fact.strip().strip('"').strip()
        if fact and fact not in self.lessons:
            self.lessons.append(fact)
            self._save_lessons()
        self._fit(progress)
        return self.reply(f"what is {subject_of(fact)}", remember=False)

    def untrain(self, which="all", progress=None):
        if which == "all":
            self.lessons = []
        elif which.isdigit() and 1 <= int(which) <= len(self.lessons):
            self.lessons.pop(int(which) - 1)
        else:
            return False
        self._save_lessons()
        self._fit(progress)
        return True

    def _fit(self, progress=None):
        import torch
        from .dataset import collate_fn, encode_example, example_to_xy
        from .model import GuppyLM
        self.learning = True
        try:
            eng = self.engine
            if not self.lessons:                       # nothing to learn: restore the base model
                with self.lock:
                    eng.model.load_state_dict(self.base_state)
                if os.path.exists(self.user_weights):
                    os.remove(self.user_weights)
                return
            dev = torch.device("mps" if torch.backends.mps.is_available() else "cpu")
            model = GuppyLM(eng.config)
            model.load_state_dict(self.base_state)
            model.to(dev).eval()                      # eval(): no dropout, so facts stick
            lesson_rows = [encode_example(eng.tokenizer, t, eng.config.max_seq_len)
                           for fact in self.lessons for t in fact_samples(fact, 40)]
            replay_rows = [encode_example(eng.tokenizer, t, eng.config.max_seq_len) for t in self._replay()]
            steps = min(400, 100 + 50 * len(self.lessons))
            opt = torch.optim.AdamW(model.parameters(), lr=2e-4, weight_decay=0.01, betas=(0.9, 0.95))
            for step in range(steps):
                batch = [example_to_xy(*random.choice(lesson_rows)) for _ in range(8)]
                if replay_rows:
                    batch += [example_to_xy(*random.choice(replay_rows)) for _ in range(16)]
                x, y = collate_fn(batch)
                _, loss = model(x.to(dev), y.to(dev))
                loss.backward()
                torch.nn.utils.clip_grad_norm_(model.parameters(), 1.0)
                opt.step()
                opt.zero_grad(set_to_none=True)
                if progress and step % 10 == 0:
                    progress(step, steps)
            new_state = {k: v.detach().cpu() for k, v in model.state_dict().items()}
            with self.lock:
                eng.model.load_state_dict(new_state)
                eng.model.eval()
            torch.save(new_state, self.user_weights)
        finally:
            self.learning = False
