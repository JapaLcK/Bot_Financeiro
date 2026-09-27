"""Trilha de 15s do vídeo, sintetizada do zero (sem sample nem música de terceiros).

120 BPM (tempo = 0,5s), então todo corte de cena do index.html cai num tempo.
Os efeitos usam os mesmos instantes das animações; se mudar um lá, mude aqui.
    python3 marketing/video-15s/soundtrack.py   → marketing/video-15s/out/soundtrack.wav
"""
import wave
from pathlib import Path

import numpy as np

SR, DUR = 48000, 15.0
N = int(SR * DUR)
rng = np.random.default_rng(11)
L, R = np.zeros(N), np.zeros(N)
DL, DR = np.zeros(N), np.zeros(N)  # pad, baixo e arpejo: abaixam a cada bumbo (sidechain)
send = np.zeros(N)  # barramento do reverb


def env(n, a=0.002, d=0.2):
    t = np.arange(n) / SR
    return np.minimum(1, t / a) * np.exp(-t / d)


def put(t0, sig, gain=1.0, pan=0.0, rev=0.0, ducked=False):
    i = int(t0 * SR)
    if i >= N:
        return
    sig = sig[: N - i] * gain
    left, right = (DL, DR) if ducked else (L, R)
    left[i:i + len(sig)] += sig * np.sqrt(1 - pan)
    right[i:i + len(sig)] += sig * np.sqrt(1 + pan)
    send[i:i + len(sig)] += sig * rev


def fft_filter(x, lo=None, hi=None):
    X = np.fft.rfft(x)
    f = np.fft.rfftfreq(len(x), 1 / SR)
    h = np.ones_like(f)
    if hi:
        h /= np.sqrt(1 + (f / hi) ** 4)
    if lo:
        h *= (f / lo) ** 2 / np.sqrt(1 + (f / lo) ** 4)
    return np.fft.irfft(X * h, len(x))


def noise(dur):
    return rng.uniform(-1, 1, int(SR * dur))


def saw(freq, dur, detune=0.0):
    t = np.arange(int(SR * dur)) / SR
    out = np.zeros_like(t)
    for d in (-detune, 0, detune) if detune else (0,):
        ph = t * freq * (1 + d)
        out += 2 * (ph - np.floor(ph + 0.5))
    return out / (3 if detune else 1)


def sine(freq, dur, f_end=None):
    n = int(SR * dur)
    f = np.full(n, float(freq)) if f_end is None else np.geomspace(freq, f_end, n)
    return np.sin(2 * np.pi * np.cumsum(f) / SR)


def hz(midi):
    return 440 * 2 ** ((midi - 69) / 12)


# ------------------------------------------------------------------ bateria
def kick(big=False):
    n = int(SR * (0.7 if big else 0.38))
    t = np.arange(n) / SR
    f = 45 + (160 if big else 120) * np.exp(-t * 28)
    body = np.sin(2 * np.pi * np.cumsum(f) / SR) * np.exp(-t / (0.32 if big else 0.13))
    click = fft_filter(noise(n / SR), lo=2000) * np.exp(-t / 0.004) * 0.4
    return np.tanh((body + click) * 1.6)


def clap():
    s = fft_filter(noise(0.25), lo=900, hi=5000)
    e = env(len(s), 0.001, 0.07)
    for k in (0.008, 0.017):  # as três batidas do clap
        e[: int(k * SR)] *= 0.6
    return s * e


def hat(open_=False):
    s = fft_filter(noise(0.3 if open_ else 0.08), lo=7000)
    return s * env(len(s), 0.0005, 0.09 if open_ else 0.018)


beats = np.arange(0, DUR, 0.5)
duck = np.ones(N)  # sidechain: o pad e o baixo abaixam a cada bumbo
for b in beats:
    if 1.5 <= b < 7.0 or 7.5 <= b < 12.9:
        put(b, kick(), 0.9)
        i = int(b * SR)
        k = np.linspace(0, 1, int(0.22 * SR))
        duck[i:i + len(k)] = np.minimum(duck[i:i + len(k)], 0.35 + 0.65 * k ** 0.7)
    if (b % 1.0) == 0.5 and (2.0 <= b < 7.0 or 8.0 <= b < 12.9):
        put(b, clap(), 0.42, pan=0.05, rev=0.25)
for s in np.arange(1.5, 12.9, 0.125):
    dense = 7.5 <= s < 10.75
    if (s % 0.5) == 0.25 or (dense and (s % 0.25) != 0):
        put(s, hat(), 0.2 if (s % 0.5) == 0.25 else 0.11, pan=0.35)
for s in (5.0, 7.5, 11.0):
    put(s, hat(True), 0.2, pan=-0.2, rev=0.3)

# ------------------------------------------------------------------ harmonia
# Lá menor · Fá · Dó · Sol, um acorde por compasso (2s); no final, Dó maior aberto.
chords = [(57, [57, 60, 64]), (53, [57, 60, 65]), (48, [55, 60, 64]), (55, [55, 59, 62])]
for bar in range(8):
    t0 = bar * 2.0
    if t0 >= 13.0:
        break
    root, notes = chords[bar % 4]
    dur = min(2.0, 13.0 - t0)
    pad = sum(saw(hz(n), dur, detune=0.004) for n in notes)
    pad = fft_filter(pad, hi=1400 if t0 < 7.5 else 2600) * np.minimum(1, np.arange(len(pad)) / (0.3 * SR))
    put(t0, pad, 0.1, pan=-0.25, rev=0.35, ducked=True)
    put(t0, fft_filter(sum(saw(hz(n + 12), dur, 0.006) for n in notes), hi=2200), 0.028, pan=0.3, rev=0.4, ducked=True)
    if t0 + 2 > 1.5:  # baixo em colcheias a partir do primeiro bumbo
        for s in np.arange(max(t0, 1.5), t0 + dur, 0.25):
            b = fft_filter(saw(hz(root - 12), 0.24) + 0.5 * sine(hz(root - 24), 0.24), hi=520)
            put(s, b * env(len(b), 0.004, 0.12), 0.44 if s % 0.5 else 0.3, ducked=True)
    if 7.5 <= t0 or t0 == 6.0:  # arpejo em semicolcheias na cena do Open Finance
        seq = [notes[0] + 12, notes[1] + 12, notes[2] + 12, notes[1] + 24]
        for j, s in enumerate(np.arange(max(t0, 7.5), min(t0 + dur, 10.75), 0.125)):
            p = sine(hz(seq[j % 4]), 0.2) + 0.3 * sine(hz(seq[j % 4]) * 2, 0.2)
            put(s, p * env(len(p), 0.002, 0.06), 0.1, pan=0.4 * np.sin(j), rev=0.45, ducked=True)
for bar in (4.0, 5.0):  # arpejo leve no Organizar
    for j, s in enumerate(np.arange(bar + 1.0, bar + 2.0, 0.25)):
        n = [64, 67, 72, 76][j % 4]
        p = sine(hz(n), 0.25)
        put(s, p * env(len(p), 0.002, 0.08), 0.06, pan=-0.3, rev=0.5)

# acorde final (13.0 → fim): Dó maior com nona, abrindo o filtro
fin = sum(saw(hz(n), 2.0, 0.005) for n in (48, 55, 60, 64, 67, 74))
fin = fft_filter(fin, hi=3000) * np.exp(-np.arange(int(2.0 * SR)) / SR / 1.6)
put(13.0, fin, 0.075, rev=0.6)
put(13.0, sine(hz(36), 1.8) * env(int(1.8 * SR), 0.005, 0.9), 0.5)
bell = sum(sine(hz(n), 1.4) * (0.6 if i else 1) for i, n in enumerate((84, 91)))
put(14.15, bell * env(len(bell), 0.002, 0.45), 0.08, rev=0.6)

L += DL * duck
R += DR * duck


# ------------------------------------------------------------------ efeitos
def whoosh(dur, rising=True):
    s = noise(dur)
    n = len(s)
    out = np.zeros(n)
    seg = n // 12
    for k in range(12):  # varredura de banda em blocos
        c = 400 * 2 ** ((k if rising else 11 - k) / 2.2)
        out[k * seg:(k + 1) * seg] = fft_filter(s[k * seg:(k + 1) * seg], lo=c * 0.6, hi=c * 1.8)
    return out * np.sin(np.linspace(0, np.pi, n)) ** 2


def pop(f=700, f2=1150, d=0.09):
    s = sine(f, d, f2)
    return s * env(len(s), 0.001, 0.03)


# abertura: brilho do anel + orelhas
put(0.0, sine(500, 0.62, 1500) * np.linspace(0, 1, int(0.62 * SR)) ** 2 * 0.5, 0.18, rev=0.6)
put(0.0, fft_filter(noise(1.5), lo=3000) * np.linspace(0, 1, int(1.5 * SR)) ** 3, 0.08, rev=0.3)
for t in (0.34, 0.40, 0.48, 0.56):
    put(t, pop(520, 900, 0.07), 0.3, rev=0.3)
put(0.7, fin[: int(0.8 * SR)] * np.exp(-np.arange(int(0.8 * SR)) / SR / 0.3), 0.22, rev=0.7)
for t in (0.82, 0.94):
    put(t, kick() * 0.5, 0.5)

# transições (a varredura fica centrada no corte)
for t in (1.5, 5.0, 11.0):
    put(t - 0.3, whoosh(0.55), 0.55, pan=-0.3, rev=0.3)
put(12.72, whoosh(0.3), 0.6, rev=0.3)
put(10.72, whoosh(0.3, rising=False), 0.5, rev=0.3)

# chat
for t, me in ((1.95, 1), (2.47, 0), (2.85, 1), (3.2, 0), (3.5, 1), (4.05, 0)):
    put(t, pop(760, 1250) if me else pop(1050, 1500), 0.35, pan=0.3 if me else -0.3, rev=0.2)
for t in (2.22, 2.3, 2.38):
    put(t, pop(1800, 1900, 0.03), 0.07)
put(3.58, sine(2400, 0.45, 5200) * env(int(0.45 * SR), 0.05, 0.4), 0.06, rev=0.3)
put(3.95, pop(1300, 1900, 0.12), 0.25, rev=0.3)
put(4.25, pop(400, 950, 0.14), 0.4, rev=0.3)

# organizar: um toque por card
for i, t in enumerate((5.08, 5.18, 5.28, 5.38, 5.48)):
    put(t + 0.05, pop(300 + 60 * i, 200, 0.08), 0.35)

# riser até o drop do Open Finance
rs = 1.5
ris = whoosh(rs) * np.linspace(0, 1, int(rs * SR)) ** 2 * 1.5 + sine(200, rs, 1600) * np.linspace(0, 1, int(rs * SR)) ** 3 * 0.25
put(6.0, ris, 1.0, rev=0.4)
put(7.5, kick(big=True), 1.0)
crash = fft_filter(noise(2.2), lo=3500) * env(int(2.2 * SR), 0.001, 0.7)
put(7.5, crash, 0.22, rev=0.5)
put(7.5, sine(110, 1.0, 40) * env(int(SR), 0.005, 0.5), 0.5)

# open finance
for i in range(5):
    put(7.72 + i * 0.07, pop(500 + 90 * i, 800 + 90 * i, 0.08), 0.22, pan=-0.6 + 0.3 * i)
ticks = 8.4 + (np.linspace(0, 1, 38) ** 1.8) * 1.1  # desacelera junto com o contador
for t in ticks:
    put(t, pop(3000, 3100, 0.012), 0.12, pan=0.2)
for i, t in enumerate((9.05, 9.12, 9.19, 9.26, 9.33)):
    b = sine(hz(76 + (0, 2, 4, 7, 9)[i]), 0.5)
    put(t, b * env(len(b), 0.002, 0.18), 0.1, pan=-0.5 + 0.25 * i, rev=0.5)
for t in (9.7, 9.82, 9.94):
    put(t, pop(900, 1300, 0.06), 0.2)

# agentes: pancada no pouso de cada um
for i in range(7):
    t = 11.10 + 0.08 * i + 0.42
    put(t, kick() * 0.6, 0.35, pan=-0.6 + 0.2 * i)
for t in (11.85, 12.1, 12.35):
    d = sine(hz(88), 0.25) * env(int(0.25 * SR), 0.002, 0.08)
    put(t, d, 0.12, rev=0.4)
    put(t + 0.09, sine(hz(93), 0.35) * env(int(0.35 * SR), 0.002, 0.12), 0.12, rev=0.4)

# final
put(13.0, kick(big=True), 1.0)
put(13.0, crash, 0.2, rev=0.5)
put(13.45, fft_filter(noise(1.2), lo=6000) * env(int(1.2 * SR), 0.002, 0.35), 0.12, rev=0.4)

# ------------------------------------------------------------------ mix
ir_n = int(1.6 * SR)
ir = rng.uniform(-1, 1, ir_n) * np.exp(-np.arange(ir_n) / SR / 0.45)
wet = [np.fft.irfft(np.fft.rfft(fft_filter(send, hi=6000), N + ir_n) * np.fft.rfft(ir2, N + ir_n))[:N]
       for ir2 in (ir, np.roll(ir, 173))]
L += wet[0] * 0.06
R += wet[1] * 0.06
mix = np.stack([L, R], 1)
mix = np.tanh(mix / np.max(np.abs(mix)) * 1.4) / np.tanh(1.4)  # saturação suave para colar
mix[-int(0.35 * SR):] *= np.linspace(1, 0, int(0.35 * SR))[:, None] ** 2
mix *= 0.89

out = Path(__file__).parent / "out"
out.mkdir(exist_ok=True)
with wave.open(str(out / "soundtrack.wav"), "wb") as w:
    w.setnchannels(2)
    w.setsampwidth(2)
    w.setframerate(SR)
    w.writeframes((mix * 32767).astype("<i2").tobytes())
print("ok:", out / "soundtrack.wav")
