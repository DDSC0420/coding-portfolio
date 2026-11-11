// Short-lived particles: confetti when the ball drops, a splash in water, sparks off a bumper.
// Cosmetic only: they use their own random numbers and never touch the physics.

export interface Particle {
  x: number;
  y: number;
  vx: number;
  vy: number;
  life: number; // seconds left
  total: number;
  size: number;
  color: string;
  gravity: number;
  spin: number;
}

const CONFETTI = ['#ffd23f', '#ee4266', '#3bceac', '#ffffff', '#540d6e', '#0ead69'];

export class Effects {
  particles: Particle[] = [];
  flashes = new Map<number, number>(); // bumper index → seconds of glow left

  burst(x: number, y: number, kind: 'confetti' | 'splash' | 'spark'): void {
    const n = kind === 'confetti' ? 70 : kind === 'splash' ? 26 : 10;
    for (let i = 0; i < n; i++) {
      const a = Math.random() * Math.PI * 2;
      const speed = kind === 'confetti' ? 120 + Math.random() * 260 : kind === 'splash' ? 60 + Math.random() * 140 : 80 + Math.random() * 120;
      const life = kind === 'confetti' ? 1.2 + Math.random() * 0.8 : 0.5 + Math.random() * 0.4;
      this.particles.push({
        x,
        y,
        vx: Math.cos(a) * speed,
        vy: Math.sin(a) * speed - (kind === 'confetti' ? 160 : 40),
        life,
        total: life,
        size: kind === 'confetti' ? 3 + Math.random() * 3 : kind === 'splash' ? 2 + Math.random() * 2.5 : 1.5 + Math.random(),
        color: kind === 'confetti' ? CONFETTI[i % CONFETTI.length]! : kind === 'splash' ? (i % 3 ? '#cfe9ff' : '#ffffff') : '#fff3b0',
        gravity: kind === 'confetti' ? 260 : kind === 'splash' ? 380 : 0,
        spin: (Math.random() - 0.5) * 12,
      });
    }
  }

  flash(index: number): void {
    this.flashes.set(index, 0.25);
  }

  update(dt: number): void {
    for (const p of this.particles) {
      p.life -= dt;
      p.vy += p.gravity * dt;
      p.vx *= 1 - 1.2 * dt;
      p.x += p.vx * dt;
      p.y += p.vy * dt;
    }
    this.particles = this.particles.filter((p) => p.life > 0);
    for (const [k, v] of this.flashes) {
      if (v - dt <= 0) this.flashes.delete(k);
      else this.flashes.set(k, v - dt);
    }
  }

  draw(ctx: CanvasRenderingContext2D, now: number): void {
    for (const p of this.particles) {
      ctx.globalAlpha = Math.min(1, p.life / (p.total * 0.4));
      ctx.fillStyle = p.color;
      ctx.save();
      ctx.translate(p.x, p.y);
      ctx.rotate(p.spin * now * 0.001);
      ctx.fillRect(-p.size / 2, -p.size / 4, p.size, p.size / 2);
      ctx.restore();
    }
    ctx.globalAlpha = 1;
  }
}
