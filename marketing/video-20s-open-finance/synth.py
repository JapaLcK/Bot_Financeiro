"""Primitivas de síntese da trilha (osciladores, filtros, percussão e efeitos), sem estado de mix.

O `rng` é único e semeado: a ordem das chamadas define o ruído, então mudar a ordem em
soundtrack.py muda o som. Fica aqui para o soundtrack.py caber no teto de 350 linhas por arquivo.
"""
import numpy as np

SR = 48000
rng = np.random.default_rng(23)


def env(n, a=0.002, d=0.2):
    t = np.arange(n) / SR
    return np.minimum(1, t / a) * np.exp(-t / d)



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


def ramp(n, p=2.0):
    return np.linspace(0, 1, n) ** p


# ------------------------------------------------------------------ bateria
def kick(big=False):
    n = int(SR * (0.75 if big else 0.36))
    t = np.arange(n) / SR
    f = 44 + (170 if big else 120) * np.exp(-t * 28)
    body = np.sin(2 * np.pi * np.cumsum(f) / SR) * np.exp(-t / (0.34 if big else 0.13))
    click = fft_filter(noise(n / SR), lo=2000) * np.exp(-t / 0.004) * 0.4
    return np.tanh((body + click) * 1.6)


def clap():
    s = fft_filter(noise(0.25), lo=900, hi=5000)
    e = env(len(s), 0.001, 0.07)
    for k in (0.008, 0.017):
        e[: int(k * SR)] *= 0.6
    return s * e


def hat(open_=False):
    s = fft_filter(noise(0.3 if open_ else 0.08), lo=7000)
    return s * env(len(s), 0.0005, 0.09 if open_ else 0.018)


def whoosh(dur, rising=True):
    s = noise(dur)
    n = len(s)
    out = np.zeros(n)
    seg = n // 12
    for k in range(12):
        c = 400 * 2 ** ((k if rising else 11 - k) / 2.2)
        out[k * seg:(k + 1) * seg] = fft_filter(s[k * seg:(k + 1) * seg], lo=c * 0.6, hi=c * 1.8)
    return out * np.sin(np.linspace(0, np.pi, n)) ** 2


def pop(f=700, f2=1150, d=0.09):
    s = sine(f, d, f2)
    return s * env(len(s), 0.001, 0.03)


def tick(f=3000, d=0.012):
    return sine(f, d) * env(int(SR * d), 0.0005, d * .5)


def click():
    s = fft_filter(noise(0.03), lo=1800, hi=9000)
    return s * env(len(s), 0.0003, 0.008)


def bell(n, dur=0.9, d=0.3):
    f = hz(n)
    s = sine(f, dur) + 0.35 * sine(f * 2.76, dur) + 0.15 * sine(f * 5.4, dur)
    return s * env(len(s), 0.002, d)


def impact(big=True):
    put_len = 1.6
    s = sine(90, put_len, 38) * env(int(put_len * SR), 0.003, 0.55)
    s += fft_filter(noise(put_len), lo=2500) * env(int(put_len * SR), 0.001, 0.6) * 0.35
    return np.tanh(s * 1.4)


def reverse_suck(dur):  # varredura descendente de ruído que "puxa" para o ponto
    return whoosh(dur, rising=False) * ramp(int(dur * SR), 1.3)
