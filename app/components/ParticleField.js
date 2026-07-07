'use client';

import { useEffect, useRef } from 'react';

export default function ParticleField() {
  const canvasRef = useRef(null);

  useEffect(() => {
    const canvas = canvasRef.current;
    const page = canvas.parentElement;
    const ctx = canvas.getContext('2d');

    let w = page.clientWidth;
    let h = page.clientHeight;
    canvas.width = w;
    canvas.height = h;

    const mouse = { x: -9999, y: -9999 };

    // ── Particles ──────────────────────────────────────────────
    // Antigravity uses small elongated marks. We replicate that
    // by drawing short lines in the direction of velocity.
    const COUNT = 180;
    const REPEL_RADIUS = 150;   // how close cursor needs to be
    const REPEL_FORCE  = 6.5;   // how hard particles are kicked
    const FRICTION     = 0.88;  // velocity decay per frame
    const BASE_SPEED   = 0.25;  // natural drift speed

    const particles = Array.from({ length: COUNT }, () => {
      const angle = Math.random() * Math.PI * 2;
      return {
        x:  Math.random() * w,
        y:  Math.random() * h,
        vx: Math.cos(angle) * BASE_SPEED * (0.5 + Math.random()),
        vy: Math.sin(angle) * BASE_SPEED * (0.5 + Math.random()),
        size: Math.random() * 1.8 + 0.8,   // radius 0.8–2.6
        alpha: Math.random() * 0.5 + 0.5,  // 0.5–1.0 opacity
      };
    });

    // ── Mouse tracking ─────────────────────────────────────────
    function onMove(e) {
      const r = canvas.getBoundingClientRect();
      mouse.x = e.clientX - r.left;
      mouse.y = e.clientY - r.top;
    }
    function onLeave() {
      mouse.x = -9999;
      mouse.y = -9999;
    }
    window.addEventListener('mousemove', onMove);
    window.addEventListener('mouseleave', onLeave);

    // ── Resize ─────────────────────────────────────────────────
    function onResize() {
      w = page.clientWidth;
      h = page.clientHeight;
      canvas.width  = w;
      canvas.height = h;
    }
    window.addEventListener('resize', onResize);

    // ── Animation loop ─────────────────────────────────────────
    let frameId;
    function animate() {
      ctx.clearRect(0, 0, w, h);

      for (const p of particles) {
        // Repulsion from cursor
        const dx = p.x - mouse.x;
        const dy = p.y - mouse.y;
        const dist = Math.sqrt(dx * dx + dy * dy);

        if (dist < REPEL_RADIUS && dist > 0) {
          // Force is stronger the closer the particle is
          const strength = (1 - dist / REPEL_RADIUS) * REPEL_FORCE;
          p.vx += (dx / dist) * strength;
          p.vy += (dy / dist) * strength;
        }

        // Apply friction
        p.vx *= FRICTION;
        p.vy *= FRICTION;

        // If nearly stopped, restore drift so particles keep moving
        const speed = Math.sqrt(p.vx * p.vx + p.vy * p.vy);
        if (speed < BASE_SPEED) {
          p.vx += (Math.random() - 0.5) * 0.04;
          p.vy += (Math.random() - 0.5) * 0.04;
        }

        // Move
        p.x += p.vx;
        p.y += p.vy;

        // Wrap around edges
        if (p.x < -10) p.x = w + 10;
        if (p.x > w + 10) p.x = -10;
        if (p.y < -10) p.y = h + 10;
        if (p.y > h + 10) p.y = -10;

        // Draw as elongated mark in direction of travel
        // (mimics the Antigravity dash look)
        const tailLen = Math.min(speed * 5, 8);
        const nx = p.vx / (speed || 1);
        const ny = p.vy / (speed || 1);

        ctx.beginPath();
        ctx.moveTo(p.x - nx * tailLen * 0.5, p.y - ny * tailLen * 0.5);
        ctx.lineTo(p.x + nx * tailLen * 0.5, p.y + ny * tailLen * 0.5);
        ctx.strokeStyle = `rgba(255,255,255,${p.alpha})`;
        ctx.lineWidth = p.size;
        ctx.lineCap = 'round';
        ctx.stroke();
      }

      // ── Connect nearby particles with lines ──────────────────
      const CONNECT = 90;
      for (let i = 0; i < particles.length; i++) {
        for (let j = i + 1; j < particles.length; j++) {
          const a = particles[i], b = particles[j];
          const dx = a.x - b.x, dy = a.y - b.y;
          const d = Math.sqrt(dx * dx + dy * dy);
          if (d < CONNECT) {
            ctx.beginPath();
            ctx.moveTo(a.x, a.y);
            ctx.lineTo(b.x, b.y);
            ctx.strokeStyle = `rgba(255,255,255,${0.12 * (1 - d / CONNECT)})`;
            ctx.lineWidth = 0.5;
            ctx.stroke();
          }
        }
      }

      frameId = requestAnimationFrame(animate);
    }

    animate();

    return () => {
      cancelAnimationFrame(frameId);
      window.removeEventListener('mousemove', onMove);
      window.removeEventListener('mouseleave', onLeave);
      window.removeEventListener('resize', onResize);
    };
  }, []);

  return (
    <canvas
      ref={canvasRef}
      style={{ position: 'absolute', inset: 0, zIndex: 1, pointerEvents: 'none' }}
    />
  );
}