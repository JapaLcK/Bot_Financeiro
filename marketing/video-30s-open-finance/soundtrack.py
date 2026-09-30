"""Trilha de 30s do anúncio, sintetizada do zero (sem sample nem música de terceiros).

120 BPM (tempo = 0,5s): as viradas de cena (5, 9,5, 19 e 23s do vídeo) caem em tempo forte.
Os efeitos usam os instantes da composição (hook.js/connect.js/payoff.js/finale.js) convertidos
pela mesma tabela de tempo do lib.js (TIME_KNOTS); se mudar um lugar, mude o outro. As primitivas de síntese moram em synth.py.
    python3 soundtrack.py   → out/soundtrack.wav
"""
import wave
from pathlib import Path

import numpy as np

from synth import (SR, bell, clap, click, env, fft_filter, hat, hz, impact, kick, noise, pop, ramp,
                   reverse_suck, rng, saw, sine, tick, whoosh)

DUR = 30.0
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


# tempo de composição (as animações) → tempo do vídeo. Igual ao TIME_KNOTS do lib.js.
KNOTS = [(0, 0), (3, 5), (6, 9.5), (13, 19), (16, 23), (22, 30)]


def tm(t):
    for (c0, v0), (c1, v1) in zip(KNOTS, KNOTS[1:]):
        if t <= c1:
            return v0 + (v1 - v0) * (t - c0) / (c1 - c0)
    return KNOTS[-1][1]


def ev(t, *a, **k):  # efeito ancorado num instante da animação
    put(tm(t), *a, **k)


# ------------------------------------------------------------------ estrutura (tempo do vídeo)
ROOT_CHORDS = [(57, [57, 60, 64]), (53, [57, 60, 65]), (48, [55, 60, 64]), (55, [55, 59, 62])]  # Am F C G
GROOVE = [(2.0, 5.0), (5.0, 9.5), (9.5, 19.0), (23.0, 27.6)]  # onde há bumbo
BREAK = (19.0, 23.0)  # organização: respiro
IMPACT = 28.2  # Piggy aparece (tm(20.45))
duck = np.ones(N)

for b in np.arange(0, DUR, 0.5):
    if any(a <= b < z for a, z in GROOVE) and (b >= 5.0 or (b % 1.0) == 0):
        put(b, kick(), 0.9)
        i = int(b * SR)
        k = np.linspace(0, 1, int(0.22 * SR))
        duck[i:i + len(k)] = np.minimum(duck[i:i + len(k)], 0.35 + 0.65 * k ** 0.7)
    if (b % 1.0) == 0.5 and (5.5 <= b < 19.0 or 23.5 <= b < 27.6):
        put(b, clap(), 0.4, pan=0.05, rev=0.25)
for b in (19.0, 20.0, 21.0, 22.0):  # respiro: bumbo suave no 1 de cada compasso
    put(b, kick() * 0.55, 0.45)
    i = int(b * SR); k = np.linspace(0, 1, int(0.25 * SR)); duck[i:i + len(k)] = np.minimum(duck[i:i + len(k)], 0.5 + 0.5 * k)

for s in np.arange(1.0, 27.6, 0.125):
    if BREAK[0] <= s < BREAK[1] and (s % 0.5) != 0.25:
        continue
    if s < 2.0 and (s % 0.25) != 0:
        continue
    dense = 9.5 <= s < 19.0 or 23.0 <= s < 27.6
    if (s % 0.5) == 0.25 or (dense and (s % 0.25) != 0):
        put(s, hat(), 0.18 if (s % 0.5) == 0.25 else 0.1, pan=0.35)
for s in (5.0, 7.0, 9.5, 12.0, 14.5, 16.5, 18.5, 23.5, 25.5):
    put(s, hat(True), 0.18, pan=-0.2, rev=0.3)

# harmonia: um acorde por compasso (2s)
for bar in range(15):
    t0 = bar * 2.0
    root, notes = ROOT_CHORDS[bar % 4]
    dur = min(2.0, 30.0 - t0)
    soft = BREAK[0] <= t0 < BREAK[1]
    pad = sum(saw(hz(n), dur, detune=0.004) for n in notes)
    pad = fft_filter(pad, hi=900 if t0 < 5.0 else 1500 if soft else 2600) * np.minimum(1, np.arange(len(pad)) / (0.3 * SR))
    put(t0, pad, 0.11 if soft else 0.1, pan=-0.25, rev=0.4, ducked=True)
    put(t0, fft_filter(sum(saw(hz(n + 12), dur, 0.006) for n in notes), hi=2200), 0.028, pan=0.3, rev=0.4, ducked=True)
    loud = 5.0 <= t0 < 19.0 or 23.0 <= t0 < 27.6
    if loud:  # baixo em colcheias
        for s in np.arange(t0, min(t0 + dur, 27.6), 0.25):
            bs = fft_filter(saw(hz(root - 12), 0.24) + 0.5 * sine(hz(root - 24), 0.24), hi=520)
            put(s, bs * env(len(bs), 0.004, 0.12), 0.42 if s % 0.5 else 0.3, ducked=True)
    if 6.0 <= t0 < 27.6:  # arpejo em pluck (suave e espaçado no respiro)
        seq = [notes[0] + 12, notes[1] + 12, notes[2] + 12, notes[1] + 24]
        step = 0.25 if soft else 0.125
        for j, s in enumerate(np.arange(t0, min(t0 + dur, 27.6), step)):
            if not soft and t0 < 9.5 and j % 2:
                continue
            p = sine(hz(seq[j % 4]), 0.3) + 0.3 * sine(hz(seq[j % 4]) * 2, 0.3)
            put(s, p * env(len(p), 0.002, 0.14 if soft else 0.06), 0.085 if soft else 0.095, pan=0.4 * np.sin(j), rev=0.5, ducked=not soft)

# abertura: drone de tensão que sobe até o vórtice
drone = fft_filter(saw(hz(45), 5.0, 0.006) + saw(hz(52), 5.0, 0.006), hi=400)
put(0.0, drone * ramp(int(5.0 * SR), 1.5) * 0.25, 1.0, rev=0.3)

# ------------------------------------------------------------------ gancho (letras se arrumam e depois são sugadas)
ev(0.0, impact() * 0.55, 1.0, rev=0.5)
ev(0.0, whoosh(0.8) * 0.6, 0.6, pan=-0.3, rev=0.3)
for t in (0.1, 0.35, 0.6, 0.85):  # as letras entram embaralhadas
    ev(t, pop(520 + 120 * t, 900 + 150 * t, 0.08), 0.3, pan=0.2 * np.sin(t * 9), rev=0.3)
for t in (0.4, 0.9, 1.4, 1.9):  # cartões cruzando a câmera
    ev(t, whoosh(0.45, rising=t % 1.0 < 0.5) * 0.5, 0.3, pan=np.sin(t * 7) * 0.7, rev=0.3)
for t, n in ((1.3, 72), (1.55, 76), (1.78, 79)):  # cada linha se arruma: um sino sobe
    ev(t, pop(700, 1000, .08), .22, rev=.3)
    ev(t + .02, bell(n + 12, .9, .3), .09, rev=.6)
ev(1.0, sine(300, 3.3, 2400) * ramp(int(3.3 * SR), 3) * 0.3, 0.25, rev=0.4)  # riser
ev(2.4, reverse_suck(1.0) * 0.8, 0.7, rev=0.3)  # tudo é puxado para o ponto
ev(2.6, sine(1200, 0.9, 100) * env(int(.9 * SR), 0.01, .5) * .3, 0.22, rev=.4)

# ------------------------------------------------------------------ 3–6s: conecte
ev(3.0, kick(big=True), 1.0)
ev(3.0, impact(), 0.95, rev=0.4)
ev(3.0, fft_filter(noise(2.0), lo=3500) * env(int(2 * SR), 0.001, 0.7), 0.2, rev=0.5)
ev(2.95, whoosh(0.6) * 0.7, 0.55, rev=0.3)  # o círculo vira celular
for i in range(4):  # os heróis pousam nas linhas
    ev(3.05 + .1 * i + .32, pop(420 + 70 * i, 700 + 70 * i, 0.09), 0.34, pan=-.3 + .2 * i, rev=.3)
ev(3.3, fft_filter(noise(0.9), lo=5000) * env(int(.9 * SR), .15, .4), 0.1, rev=.5)  # brilho da UI
ev(3.42, whoosh(0.4) * .5, .4, pan=-.4, rev=.3)
ev(3.85, whoosh(0.5) * .4, .35, pan=.5, rev=.3)  # dedo chegando
ev(4.42, click(), 0.7)  # toque
ev(4.42, pop(380, 520, 0.07), 0.3)
ev(4.45, sine(900, 0.35, 2600) * env(int(.35 * SR), .004, .25), .13, rev=.5)
ev(4.5, whoosh(0.6) * .8, .6, rev=.4)  # o botão se abre em linhas
for i in range(6):  # as linhas chegam nas instituições e confirmam
    tn = 4.52 + .03 * i + .6 + .15
    ev(tn, bell(81 + (0, 2, 4, 7, 9, 12)[i], 0.8, 0.3), 0.11, pan=-.6 + .24 * i, rev=.5)
    ev(tn - .15, pop(700 + 60 * i, 1100 + 60 * i, .07), .22, pan=-.6 + .24 * i, rev=.3)
ev(5.0, pop(520, 880, .1), .4, rev=.3)  # "É simples."
ev(5.02, whoosh(.3) * .5, .4, rev=.3)
for t in np.linspace(5.25, 5.5, 5):
    ev(t, tick(3200), .1, pan=.3)
ev(5.52, reverse_suck(0.4), .65, rev=.3)  # as conexões voltam para dentro
for i in range(6):
    ev(5.78 + .035 * i + .1, pop(500 + 40 * i, 760 + 40 * i, .06), .18, rev=.3)
ev(5.5, sine(200, .5, 1800) * ramp(int(.5 * SR), 2) * .3, .25, rev=.4)

# ------------------------------------------------------------------ 6–13s: payoff
ev(5.85, sine(150, .65, 1800) * ramp(int(.65 * SR), 2.4) * .35, 0.3, rev=.4)
ev(5.85, whoosh(.7) * 1.1, .7, rev=.4)
ev(6.0, kick(big=True), 1.0)
ev(6.0, impact(), .9, rev=.4)
ev(6.0, fft_filter(noise(2.2), lo=3500) * env(int(2.2 * SR), 0.001, 0.7), 0.2, rev=.5)
for i in range(5):  # as linhas entram
    ev(6.0 + .07 * i + .05, pop(300 + 40 * i, 500 + 40 * i, .08), .2, pan=-.3, rev=.3)
for t in (6.42, 7.66, 9.12, 10.4, 11.75):  # títulos
    ev(t, pop(620, 1000, .09), .32, rev=.3)
    ev(t - .02, whoosh(.3) * .6, .3, rev=.25)
ev(6.52, click(), .6)  # toque na linha
ev(6.6, whoosh(.6) * .8, .55, pan=.3, rev=.3)  # a linha vira card
for i in range(5):  # as bolinhas voam para o anel
    ev(6.68 + .06 * i + .3, pop(500 + 120 * i, 800 + 150 * i, .07), .22, pan=-.4 + .2 * i, rev=.35)
for t in np.geomspace(7.15, 7.9, 28):  # contador do total (desacelera)
    ev(t, tick(2800), .08, pan=.2)
ev(7.45, whoosh(.45, rising=False) * .8, .5, rev=.3)  # câmera desliza para o donut
ev(7.55, pop(800, 1300, .08), .22, rev=.3)  # hover do segmento
ev(7.72, whoosh(.55) * .8, .55, pan=-.2, rev=.3)  # o cartão nasce
ev(7.72, sine(300, .8, 1600) * env(int(.8 * SR), .02, .4) * .25, .2, rev=.5)
for i in range(6):  # o cartão se desdobra em tiras
    ev(8.5 + .05 * i + .18, click(), .5, pan=-.5 + .2 * i)
    ev(8.5 + .05 * i + .18, pop(400 + 90 * i, 500 + 110 * i, .06), .2, pan=-.5 + .2 * i, rev=.3)
ev(8.42, whoosh(.5, rising=False), .5, rev=.3)
for u, t in zip((.032, .22, .41, .6, .78, .97), (9.02, 9.13, 9.25, 9.36, 9.47, 9.58)):  # timeline
    ev(t, pop(700 + 600 * u, 900 + 600 * u, .06), .22, pan=-.5 + u, rev=.3)
for t in np.linspace(9.35, 10.0, 8):  # o dedo passa pelas parcelas
    ev(t, tick(2400 + 300 * np.sin(t * 9)), .1)
ev(10.2, sine(160, 1.0, 2400) * ramp(int(1.0 * SR), 2.2) * .4, .35, rev=.4)  # timeline curva
ev(10.25, whoosh(.9) * 1.0, .65, rev=.4)
ev(10.95, pop(650, 1500, .12), .34, rev=.4)
for t in np.geomspace(10.95, 11.45, 14):
    ev(t, tick(3100), .08, pan=.2)
for t in np.linspace(11.15, 11.6, 6):
    ev(t, tick(1800 + 500 * (t - 11.15) * 4), .09, pan=.3)
ev(11.6, sine(120, 1.4, 1500) * ramp(int(1.4 * SR), 2.2) * .4, .3, rev=.5)  # riser do recuo de câmera
ev(11.75, whoosh(.3), .4, rev=.3)
ev(11.9, whoosh(1.0) * 1.0, .6, rev=.4)
for i in range(5):  # linhas voltam ao dashboard
    ev(12.3 + .07 * i, pop(600 + 70 * i, 900 + 70 * i, .07), .2, pan=-.4, rev=.3)
ev(12.05, bell(79, 1.0, .4), .1, rev=.5)

# ------------------------------------------------------------------ 13–16s: organização (respiro)
ev(13.0, kick(big=True), .9)
ev(13.0, impact() * .8, .8, rev=.5)
ev(13.0, whoosh(1.0, rising=False) * 1.2, .7, rev=.5)  # a íris clara
ev(13.0, fft_filter(noise(2.5), lo=4500) * env(int(2.5 * SR), .002, 1.0), .18, rev=.6)
for t, n in ((13.35, 76), (14.55, 79)):  # Entenda hoje / Planeje o amanhã
    ev(t, pop(620, 1000, .09), .3, rev=.3)
    ev(t + .05, bell(n, 1.3, .5), .13, rev=.6)
for t, n in ((13.3, 88), (14.6, 91)):  # brilhos dos cards destacados
    ev(t, bell(n, 1.0, .3), .07, pan=.4, rev=.6)
# pré-impacto: caixa em crescendo e riser até 16.0
for j, s in enumerate(np.arange(15.0, 16.0, 0.125 if True else .25)):
    ev(s, clap() * (.15 + .3 * (s - 15.0)), 0.6, rev=.2)
ev(15.0, noise(1.0) * ramp(int(SR), 2.5) * .22 * np.ones(int(SR)), .3, rev=.3)
ev(15.1, sine(200, .9, 2800) * ramp(int(.9 * SR), 2.6) * .3, .35, rev=.4)
ev(15.75, whoosh(.35, rising=True), .55, rev=.3)

# ------------------------------------------------------------------ segurança + marca
ev(16.0, kick(big=True), 1.0)
ev(16.0, impact(), .95, rev=.4)
ev(16.0, whoosh(1.2, rising=False) * 1.1, .65, rev=.4)
for i in range(5):  # cards viram toggle, linha, escudo e os dois nós
    ev(16.12 + .05 * i, pop(300 + 80 * i, 650 + 90 * i, .09), .22, pan=-.5 + .25 * i, rev=.3)
ev(16.3, pop(620, 1000, .09), .3, rev=.3)  # "Você autoriza."
for t in (16.85, 17.18):  # desligado: a conexão bate no escudo
    ev(t, kick() * .6, .5)
    ev(t, sine(120, .3, 70) * env(int(.3 * SR), .002, .12), .3)
    ev(t, click(), .4)
ev(17.05, click(), .8)  # toque no toggle
ev(17.1, sine(380, .6, 1000) * env(int(.6 * SR), .05, .4), .16, rev=.4)  # o toggle desliza (suave)
ev(17.5, bell(84, 1.3, .5), .14, rev=.6)  # ligado
ev(17.52, bell(88, 1.3, .5), .1, rev=.6)
ev(17.55, bell(91, 1.5, .6), .1, rev=.6)
for t in np.arange(17.85, 19.8, .22):  # a conexão agora atravessa
    ev(t, tick(3300), .06, pan=.4)
ev(17.6, pop(620, 1000, .09), .3, rev=.3)  # "Você controla."
ev(18.0, pop(620, 1000, .09), .3, rev=.3)  # "O PigBank não movimenta..."
ev(18.1, bell(86, .8, .3), .1, rev=.5)  # só leitura
for t in (18.45, 19.0):  # o dinheiro bate e volta
    ev(t, kick() * .8, .6)
    ev(t, sine(120, .3, 70) * env(int(.3 * SR), .002, .12), .35)
    ev(t, click(), .5)
    ev(t, bell(52, .6, .2), .1, rev=.5)
ev(19.0, pop(620, 1000, .09), .3, rev=.3)  # "Open Finance oficial..."
ev(19.05, bell(84, 1.2, .5), .12, rev=.6)
ev(19.85, sine(200, .9, 3200) * ramp(int(.9 * SR), 2.4) * .4, .4, rev=.4)  # linhas convergem
ev(19.8, whoosh(1.0) * 1.1, .7, rev=.4)
ev(19.85, reverse_suck(.8) * .6, .4, rev=.3)
for t in np.linspace(19.9, 20.4, 12):
    ev(t, tick(2000 + 2400 * (t - 19.9) * 2), .09, pan=.2)
# Piggy + marca
put(IMPACT, kick(big=True), 1.0)
put(IMPACT, impact(), 1.0, rev=.5)
put(IMPACT, fft_filter(noise(2.0), lo=4000) * env(int(2 * SR), .001, .8), .22, rev=.5)
put(IMPACT, fft_filter(noise(1.0), lo=6000) * env(int(SR), .002, .5), .16, rev=.6)
ev(20.65, pop(600, 1200, .1), .4, rev=.3)
ev(20.65, whoosh(.3), .5, rev=.3)
for t, n in ((20.8, 72), (20.92, 76), (21.04, 79)):  # Conecte. Entenda. Planeje.
    ev(t, pop(700, 1050, .08), .3, rev=.3)
    ev(t + .02, bell(n + 12, 1.2, .5), .13, rev=.6)
fin = sum(saw(hz(n), 1.8, 0.005) for n in (48, 55, 60, 64, 67, 74))
fin = fft_filter(fin, hi=3200) * np.exp(-np.arange(int(1.8 * SR)) / SR / 1.5)
put(IMPACT, fin, .12, rev=.6)
put(IMPACT, sine(hz(36), 1.8) * env(int(1.8 * SR), .005, .9), .5)

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
