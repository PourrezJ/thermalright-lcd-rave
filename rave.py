#!/usr/bin/env python3
"""TRCC RAVE V7.1 : visualiseur audio-réactif cyberpunk pour le LCD Thermalright.

Modes :
* CALME  : l'affichage Nova V6 (capteurs, heure, carrousel) quand la musique est douce,
           absente, ou trop calme pendant un moment.
* ACTIF  : GIFs d'anime coupés sur le beat + glitch. L'intensité des effets suit la
           « chaleur » de la musique (groove -> rave -> drop).
Bascule CALME <-> ACTIF avec transitions violentes (breach / extinction CRT).

« Chaleur » (0..1) calculée sur 6 s : densité d'attaques, platitude spectrale
(synthés saturés, charlestons), dynamique compressée, niveau. Calibrée sur des
morceaux de référence (piano 0.0, lo-fi 0.1, pop joyeuse 0.4, électro 0.8-1.0).
Image entière -> rotation fil TRCC -> RGB565 BE -> commande publique SendFrame.
"""
from __future__ import annotations

import argparse
import collections
import datetime
import logging
import math
import queue
import random
import signal
import subprocess
import sys
import threading
import time
from pathlib import Path

import numpy as np
from PIL import Image, ImageDraw, ImageFont, ImageSequence

HERE = Path(__file__).resolve().parent
V6_DIR = HERE.parent / 'nova-fullscroll-v6'
W, H = 320, 240
SIZE = (W, H)
FPS = 20
KEY = '0402:3922'
LOG = logging.getLogger('rave')
STOP = threading.Event()

FONT_DIR = HERE / 'fonts'
F_MONO = str(FONT_DIR / 'ShareTechMono-Regular.ttf')
F_JP = str(FONT_DIR / 'DotGothic16-Regular.ttf')
F_BIG = str(FONT_DIR / 'Orbitron.ttf')

# --------------------------------------------------------------------------
# Réglages de bascule (chaleur 0..1)
ENTER_HEAT = 0.60      # passe en ACTIF au-dessus...
ENTER_HOLD = 3.0       # ...pendant ce nombre de secondes
LEAVE_HEAT = 0.38      # repasse en CALME en dessous...
LEAVE_HOLD = 8.0       # ...pendant ce nombre de secondes
SILENCE_HOLD = 6.0     # ou après ce silence (pause, changement de piste)
REENTRY_SHORT = 30.0   # intro courte si l'OVERLOAD revient moins de 30 s après

# --------------------------------------------------------------------------
PALETTES = [
    ((10, 0, 30), (255, 0, 140), (0, 240, 255), (0, 255, 200)),     # néon magenta/cyan
    ((0, 10, 5), (120, 0, 255), (60, 255, 80), (60, 255, 80)),       # acid matrix
    ((15, 0, 0), (255, 20, 40), (255, 230, 220), (255, 70, 70)),     # danger rouge
    ((5, 5, 20), (0, 200, 255), (255, 240, 0), (255, 240, 0)),       # 2077 jaune/cyan
    ((20, 0, 25), (255, 60, 0), (255, 0, 255), (255, 140, 0)),       # synthwave
]

def make_lut(p):
    lo, mid, hi = (np.array(c, np.float32) for c in p[:3])
    t = np.linspace(0, 1, 256, dtype=np.float32)[:, None]
    a = np.clip(t * 2, 0, 1)
    b = np.clip(t * 2 - 1, 0, 1)
    return ((lo * (1 - a) + mid * a) * (1 - b) + hi * b).astype(np.float32)

LUTS = [make_lut(p) for p in PALETTES]

WORDS = ['BREACH', 'OVERDRIVE', 'NO MERCY', 'SYSTEM FAILURE', 'DROP', 'KILL SWITCH',
         'FULL SEND', 'ROOT ACCESS', 'GLITCH', 'OVERLOAD', 'BASS INJECT', 'MELTDOWN',
         '暴走', 'ハッキング', '限界突破', 'サイバー', '覚醒', '危険', 'ブースト', '侵入']
KATA = 'アイウエオカキクケコサシスセソタチツテトナニヌネノハヒフヘホマミムメモヤユヨラリルレロワヲン0123456789'
HEX = '0123456789ABCDEF'

def hexs(n):
    return ''.join(random.choice(HEX) for _ in range(n))

def hack_line(intense: bool) -> tuple[str, bool]:
    if random.random() < 0.10:
        return ''.join(random.choice(KATA) for _ in range(random.randint(6, 11))), True
    tpl = random.choice([
        lambda: f'0x{hexs(8)} {" ".join(hexs(2) for _ in range(5))}',
        lambda: f'> inject payload @0x{hexs(6)}',
        lambda: f'> bypass firewall node[{random.randint(0, 255)}]',
        lambda: f'root@nova:~# ./overdrive -{random.choice("xvzf")}',
        lambda: f'decrypt [{"#" * (k := random.randint(1, 10))}{"." * (10 - k)}] {k * 10}%',
        lambda: f'[!] ICE breach sector {hexs(4)}',
        lambda: f'ssh ghost@10.{random.randint(0, 255)}.{random.randint(0, 255)}.{random.randint(1, 254)}',
        lambda: f'mov rax,0x{hexs(8)}',
        lambda: f'xor rbx,rbx ; jmp 0x{hexs(6)}',
        lambda: f'>> upload daemon.ko {random.randint(10, 99)}MB/s',
        lambda: f'SEGFAULT pid {random.randint(1000, 99999)}',
        lambda: f'[{"!!" if intense else "ok"}] neural sync {random.randint(60, 100)}%',
        lambda: f'trace {hexs(4)}::{hexs(4)} LOST',
        lambda: 'sudo rm -rf /limits/*',
        lambda: f'ACCESS GRANTED lvl {random.randint(1, 9)}',
        lambda: f'> spoof MAC {":".join(hexs(2) for _ in range(4))}',
    ])
    return tpl(), False

# --------------------------------------------------------------------------
# Encodage LCD (identique à la V6 : profil SCSI FBL51, rotation fil TRCC)

def make_encoder():
    from trcc.adapters.device.scsi_lcd import scsi_profile
    from trcc.core.protocol import wire_angle
    prof = scsi_profile(51)
    angle = wire_angle(prof, 0, False)
    if (prof.width, prof.height) != (320, 240) or not prof.big_endian:
        raise RuntimeError(f'profil LCD inattendu {prof!r}')

    def encode(arr: np.ndarray) -> bytes:
        a = arr
        if angle == 90:      # PIL ROTATE_270 == rotation horaire == np.rot90(k=-1)
            a = np.rot90(a, k=-1)
        elif angle == 180:
            a = a[::-1, ::-1]
        elif angle == 270:
            a = np.rot90(a, k=1)
        elif angle != 0:
            raise RuntimeError(f'rotation {angle}')
        a = a.astype(np.uint16)
        word = ((a[:, :, 0] >> 3) << 11) | ((a[:, :, 1] >> 2) << 5) | (a[:, :, 2] >> 3)
        return np.ascontiguousarray(word).astype('>u2').tobytes()
    return encode, angle, prof

# --------------------------------------------------------------------------
# Audio

def clip01(x):
    return 0.0 if x < 0 else 1.0 if x > 1 else float(x)

class Audio(threading.Thread):
    RATE = 48000
    HOP = 512
    N = 2048
    WIN_HOPS = 563          # 6 s de fenêtre pour la chaleur
    BLOCK = 47              # 0.5 s

    def __init__(self, file=None, start=0.0):
        super().__init__(daemon=True, name='audio')
        self.file, self.file_start = file, start
        self.lock = threading.Lock()
        self.buf = np.zeros(self.N, np.float32)
        self.win = np.hanning(self.N).astype(np.float32)
        fr = np.fft.rfftfreq(self.N, 1 / self.RATE)
        self.bass_bins = (fr >= 35) & (fr <= 150)
        self.mid_bins = (fr > 150) & (fr <= 2500)
        self.high_bins = (fr > 2500) & (fr <= 14000)
        self.flux_bins = fr < 12000
        self.flat_bins = (fr > 100) & (fr < 10000)
        edges = np.geomspace(40, 14000, 33)
        self.spec_mat = np.zeros((32, len(fr)), np.float32)
        for i in range(32):
            sel = (fr >= edges[i]) & (fr < max(edges[i + 1], edges[i] + 24))
            if sel.any():
                self.spec_mat[i, sel] = 1.0 / sel.sum()
        self.peaks = {'bass': 1e-3, 'mid': 1e-3, 'high': 1e-3, 'spec': 1e-3, 'rms': 1e-4}
        self.bass_hist = collections.deque(maxlen=90)
        self.high_hist = collections.deque(maxlen=60)
        self.rms_short = collections.deque(maxlen=50)
        self.rms_long = collections.deque(maxlen=750)
        self.beat_times = collections.deque(maxlen=24)
        self.f_flux = collections.deque(maxlen=self.WIN_HOPS)
        self.f_flat = collections.deque(maxlen=self.WIN_HOPS)
        self.f_db = collections.deque(maxlen=self.WIN_HOPS)
        self.prev_lm = None
        self.hops = 0
        # état public
        self.bass = self.mid = self.high = 0.0
        self.spectrum = np.zeros(32, np.float32)
        self.beats = 0
        self.last_beat = 0.0
        self.beat_strength = 0.0
        self.hats = 0
        self.last_hat = 0.0
        self.bpm = 0.0
        self.level = 0.0
        self.drop = 0.0
        self.heat = 0.0
        self.heat_raw = 0.0
        self.parts = (0.0, 0.0, 0.0, 0.0)
        self.alive = False
        self.last_sound = -99.0
        self.last_heat_t = None

    def run(self):
        while not STOP.is_set():
            cmd = ['pw-record', '-P', '{ stream.capture.sink=true node.name=trcc-rave-monitor '
                   'node.description="TRCC Rave monitor" }',
                   '--rate', str(self.RATE), '--channels', '1', '--format', 'f32', '-']
            if self.file:   # test : fichier lu en temps réel au lieu du moniteur
                cmd = ['ffmpeg', '-v', 'error', '-re', '-ss', str(self.file_start), '-i', self.file,
                       '-ac', '1', '-ar', str(self.RATE), '-f', 'f32le', '-']
            try:
                proc = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL)
            except FileNotFoundError:
                LOG.error('pw-record introuvable, mode sans audio'); return
            LOG.info('capture audio démarrée (moniteur sortie par défaut)')
            need = self.HOP * 4
            try:
                while not STOP.is_set():
                    raw = proc.stdout.read(need)
                    if not raw or len(raw) < need:
                        break
                    self.process(np.frombuffer(raw, np.float32))
            finally:
                proc.kill(); proc.wait()
                self.alive = False
            if not STOP.is_set():
                LOG.warning('capture audio coupée, relance dans 2 s'); time.sleep(2)

    def _agc(self, name, v, decay=0.9985, floor=1e-4):
        p = max(v, self.peaks[name] * decay, floor)
        self.peaks[name] = p
        return v / p

    def _update_heat(self, now):
        n = len(self.f_flux)
        if n < 4 * 94:          # 4 s minimum avant de juger
            return
        F = np.fromiter(self.f_flux, np.float32, n)
        thr = np.convolve(F, np.ones(self.BLOCK, np.float32) / self.BLOCK, 'same')
        c = F[1:-1]
        onsets = int(((c > thr[1:-1] * 1.5) & (c >= F[:-2]) & (c >= F[2:])).sum())
        dens = onsets / (n * self.HOP / self.RATE)
        flat = float(np.mean(self.f_flat)) * 100
        db = np.fromiter(self.f_db, np.float32, n)
        k = self.BLOCK
        blocks = db[: (n // k) * k].reshape(-1, k).mean(axis=1)
        dyn = float(blocks.std())
        loud = float(db.mean())
        s_dens = clip01((dens - 3.0) / 4.0)
        s_flat = clip01((math.log10(max(flat, 1e-3)) + 0.3) / 1.3)
        s_dyn = clip01((4.5 - dyn) / 3.5)
        s_loud = clip01((loud + 24.0) / 10.0)
        raw = 0.35 * s_dens + 0.30 * s_flat + 0.20 * s_dyn + 0.15 * s_loud
        if now - self.last_sound > 1.5:
            raw = 0.0
        dt = 0.25 if self.last_heat_t is None else now - self.last_heat_t
        self.last_heat_t = now
        tau = 1.2 if raw > self.heat else 3.5
        heat = self.heat + (raw - self.heat) * (1 - math.exp(-dt / tau))
        with self.lock:
            self.heat_raw, self.heat = raw, heat
            self.parts = (s_dens, s_flat, s_dyn, s_loud)

    def process(self, chunk, now=None):
        now = time.monotonic() if now is None else now
        self.alive = True
        chunk = np.clip(np.nan_to_num(chunk, nan=0.0, posinf=0.0, neginf=0.0), -4.0, 4.0)
        self.buf = np.roll(self.buf, -len(chunk))
        self.buf[-len(chunk):] = chunk
        mag = np.abs(np.fft.rfft(self.buf * self.win))
        ms = float(np.mean(chunk * chunk))
        rms = math.sqrt(ms)
        silent = rms < 0.002
        if not silent:
            self.last_sound = now
        # caractéristiques de chaleur
        mm = float(mag.mean())
        lm = np.log(mag + 1e-3 * mm + 1e-9)
        if self.prev_lm is not None:
            self.f_flux.append(float(np.maximum(0, lm - self.prev_lm)[self.flux_bins].mean()))
        self.prev_lm = lm
        p = mag[self.flat_bins] ** 2 + 1e-12
        self.f_flat.append(float(np.exp(np.log(p).mean()) / p.mean()))
        self.f_db.append(max(-60.0, 10 * math.log10(ms + 1e-12)))
        self.hops += 1
        if self.hops % 24 == 0:
            self._update_heat(now)
        # réactivité instantanée
        bass = float(mag[self.bass_bins].mean())
        mid = float(mag[self.mid_bins].mean())
        high = float(mag[self.high_bins].mean())
        b = self._agc('bass', bass)
        m = self._agc('mid', mid)
        h = self._agc('high', high)
        spec = self.spec_mat @ mag.astype(np.float32)
        spec *= np.linspace(0.6, 3.0, 32, dtype=np.float32)
        self._agc('spec', float(spec.max()), decay=0.999)
        spec = np.sqrt(np.clip(spec / max(self.peaks['spec'], 1e-6), 0, 1))
        avg = (sum(self.bass_hist) / len(self.bass_hist)) if self.bass_hist else bass
        self.bass_hist.append(bass)
        beat = (not silent and bass > avg * 1.38 and b > 0.35 and now - self.last_beat > 0.23)
        havg = (sum(self.high_hist) / len(self.high_hist)) if self.high_hist else high
        self.high_hist.append(high)
        hat = (not silent and high > havg * 1.6 and h > 0.3 and now - self.last_hat > 0.09)
        self.rms_short.append(rms); self.rms_long.append(rms)
        rs = sum(self.rms_short) / len(self.rms_short)
        rl = sum(self.rms_long) / len(self.rms_long)
        drop = clip01((rs / max(rl, 1e-5) - 1.0) / 0.6) if not silent else 0.0
        lvl = self._agc('rms', rms, decay=0.9995)
        with self.lock:
            self.bass = b if b > self.bass else self.bass * 0.55 + b * 0.45
            self.mid = self.mid * 0.6 + m * 0.4
            self.high = self.high * 0.5 + h * 0.5
            self.spectrum = np.maximum(spec, self.spectrum * 0.82)
            self.level = self.level * 0.8 + lvl * 0.2
            self.drop = self.drop * 0.97 + drop * 0.03
            if beat:
                if self.last_beat and 0.28 < now - self.last_beat < 1.1:
                    self.beat_times.append(now - self.last_beat)
                    if len(self.beat_times) >= 4:
                        bpm = 60 / float(np.median(self.beat_times))
                        while bpm < 85: bpm *= 2
                        while bpm > 180: bpm /= 2
                        self.bpm = bpm if not self.bpm else self.bpm * 0.8 + bpm * 0.2
                self.beats += 1
                self.last_beat = now
                self.beat_strength = min(1.0, bass / max(avg, 1e-6) / 2.5)
            if hat:
                self.hats += 1
                self.last_hat = now

    def snapshot(self, now=None):
        now = time.monotonic() if now is None else now
        with self.lock:
            return dict(bass=self.bass, mid=self.mid, high=self.high, spectrum=self.spectrum.copy(),
                        beats=self.beats, last_beat=self.last_beat, strength=self.beat_strength,
                        hats=self.hats, last_hat=self.last_hat, bpm=self.bpm, level=self.level,
                        drop=self.drop, heat=self.heat, heat_raw=self.heat_raw, parts=self.parts,
                        alive=self.alive, silent_for=now - self.last_sound,
                        silent=now - self.last_sound > 2.5)

class ModeMachine:
    """CALME <-> ACTIF avec hystérésis."""
    def __init__(self):
        self.mode = 'calm'
        self.hot_since = None
        self.cold_since = None

    def update(self, now, heat, silent_for):
        if self.mode == 'calm':
            if heat > ENTER_HEAT and silent_for < 0.5:
                if self.hot_since is None: self.hot_since = now
            else:
                self.hot_since = None
            if self.hot_since is not None and now - self.hot_since >= ENTER_HOLD:
                self.mode, self.cold_since = 'active', None
                return 'in'
        else:
            if heat < LEAVE_HEAT:
                if self.cold_since is None: self.cold_since = now
            else:
                self.cold_since = None
            if silent_for >= SILENCE_HOLD or (self.cold_since is not None and now - self.cold_since >= LEAVE_HOLD):
                self.mode, self.hot_since = 'calm', None
                return 'out'
        return None

# --------------------------------------------------------------------------
# GIFs

class Clip:
    def __init__(self, path: Path, frames, durations, label):
        self.path, self.frames, self.durations, self.label = path, frames, durations, label
        self.cat = path.name.split('-')[0].lower()

def cover(img: Image.Image) -> np.ndarray:
    img = img.convert('RGB')
    w, h = img.size
    s = max(W / w, H / h)
    nw, nh = max(W, round(w * s)), max(H, round(h * s))
    img = img.resize((nw, nh), Image.Resampling.BILINEAR)
    x, y = (nw - W) // 2, (nh - H) // 2
    return np.asarray(img.crop((x, y, x + W, y + H)), dtype=np.uint8).copy()

def load_clip(path: Path) -> Clip | None:
    try:
        frames, durs = [], []
        if path.suffix.lower() in ('.mp4', '.webm', '.mkv', '.mov'):
            cmd = ['ffmpeg', '-hide_banner', '-loglevel', 'error', '-t', '8', '-i', str(path), '-an',
                   '-vf', f'fps=15,scale={W}:{H}:force_original_aspect_ratio=increase,crop={W}:{H}',
                   '-f', 'rawvideo', '-pix_fmt', 'rgb24', 'pipe:1']
            raw = subprocess.run(cmd, check=True, capture_output=True, timeout=60).stdout
            n = len(raw) // (W * H * 3)
            frames = [np.frombuffer(raw[i * W * H * 3:(i + 1) * W * H * 3], np.uint8).reshape(H, W, 3)
                      for i in range(min(n, 120))]
            durs = [1 / 15] * len(frames)
        else:
            im = Image.open(path)
            n = getattr(im, 'n_frames', 1)
            step = max(1, math.ceil(n / 90))
            for i, fr in enumerate(ImageSequence.Iterator(im)):
                if i % step:
                    continue
                d = (fr.info.get('duration') or 70) / 1000 * step
                frames.append(cover(fr.convert('RGBA')))
                durs.append(min(max(d, 0.02), 0.5))
        if not frames:
            return None
        lbl = path.with_suffix('.txt')
        label = lbl.read_text().strip() if lbl.exists() else path.stem
        return Clip(path, frames, durs, label)
    except Exception as e:
        LOG.warning('clip illisible %s: %s', path.name, e)
        return None

HARD = ('punch', 'kick', 'shoot', 'yeet', 'run')

class ClipPool(threading.Thread):
    def __init__(self, size=7):
        super().__init__(daemon=True, name='clips')
        self.size = size
        self.ready: collections.deque[Clip] = collections.deque()
        self.lock = threading.Lock()
        self.wake = threading.Event()
        self.recent = collections.deque(maxlen=20)

    def files(self):
        exts = ('.gif', '.mp4', '.webm', '.webp', '.mkv', '.mov')
        return [p for p in sorted((HERE / 'gifs').iterdir()) if p.suffix.lower() in exts]

    def run(self):
        while not STOP.is_set():
            with self.lock:
                n = len(self.ready)
            if n < self.size:
                files = self.files()
                if not files:
                    time.sleep(5); continue
                with self.lock:
                    loaded = {c.path for c in self.ready}
                    hard_n = sum(c.cat in HARD for c in self.ready)
                cand = [f for f in files if f not in loaded and f not in self.recent] or files
                want_hard = hard_n < 3
                pref = [f for f in cand if (f.name.split('-')[0] in HARD) == want_hard]
                f = random.choice(pref or cand)
                clip = load_clip(f)
                if clip:
                    with self.lock:
                        self.ready.append(clip)
                    self.recent.append(f)
                continue
            self.wake.wait(1.0); self.wake.clear()

    def take(self, hard: bool, current: Clip | None) -> Clip | None:
        with self.lock:
            opts = [c for c in self.ready if c is not current]
            if not opts:
                return current
            pref = [c for c in opts if (c.cat in HARD) == hard] or opts
            c = random.choice(pref)
            self.ready.remove(c)
        self.wake.set()
        return c

# --------------------------------------------------------------------------
# Capteurs + affichage calme (V6)

class Sensors(threading.Thread):
    IDS = ('cpu:temp', 'gpu:primary:temp', 'gpu:amd:1:temp')

    def __init__(self, bus, calm=None):
        super().__init__(daemon=True, name='sensors')
        self.bus, self.calm, self.values = bus, calm, {}

    def run(self):
        from trcc.core.commands.system import ReadSensors
        while not STOP.is_set():
            try:
                vals = {}
                for r in self.bus.dispatch(ReadSensors()).readings:
                    if r.sensor_id in self.IDS:
                        v = float(r.value)
                        if r.unit == '°F': v = (v - 32) * 5 / 9
                        if math.isfinite(v): vals[r.sensor_id] = v
                self.values = vals
            except Exception as e:
                LOG.debug('capteurs indisponibles: %s', e)
            if self.calm is not None:
                self.calm.refresh()
            STOP.wait(1.5)

class CalmView:
    """Réutilise tel quel le rendu Nova V6 (fond animé + capteurs + heure)."""
    def __init__(self):
        self.v6 = None
        try:
            sys.path.insert(0, str(V6_DIR))
            import fullscroll as fs
            fs.init_colors()
            self.v6 = fs.Render()
            LOG.info('mode calme : rendu Nova V6 chargé')
        except Exception as e:
            LOG.warning('rendu V6 indisponible (%s), mode calme simplifié', e)
        self.f = ImageFont.truetype(F_MONO, 28)
        self.fs = ImageFont.truetype(F_MONO, 14)
        self.t0 = time.monotonic()

    def refresh(self):
        if self.v6 is not None:
            try: self.v6.update_sensors()
            except Exception as e: LOG.debug('V6 capteurs: %s', e)

    def render(self, now, sensors: dict) -> np.ndarray:
        if self.v6 is not None:
            try:
                img, _, _ = self.v6.compose(now - self.t0)
                return np.asarray(img.convert('RGB'))
            except Exception as e:
                LOG.warning('V6 compose: %s', e); self.v6 = None
        img = Image.new('RGB', SIZE, (4, 6, 16))
        d = ImageDraw.Draw(img)
        d.text((W // 2, 40), datetime.datetime.now().strftime('%H:%M'), font=self.f, fill='white', anchor='mm')
        y = 100
        for lbl, k in (('CPU', 'cpu:temp'), ('GPU', 'gpu:primary:temp'), ('6950', 'gpu:amd:1:temp')):
            v = sensors.get(k)
            d.text((60, y), lbl, font=self.fs, fill=(140, 150, 170), anchor='lm')
            d.text((260, y), '--' if v is None else f'{v:.0f}°C', font=self.f, fill=(0, 230, 255), anchor='rm')
            y += 44
        return np.asarray(img)

# --------------------------------------------------------------------------
# Rendu

_TXT: dict = {}

def text_mask(txt, font, anchor='la'):
    k = (txt, id(font), anchor)
    m = _TXT.get(k)
    if m is None:
        if len(_TXT) > 4000:
            _TXT.clear()
        l, t, r, b = font.getbbox(txt, anchor=anchor)
        im = Image.new('L', (max(1, r - l), max(1, b - t)))
        ImageDraw.Draw(im).text((-l, -t), txt, font=font, fill=255, anchor=anchor)
        m = _TXT[k] = (im, l, t)
    return m

def text(draw, xy, txt, font, fill, anchor='la'):
    """draw.text avec masque en cache (la rastérisation coûte cher)."""
    im, l, t = text_mask(txt, font, anchor)
    draw.bitmap((int(xy[0] + l), int(xy[1] + t)), im, fill=fill)

def env(t_since, tau):
    return math.exp(-max(0.0, t_since) / tau)

def crt(arr: np.ndarray, squeeze: float) -> np.ndarray:
    """Effet extinction/allumage de tube : squeeze 0 = normal, 1 = ligne."""
    h = max(2, int(H * (1 - squeeze) ** 2))
    w = W if squeeze < 0.85 else max(4, int(W * (1 - (squeeze - 0.85) / 0.15)))
    img = Image.fromarray(np.clip(arr, 0, 255).astype(np.uint8)).resize((w, h), Image.Resampling.BILINEAR)
    out = np.zeros((H, W, 3), np.float32)
    y, x = (H - h) // 2, (W - w) // 2
    out[y:y + h, x:x + w] = np.asarray(img, np.float32) * (1 + 1.5 * squeeze)
    return out

class Terminal:
    """Fenêtre de hack qui apparaît/disparaît par sessions, lignes tapées en direct."""
    PW, ROWS, LH = 160, 10, 12

    def __init__(self):
        self.font = ImageFont.truetype(F_MONO, 11)
        self.jp = ImageFont.truetype(F_JP, 11)
        self.head = ImageFont.truetype(F_MONO, 10)
        self.ph = 15 + self.ROWS * self.LH + 3
        self.y0 = 36
        self.active = False
        self.t_in = self.t_end = 0.0
        self.next_start = 0.0
        self.side = 0
        self.lines: collections.deque = collections.deque(maxlen=self.ROWS)
        self.next_line = 0.0
        self.title = ''

    def _add(self, now, intense):
        txt, jp = hack_line(intense)
        self.lines.append([txt, jp, now])

    def update(self, now, I, beat, drop):
        if not self.active:
            if now >= self.next_start and (beat or now >= self.next_start + 1.0):
                self.active = True
                self.t_in = now
                self.t_end = now + random.uniform(3.5, 6.5) * (1 - 0.4 * I)
                self.side = random.choice((0, 1))
                self.lines.clear()
                self.title = random.choice(['root@nova', 'ghost@net', 'nova::breach', 'kernel/0x7F',
                                            'ICE-killer', 'daemon.ko', 'h4x0r'])
                for k in range(random.randint(2, 4)):
                    self._add(now - 5, False)
                self.next_line = now
            return
        if now >= self.t_end:
            self.active = False
            self.next_start = now + random.uniform(0.8, 3.0) * (1.4 - I) + 0.5 * I
            return
        cps = 45 + 110 * I
        last = self.lines[-1] if self.lines else None
        typed = last is None or (now - last[2]) * cps >= len(last[0])
        if beat or (typed and now >= self.next_line):
            self._add(now, drop > 0.5 or I > 0.8)
            self.next_line = now + random.uniform(0.15, 0.45) * (1.2 - I)

    def draw(self, arr: np.ndarray, now, I, kick, acc, hot):
        if not self.active:
            return
        age, left = now - self.t_in, self.t_end - now
        IN, OUT = 0.22, 0.4
        A = min(1.0, age / IN, max(0.0, left) / OUT)
        entering, leaving = age < IN, left < OUT
        pw, ph = self.PW, self.ph
        layer = Image.new('RGB', (pw, ph))
        mask = Image.new('L', (pw, ph))
        dl, dm = ImageDraw.Draw(layer), ImageDraw.Draw(mask)
        # pas de fenêtre : seulement les lignes, avec une ombre fine pour rester lisibles
        cps = 45 + 110 * I
        n = len(self.lines)
        y = 15
        for i, (txt, jp, born) in enumerate(self.lines):
            shown = txt if (now - born) * cps >= len(txt) else txt[:max(0, int((now - born) * cps))]
            fade = 1.0
            if n == self.ROWS and i < 3:           # haut : fondu de sortie
                fade = (0.35, 0.6, 0.85)[i]
            young = now - born - len(txt) / cps    # temps depuis fin de frappe
            col = (255, 255, 255) if young < 0.25 else acc
            if shown:
                im, l, t = text_mask(shown[:27] if not jp else shown[:12], self.jp if jp else self.font)
                a = int(255 * fade * min(1.0, (now - born) / 0.08 + 0.3))
                for ox, oy in ((1, 1), (0, 1), (1, 0), (-1, 0)):      # contour sombre fin
                    dl.bitmap((4 + ox + l, y + oy + t), im, fill=(0, 0, 0))
                    dm.bitmap((4 + ox + l, y + oy + t), im, fill=int(a * 0.8))
                dl.bitmap((4 + l, y + t), im, fill=col)
                dm.bitmap((4 + l, y + t), im, fill=a)
                if i == n - 1 and len(shown) < len(txt) or (i == n - 1 and int(now * 5) % 2):
                    cx = 4 + l + im.size[0] + 2
                    if cx < pw - 6:
                        dl.rectangle((cx, y + 2, cx + 5, y + 10), fill=acc)
                        dm.rectangle((cx, y + 2, cx + 5, y + 10), fill=255)
            y += self.LH
        L = np.asarray(layer, np.float32)
        M = np.asarray(mask, np.float32) / 255.0 * A
        # violence : entrée / sortie glitchée, split RGB sur les kicks
        dx = 0
        if entering or leaving:
            p = (1 - age / IN) if entering else (1 - left / OUT)
            dx = int(random.uniform(-1, 1) * 70 * p)
            L = L.copy(); M = M.copy()
            for _ in range(int(3 + 9 * p)):
                y1 = random.randrange(0, ph - 3); hh = random.randint(2, 16)
                s = random.randint(-50, 50)
                L[y1:y1 + hh] = np.roll(L[y1:y1 + hh], s, axis=1)
                M[y1:y1 + hh] = np.roll(M[y1:y1 + hh], s, axis=1)
                if random.random() < 0.3:
                    M[y1:y1 + hh] *= 0.2
            if entering and age < 0.07:
                L = np.full_like(L, 255.0)
        k = int(1 + 6 * kick * I)
        if k > 1 or entering or leaving:
            k = max(k, 4 if (entering or leaving) else 0)
            L = L.copy()
            L[:, :, 0] = np.roll(L[:, :, 0], k, axis=1)
            L[:, :, 2] = np.roll(L[:, :, 2], -k, axis=1)
        x0 = (4 if self.side == 0 else W - pw - 4) + dx
        y0 = self.y0
        xa, xb = max(0, x0), min(W, x0 + pw)
        if xb <= xa:
            return
        Ls, Ms = L[:, xa - x0:xb - x0], M[:, xa - x0:xb - x0, None]
        reg = arr[y0:y0 + ph, xa:xb]
        reg *= (1 - Ms)
        reg += Ls * Ms

def tile_fx(arr: np.ndarray, kind: str) -> np.ndarray:
    """Effets de beat OVERLOAD : miroir, mosaïque 2x2, kaléidoscope."""
    if kind == 'mirror':
        out = arr.copy(); out[:, W // 2:] = arr[:, :W // 2][:, ::-1]; return out
    if kind == 'vmirror':
        out = arr.copy(); out[H // 2:] = arr[:H // 2][::-1]; return out
    if kind == 'kaleido':
        q = arr[:H // 2, :W // 2]
        top = np.concatenate([q, q[:, ::-1]], axis=1)
        return np.concatenate([top, top[::-1]], axis=0)
    small = arr[::2, ::2]
    out = np.concatenate([np.concatenate([small, small[:, ::-1]], 1),
                          np.concatenate([small[::-1], small[::-1, ::-1]], 1)], 0)
    return out[:H, :W]

SKULL_ART = """
......XXXXXXXX......
....XXXXXXXXXXXX....
...XXXXXXXXXXXXXX...
..XXXXXXXXXXXXXXXX..
.XXXXXXXXXXXXXXXXXX.
.XXXXXXXXXXXXXXXXXX.
XXXXXXXXXXXXXXXXXXXX
XXX.....XXXX.....XXX
XX..RR..XXXX..RR..XX
XX..RR..XXXX..RR..XX
XX.....XXXXXX.....XX
XXX...XXXXXXXX...XXX
.XXXXXXXXX.XXXXXXXX.
..XXXXXXX...XXXXXX..
...XXXXXX.X.XXXXX...
....XXXXXXXXXXXX....
""".strip().splitlines()
JAW_ART = """
....X.XX.XX.XX.X....
....XXXXXXXXXXXX....
.....X.X.X.X.X.X....
.....XXXXXXXXXXX....
......XXXXXXXXX.....
""".strip().splitlines()

class Intro:
    """MONITORING -> OVERLOAD : infection glitch, HACKED + tête de mort, pluie matrix, explosion."""
    LONG = (1.9, 1.7, 1.8, 0.7)
    SHORT = (0.5, 0.7, 0.6, 0.5)
    CW, CH = 10, 11

    def __init__(self, rave):
        self.r = rave
        self.bone = np.array([[c == 'X' for c in row] for row in SKULL_ART], bool)
        self.eyes = np.array([[c == 'R' for c in row] for row in SKULL_ART], bool)
        self.jaw = np.array([[c == 'X' for c in row] for row in JAW_ART], bool)
        self.f_hack = ImageFont.truetype(F_BIG, 60)
        try: self.f_hack.set_variation_by_axes([900])
        except Exception: pass
        self.f_over = ImageFont.truetype(F_BIG, 44)
        try: self.f_over.set_variation_by_axes([900])
        except Exception: pass
        self.f_small = ImageFont.truetype(F_MONO, 12)
        self.f_rain = ImageFont.truetype(F_JP, 10)
        self.ncol = W // self.CW
        self.t0 = None
        self.d = self.LONG

    def start(self, now, short=False):
        self.t0, self.d = now, (self.SHORT if short else self.LONG)
        self.rain = np.zeros((H, W), np.float32)
        self.cy = np.random.uniform(-H / self.CH, 0, self.ncol)
        self.last = self.cy.astype(int) - 1
        self.cs = np.random.uniform(0.45, 1.3, self.ncol)
        self.hexes = []
        LOG.info('intro OVERLOAD %s', 'courte' if short else 'complète')

    # ------------------------------------------------------------ pièces
    def skull(self, arr, cx, top, scale, kick, col, eye_col, alpha=1.0):
        gap = int(kick * 2.2) * scale // 2 + (scale if kick > 0.5 else 0)
        rows = self.bone.shape[0] + self.jaw.shape[0]
        g = np.zeros((rows * scale + gap, 20 * scale), np.float32)
        e = np.zeros_like(g)
        k = np.ones((scale, scale), np.float32)
        hb = self.bone.shape[0] * scale
        g[:hb] = np.kron(self.bone, k)
        e[:hb] = np.kron(self.eyes, k)
        g[hb + gap:hb + gap + self.jaw.shape[0] * scale] = np.kron(self.jaw, k)
        x0, y0 = int(cx - g.shape[1] / 2), int(top)
        x1, y1 = min(W, x0 + g.shape[1]), min(H, y0 + g.shape[0])
        xa, ya = max(0, x0), max(0, y0)
        if x1 <= xa or y1 <= ya:
            return
        gs = g[ya - y0:y1 - y0, xa - x0:x1 - x0, None] * alpha
        es = e[ya - y0:y1 - y0, xa - x0:x1 - x0, None] * alpha
        reg = arr[ya:y1, xa:x1]
        reg *= (1 - gs) * (1 - es)
        reg += gs * np.array(col, np.float32) + es * np.array(eye_col, np.float32)

    def rain_step(self, speed):
        self.rain *= 0.875
        layer = Image.new('L', (W, H))
        heads = Image.new('L', (W, H))
        dl, dh = ImageDraw.Draw(layer), ImageDraw.Draw(heads)
        self.cy += self.cs * speed
        for c in range(self.ncol):
            y = int(self.cy[c])
            for row in range(max(self.last[c] + 1, y - 6), y + 1):
                if row < 0:
                    continue
                im, l, t = text_mask(random.choice(KATA), self.f_rain)
                pos = (c * self.CW + l, row * self.CH + t)
                dl.bitmap(pos, im, fill=230)
                if row == y:
                    dh.bitmap(pos, im, fill=255)
            self.last[c] = y
            if y * self.CH > H + 60:
                self.cy[c] = random.uniform(-8, 0)
                self.last[c] = int(self.cy[c]) - 1
                self.cs[c] = random.uniform(0.45, 1.3)
        # scintillement : quelques caractères de traîne changent
        for _ in range(12):
            c, row = random.randrange(self.ncol), random.randrange(H // self.CH)
            v = self.rain[row * self.CH + 5, c * self.CW + 4]
            if v > 0.15:
                im, l, t = text_mask(random.choice(KATA), self.f_rain)
                dl.bitmap((c * self.CW + l, row * self.CH + t), im, fill=int(255 * v))
        self.rain = np.maximum(self.rain, np.asarray(layer, np.float32) / 255)
        hd = np.asarray(heads, np.float32) / 255
        out = np.empty((H, W, 3), np.float32)
        out[:, :, 1] = np.minimum(255, self.rain * 270 + hd * 20)
        out[:, :, 0] = hd * 210 + self.rain ** 3 * 30
        out[:, :, 2] = hd * 210 + self.rain ** 3 * 70
        return out

    # ------------------------------------------------------------ frame
    def frame(self, now, a, beat, dt):
        r = self.r
        if self.t0 is None:
            self.start(now)
        t = now - self.t0
        dA, dB, dC, dD = self.d
        kick = env(now - a['last_beat'], 0.1)
        rng = r.rng
        if t < dA:                                    # 1. INFECTION du monitoring
            p = t / dA
            arr = r.calm_frame(now)
            arr[:, :, 1] *= 1 - 0.45 * p
            arr[:, :, 2] *= 1 - 0.35 * p
            arr[:, :, 0] = np.minimum(255, arr[:, :, 0] * (1 + 0.35 * p))
            r.glitch(arr, a, now, kick, force=0.03 + 0.55 * p * p + 0.35 * kick * p)
            if p > 0.45 and random.random() < 0.07 + 0.2 * kick:   # tête de mort subliminale
                self.skull(arr, W / 2, 30, 6, 0, (255, 30, 40), (255, 255, 255), 0.45)
            pil = Image.fromarray(np.clip(arr, 0, 255).astype(np.uint8))
            d = ImageDraw.Draw(pil)
            if beat or random.random() < 0.3 * p:
                self.hexes.append((random.randrange(0, W - 60), random.randrange(18, H - 30), now, hexs(4)))
            self.hexes = [h for h in self.hexes if now - h[2] < 0.5]
            for x, y, _, hx in self.hexes:
                d.rectangle((x - 2, y - 1, x + 52, y + 13), fill=(0, 0, 0))
                text(d, (x, y), f'0x{hx}', self.f_small, (255, 40, 60) if random.random() < 0.5 else (0, 255, 90))
            if p > 0.25 and int(now * 7) % 2:
                d.rectangle((0, H - 18, W, H), fill=(220, 0, 20))
                text(d, (W // 2, H - 9), '/!\\ INTRUSION DETECTED /!\\', self.f_small, (0, 0, 0), 'mm')
            out = np.asarray(pil, np.float32)
            if p > 0.85 and int(t * 20) % 2:
                out = 255 - out
            return out
        t -= dA
        if t < dB:                                    # 2. HACKED + tête de mort
            p = t / dB
            noise = rng.integers(0, 45, (H, W, 1)).astype(np.float32)
            arr = noise * np.array([1.0, 0.15, 0.2], np.float32)
            if p > 0.6:
                arr += self.rain_step(0.8) * (p - 0.6) / 0.4 * 0.6
            sx, sy = (rng.integers(-6, 7, 2) * kick).astype(int)
            hot = kick > 0.55
            self.skull(arr, W / 2 + sx, 18 + sy, 6, kick, (255, 255, 255) if hot else (255, 25, 45),
                       (0, 255, 80) if hot else (255, 255, 255))
            r.glitch(arr, a, now, kick, force=0.08 + 0.3 * kick)
            pil = Image.fromarray(np.clip(arr, 0, 255).astype(np.uint8))
            d = ImageDraw.Draw(pil)
            off = 3 + int(8 * kick)
            cy = 182 + random.randint(-2, 2)
            text(d, (W // 2 - off, cy), 'HACKED', self.f_hack, (255, 0, 50), 'mm')
            text(d, (W // 2 + off, cy), 'HACKED', self.f_hack, (0, 255, 255), 'mm')
            text(d, (W // 2, cy), 'HACKED', self.f_hack, (255, 255, 255), 'mm')
            msg = '> SYSTEM COMPROMISED' if p < 0.5 else '> ROOT ACCESS: GRANTED'
            n = int(((p if p < 0.5 else p - 0.5) / 0.5) * len(msg) * 1.6)
            text(d, (8, 220), msg[:n] + ('_' if int(now * 8) % 2 else ''), self.f_small, (0, 255, 90))
            out = np.asarray(pil, np.float32) * r.scan
            if beat and a['strength'] > 0.5 and random.random() < 0.6:
                out = 255 - out
            return out
        t -= dB
        if t < dC:                                    # 3. pluie MATRIX
            p = t / dC
            arr = self.rain_step(1.0 + 3.0 * p * p + 1.5 * kick)
            if kick > 0.5:
                arr *= 1.35
            self.skull(arr, W / 2, 18, 6, kick, (0, 120, 50), (0, 255, 120), 0.5 * (1 - p))
            r.glitch(arr, a, now, kick, force=0.05 + 0.5 * p ** 3)
            if p > 0.5:                               # OVERLOAD qui se déchiffre
                word = 'OVERLOAD'
                lock = int((p - 0.5) / 0.45 * len(word))
                shown = ''.join(ch if i < lock else random.choice('#$%&@01ABCDEF')
                                for i, ch in enumerate(word))
                pil = Image.fromarray(np.clip(arr, 0, 255).astype(np.uint8))
                d = ImageDraw.Draw(pil)
                d.rectangle((0, 98, W, 142), fill=(0, 0, 0))
                text(d, (W // 2 + 2, 121), shown, self.f_over, (0, 120, 40), 'mm')
                text(d, (W // 2, 120), shown, self.f_over, (200, 255, 210), 'mm')
                arr = np.asarray(pil, np.float32)
            return arr * r.scan
        t -= dC
        if t < dD:                                    # 4. EXPLOSION -> OVERLOAD
            q = t / dD
            if r.word != 'OVERLOAD':
                r.word, r.word_t = 'OVERLOAD', now
                r.flash, r.flash_col = 0.95, (255, 255, 255)
                r.tile = ('kaleido', now + 0.15)
            return r.active_frame(a, now, dt, beat, force=1.0 - q)
        self.t0 = None
        return None

class Rave:
    def __init__(self, audio: Audio, pool: ClipPool, sensors: Sensors | None, calm: CalmView):
        self.audio, self.pool, self.sensors, self.calm = audio, pool, sensors, calm
        self.f_mono = ImageFont.truetype(F_MONO, 10)
        self.f_mono_s = ImageFont.truetype(F_MONO, 9)
        self.f_hud = ImageFont.truetype(F_MONO, 13)
        self.big = {}
        for sz in (34, 44, 56, 72):
            f = ImageFont.truetype(F_BIG, sz)
            try: f.set_variation_by_axes([900])
            except Exception: pass
            self.big[sz] = f
        self.big_jp = {sz: ImageFont.truetype(F_JP, sz) for sz in (40, 56, 72)}
        self.term = Terminal()
        self.modes = ModeMachine()
        self.mode_trans = None        # ('in'|'out', t0)
        self.clip: Clip | None = None
        self.clip_t = 0.0
        self.clip_frame = 0
        self.clip_beats = 0
        self.clip_started = 0.0
        self.pal = 0
        self.seen_beats = 0
        self.word = None
        self.word_t = -9.0
        self.flash = 0.0
        self.invert_until = 0.0
        self.cut = None               # (type, t0, dur, old_graded, grid)
        self.last_graded = None
        self.peak_caps = np.zeros(32, np.float32)
        self.rng = np.random.default_rng()
        self.scan = np.where(np.arange(H) % 2 == 0, 1.0, 0.72).astype(np.float32)[:, None, None]
        self.prev = np.zeros((H, W, 3), np.uint8)
        self.frame_n = 0
        self.I = 0.0
        self.last_log = 0.0
        self.last_exit = -1e9
        self.tile = None
        self.flash_col = (255, 255, 255)
        self.intro = Intro(self)

    # ---------------------------------------------------------------- events
    def on_beat(self, a, now):
        I = self.I
        beats = a['beats']
        self.clip_beats += 1
        if I > 0.3:
            self.flash = min(0.45, max(self.flash, a['strength'] * (0.1 + 0.3 * I) + 0.15 * a['drop'] * I))
        if I > 0.7 and a['strength'] > 0.75 and random.random() < 0.12 + 0.3 * a['drop']:
            self.invert_until = now + 0.05
        if beats % (8 if I > 0.75 else 16) == 0:
            self.pal = (self.pal + 1) % len(PALETTES)
        hot_drop = a['drop'] > 0.5 and I > 0.7
        every = 2 if hot_drop else 4 if I > 0.75 else 8
        if I > 0.35 and (beats % every == 0 or (I > 0.8 and a['strength'] > 0.9 and random.random() < 0.2)):
            self.word, self.word_t = random.choice(WORDS), now
        # couleur du strobe : alterne blanc / néon en OVERLOAD
        self.flash_col = PALETTES[self.pal][1 + beats % 2] if I > 0.75 and beats % 3 else (255, 255, 255)
        # effets de beat OVERLOAD : miroir / mosaïque / stutter
        if I > 0.8 and random.random() < 0.28:
            self.tile = (random.choice(['mirror', 'quad', 'vmirror', 'kaleido', 'quad']),
                         now + random.uniform(0.12, 0.3))
        if I > 0.75 and self.clip is not None and random.random() < 0.35:
            self.clip_frame = (self.clip_frame + random.choice((-5, -4, -3, 3, 4, 6))) % len(self.clip.frames)
        cut_every = 2 if hot_drop else 4 if I > 0.75 else 8 if I > 0.4 else 16
        if self.clip_beats >= cut_every and now - self.clip_started > 0.9:
            self.next_clip(now, hard=hot_drop or random.random() < 0.15 + 0.3 * I)

    def next_clip(self, now, hard=False, transition=True):
        c = self.pool.take(hard, self.clip)
        if c is None:
            return
        if c is not self.clip and transition and self.last_graded is not None:
            kind = random.choice(['wipe', 'blocks', 'zoom', 'static', 'wipe', 'blocks'])
            dur = random.uniform(0.18, 0.3)
            grid = self.rng.random((H // 16 + 1, W // 16 + 1))
            self.cut = (kind, now, dur, self.last_graded.copy(), grid, random.choice((-1, 1)))
        self.clip, self.clip_t, self.clip_frame = c, 0.0, 0
        self.clip_beats, self.clip_started = 0, now

    # ---------------------------------------------------------------- layers
    def base(self, a, now, dt):
        if self.clip is None:
            self.next_clip(now, transition=False)
        c = self.clip
        if c is None:
            n = self.rng.integers(0, 60, (H, W, 1), dtype=np.uint8)
            return np.repeat(n, 3, axis=2), 0.0
        speed = min(2.0, max(0.8, a['bpm'] / 120)) if a['bpm'] else 1.0
        speed *= (0.8 + 0.7 * self.I + 0.4 * a['bass'] * self.I) if not a['silent'] else 0.6
        self.clip_t += dt * speed
        while self.clip_t >= c.durations[self.clip_frame]:
            self.clip_t -= c.durations[self.clip_frame]
            self.clip_frame = (self.clip_frame + 1) % len(c.frames)
        img = c.frames[self.clip_frame]
        kick = env(now - a['last_beat'], 0.12) * (0.5 + 0.5 * a['strength'])
        z = 1.0 + (0.05 + 0.2 * self.I) * kick + 0.06 * a['drop'] * self.I
        if z > 1.01:
            cw, ch = int(W / z), int(H / z)
            j = kick * self.I
            x0 = (W - cw) // 2 + int(self.rng.integers(-6, 7) * j)
            y0 = (H - ch) // 2 + int(self.rng.integers(-5, 6) * j)
            x0, y0 = max(0, min(W - cw, x0)), max(0, min(H - ch, y0))
            img = np.asarray(Image.fromarray(img[y0:y0 + ch, x0:x0 + cw]).resize(SIZE, Image.Resampling.BILINEAR))
        return img, kick

    def grade(self, img, a):
        f = img.astype(np.float32)
        lum = f[:, :, 0] * 0.3 + f[:, :, 1] * 0.59 + f[:, :, 2] * 0.11
        lum = np.clip((lum - 128) * 1.35 + 128, 0, 255).astype(np.uint8)
        duo = LUTS[self.pal][lum]
        mix = 0.35 + 0.3 * self.I + 0.2 * a['drop'] * self.I
        out = f * (1 - mix) + duo * mix
        g = out.mean(axis=2, keepdims=True)
        return np.clip(g + (out - g) * (1.15 + 0.25 * self.I), 0, 255)

    def apply_cut(self, arr, now):
        kind, t0, dur, old, grid, sgn = self.cut
        p = (now - t0) / dur
        if p >= 1:
            self.cut = None
            return arr, 0.0
        if kind == 'wipe':
            out = old.copy()
            for y in range(0, H, 8):
                edge = int(p * (W + 60)) - 30 + random.randint(-30, 30)
                edge = max(0, min(W, edge))
                if sgn > 0:
                    out[y:y + 8, :edge] = arr[y:y + 8, :edge]
                    out[y:y + 8, max(0, edge - 3):edge] = 255
                else:
                    out[y:y + 8, W - edge:] = arr[y:y + 8, W - edge:]
                    out[y:y + 8, W - edge:min(W, W - edge + 3)] = 255
            return out, 0.4
        if kind == 'blocks':
            m = np.kron(grid < p, np.ones((16, 16)))[:H, :W, None].astype(np.float32)
            out = arr * m + old * (1 - m)
            inv = np.kron((grid > p - 0.15) & (grid < p), np.ones((16, 16)))[:H, :W].astype(bool)
            out[inv] = 255 - out[inv]
            return out, 0.5
        if kind == 'zoom':
            z = 1.0 + 0.7 * (1 - p)
            cw, ch = int(W / z), int(H / z)
            x0, y0 = (W - cw) // 2, (H - ch) // 2
            im = Image.fromarray(np.clip(arr, 0, 255).astype(np.uint8)[y0:y0 + ch, x0:x0 + cw]).resize(SIZE)
            self.flash = max(self.flash, 0.6 * (1 - p))
            return np.asarray(im, np.float32), 0.6
        return arr, 1.0     # static -> glitch max

    def spectrum(self, draw, a):
        spec = np.minimum(1.0, a['spectrum'] * 1.5) ** 0.8
        self.peak_caps = np.maximum(spec, self.peak_caps - 0.025)
        p = PALETTES[self.pal]
        bw = W / 32
        base = H - 2
        maxh = 40 + 30 * self.I + 25 * a['drop'] * self.I
        for i in range(32):
            h = int(float(spec[i]) * maxh)
            x0, x1 = int(i * bw) + 1, int((i + 1) * bw) - 2
            t = i / 31
            col = tuple(int(p[2][k] * (1 - t) + p[1][k] * t) for k in range(3))
            for yy in range(base - h, base, 3):
                draw.rectangle((x0, yy, x1, yy + 1), fill=col)
            cy = base - int(self.peak_caps[i] * maxh) - 3
            draw.rectangle((x0, cy, x1, cy + 1), fill=(255, 255, 255))

    def big_word(self, draw, now, word=None, age=None, dur=0.55):
        w = word or self.word
        age = (now - self.word_t) if age is None else age
        if not w or age > dur:
            return
        jp = any(ord(ch) > 0x2000 for ch in w)
        fonts = self.big_jp if jp else self.big
        fits = [s for s in fonts if fonts[s].getlength(w) < W - 16]
        f = fonts[max(fits) if fits else min(fonts)]
        cx = W // 2 + random.randint(-4, 4)
        cy = H // 2 - 10 + random.randint(-3, 3)
        off = int(6 * (1 - age / dur)) + 2
        text(draw, (cx - off, cy), w, f, (255, 0, 60), 'mm')
        text(draw, (cx + off, cy), w, f, (0, 255, 255), 'mm')
        text(draw, (cx, cy), w, f, (255, 255, 255), 'mm')

    def hud(self, draw, a, now):
        acc = PALETTES[self.pal][3]
        draw.rectangle((0, 0, W, 15), fill=(0, 0, 0))
        jx = random.randint(-1, 1) if a['bass'] > 0.8 and self.I > 0.5 else 0
        text(draw, (3 + jx, 1), datetime.datetime.now().strftime('%H:%M:%S'), self.f_hud, (255, 255, 255))
        bpm = f'{a["bpm"]:3.0f}BPM' if a['bpm'] and not a['silent'] else '---BPM'
        text(draw, (72 + jx, 1), bpm, self.f_hud, acc)
        sv = self.sensors.values if self.sensors else {}
        x = 140
        for label, k in (('CPU', 'cpu:temp'), ('GPU', 'gpu:primary:temp'), ('6950', 'gpu:amd:1:temp')):
            v = sv.get(k)
            col = (150, 150, 160) if v is None else (255, 40, 60) if v >= 85 else (255, 200, 0) if v >= 70 else (0, 255, 200)
            text(draw, (x + jx, 1), label, self.f_mono_s, (170, 170, 190))
            lw = self.f_mono_s.getlength(label) + 2
            text(draw, (x + lw + jx, 1), ('--' if v is None else f'{v:.0f}') + '°', self.f_hud, col)
            x += lw + 34
        # jauge de chaleur (pas de volume) sous la barre
        hw = int(W * a['heat'])
        draw.rectangle((0, 15, hw, 16), fill=(255, 40, 60) if a['heat'] > 0.75 else acc)
        if self.clip is not None:
            text(draw, (W - 4, 19), f'SRC::{self.clip.label[:28].upper()}', self.f_mono_s, (200, 200, 220), 'ra')
        if a['drop'] > 0.55 and self.I > 0.6 and int(now * 6) % 2:
            text(draw, (W - 4, 205), '!! DROP MODE !!', self.f_mono, (255, 30, 60), 'ra')
        p = PALETTES[self.pal][1]
        L = 14 + int(10 * a['bass'] * self.I)
        for (x0, y0, sx, sy) in ((1, 18, 1, 1), (W - 2, 18, -1, 1), (1, H - 2, 1, -1), (W - 2, H - 2, -1, -1)):
            draw.line((x0, y0, x0 + sx * L, y0), fill=p, width=2)
            draw.line((x0, y0, x0, y0 + sy * L), fill=p, width=2)

    def glitch(self, arr, a, now, kick, force=0.0):
        rng = self.rng
        hat = env(now - a['last_hat'], 0.07)
        power = min(1.0, (0.35 * kick + 0.35 * hat + 0.5 * a['drop'] + 0.15 * a['high']) * (0.25 + 0.75 * self.I)
                    + 0.18 * self.I * self.I)
        power = max(power, force)
        if a['silent'] and force == 0:
            power = 0.1 if random.random() < 0.06 else 0.02
        k = int(1 + 9 * power + 4 * kick * self.I)
        if k > 1:
            arr[:, :, 0] = np.roll(arr[:, :, 0], k, axis=1)
            arr[:, :, 2] = np.roll(arr[:, :, 2], -k, axis=1)
            if power > 0.6:
                arr[:, :, 1] = np.roll(arr[:, :, 1], int(k / 2), axis=0)
        for _ in range(int(power * 10 + (6 if force > 0.9 else 0))):
            y = int(rng.integers(0, H - 4))
            hh = int(rng.integers(2, 4 + int(28 * power)))
            dx = int(rng.integers(-60, 61) * power)
            arr[y:y + hh] = np.roll(arr[y:y + hh], dx, axis=1)
        for _ in range(int(power * 6) + (8 if force > 0.9 else 0)):
            bw, bh = int(rng.integers(10, 90)), int(rng.integers(4, 30))
            x, y = int(rng.integers(0, W - bw)), int(rng.integers(0, H - bh))
            r = rng.random()
            if r < 0.4:
                sx, sy = int(rng.integers(0, W - bw)), int(rng.integers(0, H - bh))
                arr[y:y + bh, x:x + bw] = arr[sy:sy + bh, sx:sx + bw]
            elif r < 0.6:
                arr[y:y + bh, x:x + bw] = self.prev[y:y + bh, x:x + bw]
            elif r < 0.8:
                arr[y:y + bh, x:x + bw] = 255 - arr[y:y + bh, x:x + bw]
            else:
                c = PALETTES[self.pal][rng.integers(1, 3)]
                arr[y:y + bh, x:x + bw] = arr[y:y + bh, x:x + bw] * 0.3 + np.array(c) * 0.7
        if force > 0.9 or power > 0.85:
            noise = rng.integers(0, 255, (H, W, 1)).astype(np.float32)
            m = 0.5 if force > 0.9 else 0.18
            arr *= (1 - m); arr += noise * m
        return power

    # ---------------------------------------------------------------- frames
    def active_frame(self, a, now, dt, beat, force=0.0):
        img, kick = self.base(a, now, dt)
        arr = self.grade(img, a)
        cut_force = 0.0
        if self.cut is not None:
            arr, cut_force = self.apply_cut(arr, now)
        self.last_graded = arr.copy()
        if self.tile is not None:
            kind, until = self.tile
            if now < until:
                arr = tile_fx(arr, kind)
            else:
                self.tile = None
        if kick * self.I > 0.15:
            arr = np.roll(arr, (int(self.rng.integers(-6, 7) * kick * self.I),
                                int(self.rng.integers(-9, 10) * kick * self.I)), axis=(0, 1))
        pil = Image.fromarray(np.clip(arr, 0, 255).astype(np.uint8))
        d = ImageDraw.Draw(pil)
        self.spectrum(d, a)
        self.big_word(d, now)
        arr = np.asarray(pil, dtype=np.float32).copy()
        self.glitch(arr, a, now, kick, max(force, cut_force))
        self.term.update(now, self.I, beat, a['drop'])
        self.term.draw(arr, now, self.I, kick, PALETTES[self.pal][3], a['drop'] > 0.5)
        arr *= self.scan
        band = int((now * 60) % (H + 40)) - 20
        lo, hi = max(0, band), min(H, band + 10)
        if hi > lo:
            arr[lo:hi] *= 1.25
        if self.flash > 0.02:
            arr = arr * (1 - self.flash) + np.array(self.flash_col, np.float32) * self.flash
            self.flash *= 0.45
        out = np.clip(arr, 0, 255).astype(np.uint8)
        if now < self.invert_until:
            out = 255 - out
        self.prev = out
        pil = Image.fromarray(out)
        self.hud(ImageDraw.Draw(pil), a, now)
        return np.asarray(pil, dtype=np.float32)

    def calm_frame(self, now):
        sv = self.sensors.values if self.sensors else {}
        return self.calm.render(now, sv).astype(np.float32)

    def render(self, now, dt):
        a = self.audio.snapshot(now)
        target = clip01((a['heat'] - 0.4) / 0.45)
        self.I += (target - self.I) * min(1.0, dt / 1.5)
        beat = False
        if a['beats'] != self.seen_beats:
            beat = a['beats'] - self.seen_beats < 4 and self.frame_n > 0
            self.seen_beats = a['beats']
        change = self.modes.update(now, a['heat'], a['silent_for'])
        if change:
            self.mode_trans = (change, now)
            LOG.info('mode -> %s (chaleur %.2f)', 'OVERLOAD' if change == 'in' else 'MONITORING', a['heat'])
            if change == 'in':
                self.next_clip(now, hard=True, transition=False)
                self.intro.start(now, short=now - self.last_exit < REENTRY_SHORT)
            else:
                self.last_exit = now
        if now - self.last_log > 30:
            self.last_log = now
            LOG.info('mode=%s chaleur=%.2f (brute %.2f) I=%.2f [dens %.2f flat %.2f dyn %.2f loud %.2f] bpm=%.0f',
                     self.modes.mode, a['heat'], a['heat_raw'], self.I, *a['parts'], a['bpm'])
        tr = self.mode_trans
        out = None
        if tr is not None:
            kind, t0 = tr
            p = now - t0
            if kind == 'in':           # MONITORING -> OVERLOAD : infection, HACKED, matrix
                out = self.intro.frame(now, a, beat, dt)
                if out is None:
                    self.mode_trans = None
            else:                      # OVERLOAD -> MONITORING : signal perdu, extinction CRT
                if p < 0.5:
                    arr = self.active_frame(a, now, dt, False, force=p * 2)
                    arr *= 1 - 0.5 * p
                    pil = Image.fromarray(np.clip(arr, 0, 255).astype(np.uint8))
                    if int(p * 12) % 2 == 0:
                        self.big_word(ImageDraw.Draw(pil), now, 'SIGNAL LOST', 0.2)
                    out = np.asarray(pil, np.float32)
                elif p < 0.75:
                    arr = self.active_frame(a, now, dt, False, force=1.0)
                    out = crt(arr, (p - 0.5) / 0.25)
                elif p < 1.0:
                    out = np.zeros((H, W, 3), np.float32)
                    if p < 0.8:
                        out[H // 2 - 1:H // 2 + 1, W // 2 - 2:W // 2 + 2] = 255
                elif p < 1.35:
                    arr = self.calm_frame(now)
                    out = crt(arr, 1 - (p - 1.0) / 0.35)
                elif p < 1.8:
                    arr = self.calm_frame(now)
                    self.glitch(arr, a, now, 0.0, force=0.5 * (1 - (p - 1.35) / 0.45))
                    out = arr
                else:
                    self.mode_trans = None
        if out is None:
            if self.modes.mode == 'calm':
                out = self.calm_frame(now)
            else:
                if beat:
                    self.on_beat(a, now)
                if not a['silent'] and now - self.clip_started > 10:
                    self.next_clip(now, hard=random.random() < 0.3)
                out = self.active_frame(a, now, dt, beat)
        self.frame_n += 1
        return np.clip(out, 0, 255).astype(np.uint8)

# --------------------------------------------------------------------------

class Sender(threading.Thread):
    def __init__(self, bus, encode):
        super().__init__(daemon=True, name='sender')
        self.bus, self.encode = bus, encode
        self.q: queue.Queue = queue.Queue(maxsize=1)
        self.sent = 0
        self.errors = 0
        self.fatal: Exception | None = None

    def submit(self, arr):
        try:
            self.q.put_nowait(arr)
        except queue.Full:
            try: self.q.get_nowait()
            except queue.Empty: pass
            self.q.put_nowait(arr)

    def run(self):
        from trcc.core.commands.device import SendFrame
        while not STOP.is_set():
            try:
                arr = self.q.get(timeout=0.5)
            except queue.Empty:
                continue
            try:
                r = self.bus.dispatch(SendFrame(key=KEY, data=self.encode(arr)))
                if not r.ok:
                    raise RuntimeError(r.message)
                self.sent += 1
                self.errors = 0
                if self.sent in (1, 20, 200, 2000):
                    LOG.info('frame %d envoyée', self.sent)
            except Exception as e:
                self.errors += 1
                LOG.error('SendFrame erreur #%d: %s', self.errors, e)
                if self.errors >= 10:
                    self.fatal = e
                    STOP.set()
                time.sleep(0.4)

def analyze(paths):
    """Passe des fichiers audio dans le vrai détecteur (temps simulé)."""
    for path in paths:
        raw = subprocess.run(['ffmpeg', '-v', 'error', '-i', path, '-ac', '1', '-ar', '48000', '-f', 'f32le', '-'],
                             capture_output=True, check=True).stdout
        x = np.frombuffer(raw, np.float32)
        au, mm = Audio(), ModeMachine()
        hop = Audio.HOP
        t, line, changes = 0.0, [], []
        for i in range(0, len(x) - hop, hop):
            t = i / 48000
            au.process(x[i:i + hop], now=t)
            if i % (48000 * 5) < hop:
                line.append(f'{au.heat:.2f}')
            c = mm.update(t, au.heat, t - au.last_sound)
            if c: changes.append(f'{t:.0f}s:{"ACTIF" if c == "in" else "CALME"}')
        print(f'{Path(path).stem[:26]:26s} chaleur/5s: {" ".join(line[:24])}')
        print(f'{"":26s} bascules: {", ".join(changes) or "aucune"}  (final {mm.mode})')

def render_offline(audio_path, out_base, fmt='both', fps=FPS, outro=False):
    """Rendu hors ligne image par image, synchronisé à un fichier audio, vidéo HD + son."""
    raw = subprocess.run(['ffmpeg', '-v', 'error', '-i', audio_path, '-ac', '1', '-ar', '48000',
                          '-f', 'f32le', '-'], capture_output=True, check=True).stdout
    x = np.frombuffer(raw, np.float32)
    total = len(x) / 48000
    LOG.info('rendu hors ligne de %s (%.1f s)', audio_path, total)
    if outro:   # vidéo : fin nerveuse, SIGNAL LOST 2 s après la fin de la musique
        global SILENCE_HOLD
        SILENCE_HOLD = 2.0
    audio = Audio()
    pool = ClipPool(size=10); pool.start()
    calm = CalmView(); calm.t0 = 0.0
    sensors = None
    try:
        from trcc.proxy import AppProxy
        sensors = Sensors(AppProxy(timeout=7), calm); sensors.start()
        time.sleep(2.0)
    except Exception as e:
        LOG.warning('capteurs indisponibles pour le rendu: %s', e)
    t0 = time.monotonic()
    while len(pool.ready) < 8 and time.monotonic() - t0 < 60:
        time.sleep(0.2)
    rave = Rave(audio, pool, sensors, calm)
    outs = []
    # 4:3 : agrandissement entier x6 (pixels nets, scanlines intactes)
    if fmt in ('both', '4x3'):
        outs.append((f'{out_base}-4x3.mp4', 'scale=1920:1440:flags=neighbor,fps=30'))
    # vertical 9:16 (Reels/TikTok/Shorts) : écran x3 au centre sur fond flou
    if fmt in ('both', 'vertical'):
        outs.append((f'{out_base}-vertical.mp4',
                     'split[a][b];[a]scale=2560:1920:flags=bilinear,crop=1080:1920,boxblur=24:3,'
                     'eq=brightness=-0.18:saturation=1.3[bg];[b]scale=960:720:flags=neighbor,'
                     'pad=976:736:8:8:color=0x101018[fg];[bg][fg]overlay=(W-w)/2:(H-h)/2,fps=30'))
    procs = []
    for path, vf in outs:
        cmd = ['ffmpeg', '-y', '-v', 'error', '-f', 'rawvideo', '-pix_fmt', 'rgb24', '-s', f'{W}x{H}',
               '-r', str(fps), '-i', 'pipe:0', '-i', audio_path, '-filter_complex', f'[0:v]{vf}[v]',
               '-map', '[v]', '-map', '1:a', '-c:v', 'libx264', '-preset', 'slow', '-crf', '15',
               '-pix_fmt', 'yuv420p', '-c:a', 'aac', '-b:a', '320k', '-shortest',
               '-movflags', '+faststart', path]
        procs.append(subprocess.Popen(cmd, stdin=subprocess.PIPE))
    hop = Audio.HOP
    pos = 0
    nframes = int(total * fps)
    for k in range(nframes):
        t = k / fps
        while pos + hop <= len(x) and (pos + hop) / 48000 <= t:
            audio.process(x[pos:pos + hop], now=(pos + hop) / 48000)
            pos += hop
        img = rave.render(t, 1 / fps)
        buf = img.tobytes()
        for pr in procs:
            pr.stdin.write(buf)
        if k % fps == 0:
            print(f'PROGRESS {100 * k // max(1, nframes)}', flush=True)
        if k % (fps * 10) == 0:
            LOG.info('  %3.0f s / %.0f s  mode=%s chaleur=%.2f', t, total, rave.modes.mode, audio.heat)
    for pr in procs:
        pr.stdin.close(); pr.wait()
    print('PROGRESS 100', flush=True)
    STOP.set()
    for path, _ in outs:
        LOG.info('vidéo : %s', path)

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--preview', type=float, default=0, help='rend N secondes en PNG/MP4 sans LCD')
    ap.add_argument('--force', choices=['calm', 'active', 'in', 'out'], help='preview : forcer un mode/transition')
    ap.add_argument('--fps', type=int, default=FPS)
    ap.add_argument('--audio-file', help='preview : audio depuis un fichier (temps réel)')
    ap.add_argument('--audio-start', type=float, default=0.0)
    ap.add_argument('--analyze', nargs='+', help='analyse hors-ligne de fichiers audio')
    ap.add_argument('--render', metavar='AUDIO', help='rendu vidéo hors ligne synchronisé à ce morceau')
    ap.add_argument('--out', default=str(HERE / 'demo' / 'demo'), help='préfixe des vidéos (--render)')
    ap.add_argument('--format', choices=['both', '4x3', 'vertical'], default='both')
    ap.add_argument('--outro', action='store_true', help='--render : SIGNAL LOST 2 s après la musique')
    a = ap.parse_args()
    logging.basicConfig(level=logging.INFO, format='%(asctime)s %(levelname)s %(message)s')
    if a.analyze:
        analyze(a.analyze); return
    if a.render:
        Path(a.out).parent.mkdir(parents=True, exist_ok=True)
        render_offline(a.render, a.out, a.format, outro=a.outro); return
    signal.signal(signal.SIGTERM, lambda *_: STOP.set())
    signal.signal(signal.SIGINT, lambda *_: STOP.set())

    audio = Audio(a.audio_file, a.audio_start); audio.start()
    pool = ClipPool(); pool.start()
    calm = CalmView()
    sensors = sender = None
    from trcc.proxy import AppProxy
    if not a.preview:
        from trcc.core.commands.device import LcdSnapshot
        bus = AppProxy(timeout=7)
        snap = bus.dispatch(LcdSnapshot(key=KEY))
        if snap.orientation != 0:
            raise RuntimeError(f'orientation LCD {snap.orientation}, la V7 exige 0°')
        encode, angle, prof = make_encoder()
        LOG.info('LCD %s, rotation fil %d°', KEY, angle)
        sender = Sender(bus, encode); sender.start()
    try:
        sensors = Sensors(AppProxy(timeout=7), calm); sensors.start()
    except Exception as e:
        LOG.warning('capteurs: %s', e)
    t0 = time.monotonic()
    while not pool.ready and time.monotonic() - t0 < 20:
        time.sleep(0.1)
    rave = Rave(audio, pool, sensors, calm)
    period = 1 / a.fps
    started = last = time.monotonic()
    if a.force:
        if a.force in ('active', 'out'):
            rave.modes.mode = 'active'; rave.I = 1.0
        if a.force in ('in', 'out'):
            rave.mode_trans = (a.force, started + 0.5)
            rave.modes.mode = 'calm' if a.force == 'out' else 'active'
        rave.modes.update = lambda *x: None
    due = started
    frames = []
    rt = collections.deque(maxlen=200)
    while not STOP.is_set():
        now = time.monotonic()
        dt = min(0.2, now - last); last = now
        t = time.monotonic()
        if a.force in ('active',):
            rave.I = 1.0
        img = rave.render(now, dt)
        rt.append(time.monotonic() - t)
        if a.preview:
            frames.append(img)
            if now - started >= a.preview:
                break
        else:
            sender.submit(img)
        if rave.frame_n % 1200 == 0:
            LOG.info('rendu moyen %.1f ms, envoyées=%d', 1000 * sum(rt) / len(rt), sender.sent if sender else 0)
        due += period
        now = time.monotonic()
        if now > due: due = now
        else: time.sleep(due - now)
    STOP.set()
    if a.preview:
        out = HERE / 'preview'
        out.mkdir(exist_ok=True)
        for f in out.glob('frame-*.png'): f.unlink()
        raw = b''.join(f.tobytes() for f in frames)
        subprocess.run(['ffmpeg', '-y', '-hide_banner', '-loglevel', 'error', '-f', 'rawvideo', '-pix_fmt', 'rgb24',
                        '-s', f'{W}x{H}', '-r', str(a.fps), '-i', 'pipe:0', '-vf', 'scale=640:480:flags=neighbor',
                        '-pix_fmt', 'yuv420p', str(out / 'preview.mp4')], input=raw, check=True)
        LOG.info('preview: %d frames, rendu moyen %.1f ms, max %.1f ms -> %s', len(frames),
                 1000 * sum(rt) / len(rt), 1000 * max(rt), out)
    if sender and sender.fatal:
        raise SystemExit(f'LCD perdu: {sender.fatal}')

if __name__ == '__main__':
    main()
