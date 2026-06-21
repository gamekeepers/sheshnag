'use client';

import { useEffect, useRef } from 'react';

export default function CursorEffect() {
  const canvasRef = useRef(null);

  useEffect(() => {
    const canvas = canvasRef.current;
    const ctx = canvas.getContext('2d');

    let w = window.innerWidth;
    let h = window.innerHeight;
    canvas.width = w;
    canvas.height = h;

    const mouse = { x: -9999, y: -9999 };

    const COUNT      = 200;
    const REPEL_R    = 130;
    const REPEL_F    = 7;
    const FRICTION   = 0.87;
    const DRIFT      = 0.22;

    const particles = Array.from({ length: COUNT }, () => {
      const angle = Math.random() * Math.PI * 2;
      return {
        x:  Math.random() * w,
        y:  Math.random() * h,
        vx: Math.cos(angle) * DRIFT * (0.4 + Math.random() * 0.8),
        vy: Math.sin(angle) * DRIFT * (0.4 + Math.random() * 0.8),
        r:  Math.random() * 1.5 + 0.6,
        alpha: Math.random() * 0.45 + 0.55,
      };
    });

    function onMove(e) { mouse.x = e.clientX; mouse.y = e.clientY; }
    function onLeave()  { mouse.x = -9999; mouse.y = -9999; }
    function onResize() {
      w = window.innerWidth; h = window.innerHeight;
      canvas.width = w; canvas.height = h;
    }

    window.addEventListener('mousemove', onMove);
    window.addEventListener('mouseleave', onLeave);
    window.addEventListener('resize', onResize);

    let raf;
    function draw() {
      ctx.clearRect(0, 0, w, h);

      for (const p of particles) {
        const dx = p.x - mouse.x;
        const dy = p.y - mouse.y;
        const dist = Math.sqrt(dx * dx + dy * dy);

        if (dist < REPEL_R && dist > 0) {
          const strength = (1 - dist / REPEL_R) * REPEL_F;
          p.vx += (dx / dist) * strength;
          p.vy += (dy / dist) * strength;
        }

        p.vx *= FRICTION;
        p.vy *= FRICTION;
        const speed = Math.sqrt(p.vx * p.vx + p.vy * p.vy);
        if (speed < DRIFT) {
          p.vx += (Math.random() - 0.5) * 0.05;
          p.vy += (Math.random() - 0.5) * 0.05;
        }

        p.x += p.vx;
        p.y += p.vy;

        if (p.x < -5) p.x = w + 5;
        if (p.x > w + 5) p.x = -5;
        if (p.y < -5) p.y = h + 5;
        if (p.y > h + 5) p.y = -5;

        // Elongated dash in direction of travel
        const tailLen = Math.min(speed * 6, 10);
        const nx = speed > 0.01 ? p.vx / speed : 0;
        const ny = speed > 0.01 ? p.vy / speed : 1;

        ctx.beginPath();
        ctx.moveTo(p.x - nx * tailLen * 0.5, p.y - ny * tailLen * 0.5);
        ctx.lineTo(p.x + nx * tailLen * 0.5, p.y + ny * tailLen * 0.5);
        ctx.strokeStyle = `rgba(255,255,255,${p.alpha})`;
        ctx.lineWidth = p.r;
        ctx.lineCap = 'round';
        ctx.stroke();
      }

      raf = requestAnimationFrame(draw);
    }

    draw();

    return () => {
      cancelAnimationFrame(raf);
      window.removeEventListener('mousemove', onMove);
      window.removeEventListener('mouseleave', onLeave);
      window.removeEventListener('resize', onResize);
    };
  }, []);

  return (
    <canvas
      ref={canvasRef}
      style={{ position: 'fixed', inset: 0, zIndex: 9999, pointerEvents: 'none' }}
    />
  );
}
