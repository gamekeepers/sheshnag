'use client';

import { useEffect, useRef } from 'react';

export default function ParticleField() {
  const canvasRef = useRef(null);

  useEffect(() => {
    const canvas = canvasRef.current;
    if (!canvas) return;
    const ctx = canvas.getContext('2d');

    let w = window.innerWidth;
    let h = window.innerHeight;
    canvas.width = w;
    canvas.height = h;

    const COUNT = 900;
    const stars = Array.from({ length: COUNT }, () => {
      const colors = ['#ffffff', '#ffffff', '#ffffff', '#ffffff', '#e2f0ff', '#fff3e2'];
      const color = colors[Math.floor(Math.random() * colors.length)];
      
      return {
        x: Math.random() * w,
        y: Math.random() * h,
        z: Math.random() * 3 + 1,
        size: Math.random() * 1.2 + 0.1,
        color,
        twinkleSpeed: Math.random() * 0.02 + 0.005,
        twinklePhase: Math.random() * Math.PI * 2,
      };
    });

    let scrollY = 0;
    let targetScrollY = 0;
    function onScroll() {
      targetScrollY = window.scrollY;
    }
    window.addEventListener('scroll', onScroll, { passive: true });

    function onResize() {
      w = window.innerWidth;
      h = window.innerHeight;
      canvas.width = w;
      canvas.height = h;
    }
    window.addEventListener('resize', onResize, { passive: true });

    let frameId;
    let time = 0;

    function animate() {
      ctx.clearRect(0, 0, w, h);
      time += 1;
      scrollY += (targetScrollY - scrollY) * 0.1;

      const totalSections = 5;
      const totalScroll = h * totalSections;
      const fullPageScroll = h * (totalSections + 3);

      for (const s of stars) {
        s.y -= 0.03 * s.z;
        
        let drawX = s.x;
        let drawY = s.y - (scrollY * s.z * 0.08);

        drawX = ((drawX % w) + w) % w;
        drawY = ((drawY % h) + h) % h;

        // Twinkling
        const alpha = 0.3 + 0.7 * Math.abs(Math.sin(time * s.twinkleSpeed + s.twinklePhase));

        ctx.beginPath();
        ctx.arc(drawX, drawY, s.size, 0, Math.PI * 2);
        ctx.fillStyle = s.color;
        ctx.globalAlpha = alpha;
        ctx.fill();
      }
      
      ctx.globalAlpha = 1;
      frameId = requestAnimationFrame(animate);
    }

    animate();

    return () => {
      cancelAnimationFrame(frameId);
      window.removeEventListener('scroll', onScroll);
      window.removeEventListener('resize', onResize);
    };
  }, []);

  return (
    <canvas
      ref={canvasRef}
      style={{ position: 'fixed', inset: 0, zIndex: 2, pointerEvents: 'none', background: 'transparent' }}
    />
  );
}