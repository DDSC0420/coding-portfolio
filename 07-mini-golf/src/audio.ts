// Little synthesised sounds (Web Audio, no files). Started on the first click or key press, as
// browsers require; muting is remembered.

type Sound = 'putt' | 'wall' | 'bumper' | 'splash' | 'holed';

const KEY = 'pocket-links:muted';

export class Sounds {
  private ctx: AudioContext | null = null;
  muted: boolean;

  constructor() {
    let stored = false;
    try {
      stored = localStorage.getItem(KEY) === '1';
    } catch {
      /* storage blocked: default to sound on */
    }
    this.muted = stored;
  }

  toggle(): boolean {
    this.muted = !this.muted;
    try {
      localStorage.setItem(KEY, this.muted ? '1' : '0');
    } catch {
      /* ignore */
    }
    return this.muted;
  }

  /** Call from a user gesture. */
  unlock(): void {
    if (!this.ctx && typeof AudioContext !== 'undefined') this.ctx = new AudioContext();
    void this.ctx?.resume();
  }

  play(sound: Sound, strength = 1): void {
    const ctx = this.ctx;
    if (!ctx || this.muted || ctx.state !== 'running') return;
    const t = ctx.currentTime;
    const gain = ctx.createGain();
    gain.connect(ctx.destination);
    const level = Math.max(0.05, Math.min(1, strength)) * 0.35;
    const tone = (freq: number, end: number, dur: number, type: OscillatorType = 'sine', at = t) => {
      const o = ctx.createOscillator();
      o.type = type;
      o.frequency.setValueAtTime(freq, at);
      o.frequency.exponentialRampToValueAtTime(end, at + dur);
      o.connect(gain);
      o.start(at);
      o.stop(at + dur);
    };
    const noise = (dur: number, cutoff: number) => {
      const buffer = ctx.createBuffer(1, Math.ceil(ctx.sampleRate * dur), ctx.sampleRate);
      const data = buffer.getChannelData(0);
      for (let i = 0; i < data.length; i++) data[i] = (Math.random() * 2 - 1) * (1 - i / data.length);
      const src = ctx.createBufferSource();
      src.buffer = buffer;
      const filter = ctx.createBiquadFilter();
      filter.type = 'lowpass';
      filter.frequency.value = cutoff;
      src.connect(filter).connect(gain);
      src.start(t);
    };
    switch (sound) {
      case 'putt':
        gain.gain.setValueAtTime(level, t);
        gain.gain.exponentialRampToValueAtTime(0.001, t + 0.12);
        tone(1400, 600, 0.08, 'triangle');
        break;
      case 'wall':
        gain.gain.setValueAtTime(level * 0.7, t);
        gain.gain.exponentialRampToValueAtTime(0.001, t + 0.09);
        tone(420, 180, 0.08, 'square');
        break;
      case 'bumper':
        gain.gain.setValueAtTime(level, t);
        gain.gain.exponentialRampToValueAtTime(0.001, t + 0.25);
        tone(300, 900, 0.2, 'sine');
        break;
      case 'splash':
        gain.gain.setValueAtTime(0.4, t);
        gain.gain.exponentialRampToValueAtTime(0.001, t + 0.5);
        noise(0.5, 1200);
        break;
      case 'holed':
        gain.gain.setValueAtTime(0.3, t);
        gain.gain.exponentialRampToValueAtTime(0.001, t + 0.9);
        [660, 880, 1320].forEach((f, i) => tone(f, f, 0.35, 'sine', t + i * 0.09));
        break;
    }
  }
}
