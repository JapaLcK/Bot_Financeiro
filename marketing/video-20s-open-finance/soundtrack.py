"""Trilha de 20s do anúncio, sintetizada do zero (sem sample nem música de terceiros).

120 BPM (tempo = 0,5s): as viradas de cena (3, 6, 13 e 16s) caem em tempo forte.
Os efeitos usam os mesmos instantes das animações em hook.js/connect.js/payoff.js/finale.js;
se mudar um lá, mude aqui. As primitivas de síntese moram em synth.py.
    python3 soundtrack.py   → out/soundtrack.wav
"""
import wave
from pathlib import Path

import numpy as np

from synth import (SR, bell, clap, click, env, fft_filter, hat, hz, impact, kick, noise, pop, ramp,
                   reverse_suck, rng, saw, sine, tick, whoosh)

DUR = 20.0
N = int(SR * DUR)
L, R = np.zeros(N), np.zeros(N)
DL, DR = np.zeros(N), np.zeros(N)  # pad, baixo e arpejo: abaixam a cada bumbo (sidechain)
send = np.zeros(N)  # barramento do reverb


def put(t0, sig, gain=1.0, pan=0.0, rev=0.0, ducked=False):
    i = int(t0 * SR)
    if i >= N or i < 0:
        return
    sig = sig[: N - i] * gain
    left, right = (DL, DR) if ducked else (L, R)
    left[i:i + len(sig)] += sig * np.sqrt(1 - pan)
    right[i:i + len(sig)] += sig * np.sqrt(1 + pan)
    send[i:i + len(sig)] += sig * rev


# ------------------------------------------------------------------ estrutura
ROOT_CHORDS = [(57, [57, 60, 64]), (53, [57, 60, 65]), (48, [55, 60, 64]), (55, [55, 59, 62])]  # Am F C G
GROOVE = [(1.0, 3.0, "sparse"), (3.0, 13.0, "full"), (16.0, 19.0, "full")]  # onde há bumbo
BREAK = (13.0, 16.0)

duck = np.ones(N)


def in_groove(b):
    return any(a <= b < z for a, z, _ in GROOVE)


for b in np.arange(0, DUR, 0.5):
    if in_groove(b) and (b >= 3.0 or (b % 1.0) == 0):
        put(b, kick(), 0.9)
        i = int(b * SR)
        k = np.linspace(0, 1, int(0.22 * SR))
        duck[i:i + len(k)] = np.minimum(duck[i:i + len(k)], 0.35 + 0.65 * k ** 0.7)
    if (b % 1.0) == 0.5 and ((3.5 <= b < 13.0) or (16.5 <= b < 19.0)):
        put(b, clap(), 0.4, pan=0.05, rev=0.25)
# respiração no meio: bumbo suave no 1 de cada compasso
for b in (13.0, 14.0, 15.0):
    put(b, kick() * 0.55, 0.45)
    i = int(b * SR); k = np.linspace(0, 1, int(0.25 * SR)); duck[i:i + len(k)] = np.minimum(duck[i:i + len(k)], 0.5 + 0.5 * k)

for s in np.arange(0.5, 19.0, 0.125):
    if BREAK[0] <= s < BREAK[1] and (s % 0.5) != 0.25:
        continue
    if s < 1.0 and (s % 0.25) != 0:
        continue
    dense = 6.0 <= s < 13.0 or 16.0 <= s < 19.0
    if (s % 0.5) == 0.25 or (dense and (s % 0.25) != 0):
        put(s, hat(), 0.18 if (s % 0.5) == 0.25 else 0.1, pan=0.35)
for s in (3.0, 5.0, 7.5, 9.5, 11.0, 12.5, 16.5, 18.0):
    put(s, hat(True), 0.18, pan=-0.2, rev=0.3)

# ------------------------------------------------------------------ harmonia: Am F C G, um acorde por compasso (2s)
for bar in range(10):
    t0 = bar * 2.0
    root, notes = ROOT_CHORDS[bar % 4]
    if t0 >= 19.0:
        break
    dur = min(2.0, 19.6 - t0)
    soft = BREAK[0] <= t0 < BREAK[1]
    pad = sum(saw(hz(n), dur, detune=0.004) for n in notes)
    pad = fft_filter(pad, hi=900 if t0 < 3.0 else 1500 if soft else 2600) * np.minimum(1, np.arange(len(pad)) / (0.3 * SR))
    put(t0, pad, 0.11 if soft else 0.1, pan=-0.25, rev=0.4, ducked=True)
    put(t0, fft_filter(sum(saw(hz(n + 12), dur, 0.006) for n in notes), hi=2200), 0.028, pan=0.3, rev=0.4, ducked=True)
    if 3.0 <= t0 < 13.0 or 16.0 <= t0 < 19.0:  # baixo em colcheias
        for s in np.arange(t0, t0 + dur, 0.25):
            bs = fft_filter(saw(hz(root - 12), 0.24) + 0.5 * sine(hz(root - 24), 0.24), hi=520)
            put(s, bs * env(len(bs), 0.004, 0.12), 0.42 if s % 0.5 else 0.3, ducked=True)
    # arpejo: pluck em semicolcheias (mais denso a partir do payoff; suave e espaçado no respiro)
    if t0 >= 4.0 and t0 < 19.0:
        seq = [notes[0] + 12, notes[1] + 12, notes[2] + 12, notes[1] + 24]
        step = 0.25 if soft else 0.125
        for j, s in enumerate(np.arange(t0, t0 + dur, step)):
            if not soft and t0 < 6.0 and j % 2:
                continue
            p = sine(hz(seq[j % 4]), 0.3) + 0.3 * sine(hz(seq[j % 4]) * 2, 0.3)
            put(s, p * env(len(p), 0.002, 0.14 if soft else 0.06), 0.085 if soft else 0.095, pan=0.4 * np.sin(j), rev=0.5, ducked=not soft)

# abertura: drone de tensão que sobe até o vórtice
drone = fft_filter(saw(hz(45), 3.2, 0.006) + saw(hz(52), 3.2, 0.006), hi=400)
put(0.0, drone * ramp(int(3.2 * SR), 1.5) * 0.25, 1.0, rev=0.3)

# ------------------------------------------------------------------ 0–3s: gancho
put(0.0, impact() * 0.55, 1.0, rev=0.5)
put(0.0, whoosh(0.7) * 0.6, 0.6, pan=-0.3, rev=0.3)
for t, g in ((0.08, .34), (0.28, .34), (0.49, .34), (0.86, .4)):  # palavras do título
    put(t, pop(520 + g * 400, 900 + g * 500, 0.08), 0.34, pan=0.2 * np.sin(t * 9), rev=0.3)
for t in (0.4, 0.8, 1.2, 1.6):  # cartões cruzando a câmera
    put(t, whoosh(0.35, rising=t % 0.8 < 0.4) * 0.5, 0.35, pan=np.sin(t * 7) * 0.7, rev=0.3)
put(1.0, sine(300, 2.0, 2400) * ramp(int(2 * SR), 3) * 0.3, 0.25, rev=0.4)  # riser
put(1.8, reverse_suck(1.25) * 0.8, 0.7, rev=0.3)  # tudo é puxado para o ponto
put(2.3, sine(1200, 0.7, 100) * env(int(.7 * SR), 0.01, .4) * .3, 0.22, rev=.4)

# ------------------------------------------------------------------ 3–6s: conecte
put(3.0, kick(big=True), 1.0)
put(3.0, impact(), 0.95, rev=0.4)
put(3.0, fft_filter(noise(2.0), lo=3500) * env(int(2 * SR), 0.001, 0.7), 0.2, rev=0.5)
put(2.95, whoosh(0.6) * 0.7, 0.55, rev=0.3)  # o círculo vira celular
for i in range(4):  # os heróis pousam nas linhas
    put(3.05 + .1 * i + .32, pop(420 + 70 * i, 700 + 70 * i, 0.09), 0.34, pan=-.3 + .2 * i, rev=.3)
put(3.3, fft_filter(noise(0.9), lo=5000) * env(int(.9 * SR), .15, .4), 0.1, rev=.5)  # brilho da UI
put(3.42, whoosh(0.4) * .5, .4, pan=-.4, rev=.3)
put(3.85, whoosh(0.5) * .4, .35, pan=.5, rev=.3)  # dedo chegando
put(4.42, click(), 0.7)  # toque
put(4.42, pop(380, 520, 0.07), 0.3)
put(4.45, sine(900, 0.35, 2600) * env(int(.35 * SR), .004, .25), .13, rev=.5)
put(4.5, whoosh(0.6) * .8, .6, rev=.4)  # o botão se abre em linhas
for i in range(6):  # as linhas chegam nas instituições e confirmam
    tn = 4.52 + .03 * i + .6 + .15
    put(tn, bell(81 + (0, 2, 4, 7, 9, 12)[i], 0.8, 0.3), 0.11, pan=-.6 + .24 * i, rev=.5)
    put(tn - .15, pop(700 + 60 * i, 1100 + 60 * i, .07), .22, pan=-.6 + .24 * i, rev=.3)
put(5.0, pop(520, 880, .1), .4, rev=.3)  # "É simples."
put(5.02, whoosh(.3) * .5, .4, rev=.3)
for t in np.linspace(5.25, 5.5, 5):
    put(t, tick(3200), .1, pan=.3)
put(5.52, reverse_suck(0.4), .65, rev=.3)  # as conexões voltam para dentro
for i in range(6):
    put(5.78 + .035 * i + .1, pop(500 + 40 * i, 760 + 40 * i, .06), .18, rev=.3)
put(5.5, sine(200, .5, 1800) * ramp(int(.5 * SR), 2) * .3, .25, rev=.4)

# ------------------------------------------------------------------ 6–13s: payoff
put(5.85, sine(150, .65, 1800) * ramp(int(.65 * SR), 2.4) * .35, 0.3, rev=.4)
put(5.85, whoosh(.7) * 1.1, .7, rev=.4)
put(6.0, kick(big=True), 1.0)
put(6.0, impact(), .9, rev=.4)
put(6.0, fft_filter(noise(2.2), lo=3500) * env(int(2.2 * SR), 0.001, 0.7), 0.2, rev=.5)
for i in range(5):  # as linhas entram
    put(6.0 + .07 * i + .05, pop(300 + 40 * i, 500 + 40 * i, .08), .2, pan=-.3, rev=.3)
for t in (6.42, 7.66, 9.12, 10.4, 11.75):  # títulos
    put(t, pop(620, 1000, .09), .32, rev=.3)
    put(t - .02, whoosh(.3) * .6, .3, rev=.25)
put(6.52, click(), .6)  # toque na linha
put(6.6, whoosh(.6) * .8, .55, pan=.3, rev=.3)  # a linha vira card
for i in range(5):  # as bolinhas voam para o anel
    put(6.68 + .06 * i + .3, pop(500 + 120 * i, 800 + 150 * i, .07), .22, pan=-.4 + .2 * i, rev=.35)
for t in np.geomspace(7.15, 7.9, 28):  # contador do total (desacelera)
    put(t, tick(2800), .08, pan=.2)
put(7.45, whoosh(.45, rising=False) * .8, .5, rev=.3)  # câmera desliza para o donut
put(7.55, pop(800, 1300, .08), .22, rev=.3)  # hover do segmento
put(7.72, whoosh(.55) * .8, .55, pan=-.2, rev=.3)  # o cartão nasce
put(7.72, sine(300, .8, 1600) * env(int(.8 * SR), .02, .4) * .25, .2, rev=.5)
for i in range(6):  # o cartão se desdobra em tiras
    put(8.5 + .05 * i + .18, click(), .5, pan=-.5 + .2 * i)
    put(8.5 + .05 * i + .18, pop(400 + 90 * i, 500 + 110 * i, .06), .2, pan=-.5 + .2 * i, rev=.3)
put(8.42, whoosh(.5, rising=False), .5, rev=.3)
for u, t in zip((.032, .22, .41, .6, .78, .97), (9.02, 9.13, 9.25, 9.36, 9.47, 9.58)):  # timeline
    put(t, pop(700 + 600 * u, 900 + 600 * u, .06), .22, pan=-.5 + u, rev=.3)
for t in np.linspace(9.35, 10.0, 8):  # o dedo passa pelas parcelas
    put(t, tick(2400 + 300 * np.sin(t * 9)), .1)
put(10.2, sine(160, 1.0, 2400) * ramp(int(1.0 * SR), 2.2) * .4, .35, rev=.4)  # timeline curva
put(10.25, whoosh(.9) * 1.0, .65, rev=.4)
put(10.95, pop(650, 1500, .12), .34, rev=.4)
for t in np.geomspace(10.95, 11.45, 14):
    put(t, tick(3100), .08, pan=.2)
for t in np.linspace(11.15, 11.6, 6):
    put(t, tick(1800 + 500 * (t - 11.15) * 4), .09, pan=.3)
put(11.6, sine(120, 1.4, 1500) * ramp(int(1.4 * SR), 2.2) * .4, .3, rev=.5)  # riser do recuo de câmera
put(11.75, whoosh(.3), .4, rev=.3)
put(11.9, whoosh(1.0) * 1.0, .6, rev=.4)
for i in range(5):  # linhas voltam ao dashboard
    put(12.3 + .07 * i, pop(600 + 70 * i, 900 + 70 * i, .07), .2, pan=-.4, rev=.3)
put(12.05, bell(79, 1.0, .4), .1, rev=.5)

# ------------------------------------------------------------------ 13–16s: organização (respiro)
put(13.0, kick(big=True), .9)
put(13.0, impact() * .8, .8, rev=.5)
put(13.0, whoosh(1.0, rising=False) * 1.2, .7, rev=.5)  # a íris clara
put(13.0, fft_filter(noise(2.5), lo=4500) * env(int(2.5 * SR), .002, 1.0), .18, rev=.6)
for t, n in ((13.35, 76), (14.55, 79)):  # Entenda hoje / Planeje o amanhã
    put(t, pop(620, 1000, .09), .3, rev=.3)
    put(t + .05, bell(n, 1.3, .5), .13, rev=.6)
for t, n in ((13.3, 88), (14.6, 91)):  # brilhos dos cards destacados
    put(t, bell(n, 1.0, .3), .07, pan=.4, rev=.6)
# pré-impacto: caixa em crescendo e riser até 16.0
for j, s in enumerate(np.arange(15.0, 16.0, 0.125 if True else .25)):
    put(s, clap() * (.15 + .3 * (s - 15.0)), 0.6, rev=.2)
put(15.0, noise(1.0) * ramp(int(SR), 2.5) * .22 * np.ones(int(SR)), .3, rev=.3)
put(15.1, sine(200, .9, 2800) * ramp(int(.9 * SR), 2.6) * .3, .35, rev=.4)
put(15.75, whoosh(.35, rising=True), .55, rev=.3)

# ------------------------------------------------------------------ 16–20s: segurança + marca
put(16.0, kick(big=True), 1.0)
put(16.0, impact(), .95, rev=.4)
put(16.0, whoosh(1.0, rising=False) * 1.1, .65, rev=.4)
for t in (16.15, 16.5, 16.98, 17.74):
    put(t, pop(620, 1000, .09), .3, rev=.3)
for i in range(5):  # cards viram formas e depois linhas
    put(16.1 + .045 * i + .15, pop(300 + 80 * i, 650 + 90 * i, .09), .22, pan=-.5 + .25 * i, rev=.3)
put(16.25, whoosh(.45) * .6, .4, rev=.3)
for t, hi in ((16.42, True), (16.62, False), (16.78, True)):  # toggle: autoriza / controla
    put(t, click(), .8)
    put(t, pop(700 if hi else 420, 1100 if hi else 300, .07), .3, rev=.3)
    if hi:
        put(t + .03, bell(86, .6, .25), .1, rev=.5)
put(16.92, whoosh(.4) * .7, .5, rev=.3)  # toggle vira escudo
put(17.0, pop(300, 200, .08), .3)
for t in (17.22, 17.58):  # o dinheiro bate no escudo e volta
    put(t, kick() * .8, .6)
    put(t, sine(120, .3, 70) * env(int(.3 * SR), .002, .12), .35)
    put(t, click(), .5)
    put(t, bell(52, .5, .15), .1, rev=.5)
for t in np.linspace(17.0, 17.55, 9):  # os dados seguem passando
    put(t, tick(3400), .07, pan=.4)
put(17.72, bell(84, 1.2, .5), .14, rev=.6)  # oficial: confirmado
put(17.74, bell(88, 1.2, .5), .1, rev=.6)
put(17.76, bell(91, 1.4, .6), .1, rev=.6)
put(18.05, sine(200, .7, 3200) * ramp(int(.7 * SR), 2.4) * .4, .4, rev=.4)  # linhas convergem
put(18.0, whoosh(.8) * 1.1, .7, rev=.4)
put(18.05, reverse_suck(.7) * .6, .4, rev=.3)
for t in np.linspace(18.15, 18.65, 12):
    put(t, tick(2000 + 2400 * (t - 18.15)), .09, pan=.2)
# Piggy + marca
put(18.75, kick(big=True), 1.0)
put(18.75, impact(), 1.0, rev=.5)
put(18.75, fft_filter(noise(2.0), lo=4000) * env(int(2 * SR), .001, .8), .22, rev=.5)
put(18.75, fft_filter(noise(1.0), lo=6000) * env(int(SR), .002, .5), .16, rev=.6)
put(18.95, pop(600, 1200, .1), .4, rev=.3)
put(18.95, whoosh(.3), .5, rev=.3)
for t, n in ((19.1, 72), (19.23, 76), (19.36, 79)):  # Conecte. Entenda. Planeje.
    put(t, pop(700, 1050, .08), .3, rev=.3)
    put(t + .02, bell(n + 12, 1.2, .5), .13, rev=.6)
fin = sum(saw(hz(n), 1.4, 0.005) for n in (48, 55, 60, 64, 67, 74))
fin = fft_filter(fin, hi=3200) * np.exp(-np.arange(int(1.4 * SR)) / SR / 1.2)
put(18.75, fin, .12, rev=.6)
put(18.75, sine(hz(36), 1.4) * env(int(1.4 * SR), .005, .8), .5)

# ------------------------------------------------------------------ mix
L += DL * duck
R += DR * duck
ir_n = int(1.6 * SR)
ir = rng.uniform(-1, 1, ir_n) * np.exp(-np.arange(ir_n) / SR / 0.45)
wet = [np.fft.irfft(np.fft.rfft(fft_filter(send, hi=6000), N + ir_n) * np.fft.rfft(ir2, N + ir_n))[:N]
       for ir2 in (ir, np.roll(ir, 173))]
L += wet[0] * 0.06
R += wet[1] * 0.06
mix = np.stack([L, R], 1)
mix = np.tanh(mix / np.max(np.abs(mix)) * 1.4) / np.tanh(1.4)
mix[-int(0.25 * SR):] *= np.linspace(1, 0, int(0.25 * SR))[:, None] ** 2
mix *= 0.89

out = Path(__file__).parent / "out"
out.mkdir(exist_ok=True)
with wave.open(str(out / "soundtrack.wav"), "wb") as w:
    w.setnchannels(2)
    w.setsampwidth(2)
    w.setframerate(SR)
    w.writeframes((mix * 32767).astype("<i2").tobytes())
print("ok:", out / "soundtrack.wav")
