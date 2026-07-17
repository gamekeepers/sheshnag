'use client';
import { useEffect, useState, useRef } from 'react';
import confetti from 'canvas-confetti';

export default function InteractiveMoon({ isPasswordFocused }) {
  const [mousePos, setMousePos] = useState({ x: 0, y: 0 });
  const [isTickled, setIsTickled] = useState(false);
  const containerRef = useRef(null);

  useEffect(() => {
    const handleMouseMove = (e) => {
      if (!containerRef.current) return;
      const rect = containerRef.current.getBoundingClientRect();
      // Calculate cursor position relative to the center of the moon
      const centerX = rect.left + rect.width / 2;
      const centerY = rect.top + rect.height / 2;
      
      // Normalize values relative to screen size so the eyes don't go too crazy
      const diffX = (e.clientX - centerX) / (window.innerWidth / 2);
      const diffY = (e.clientY - centerY) / (window.innerHeight / 2);

      // Clamp between -1 and 1
      const clampedX = Math.max(-1, Math.min(1, diffX));
      const clampedY = Math.max(-1, Math.min(1, diffY));

      setMousePos({ x: clampedX, y: clampedY });
    };

    window.addEventListener('mousemove', handleMouseMove);
    return () => window.removeEventListener('mousemove', handleMouseMove);
  }, []);

  // Max translation radius in pixels (increased for more obvious movement)
  const maxMove = 28; 
  const eyeX = mousePos.x * maxMove;
  const eyeY = mousePos.y * maxMove;

  const handleTickle = () => {
    if (isTickled) return;
    setIsTickled(true);
    
    // Confetti from the center of the moon
    const rect = containerRef.current.getBoundingClientRect();
    const x = (rect.left + rect.width / 2) / window.innerWidth;
    const y = (rect.top + rect.height / 2) / window.innerHeight;
    
    confetti({
      particleCount: 60,
      spread: 80,
      origin: { x, y },
      colors: ['#fff', '#cfdbfa', '#8896d3', '#302b5e'],
      zIndex: 100
    });
    
    setTimeout(() => setIsTickled(false), 1000);
  };

  return (
    <div 
      ref={containerRef}
      onClick={handleTickle}
      style={{
        width: '180px',
        height: '180px',
        margin: '0 auto 24px auto',
        perspective: '1200px', // Enables 3D space for children
        cursor: 'pointer',
        display: 'flex',
        alignItems: 'center',
        justifyContent: 'center'
      }}
    >
      <div style={{
        width: '120px',
        height: '120px',
        transform: isTickled ? 'scale(1.5) rotate(-5deg)' : 'scale(1.5)',
        transition: 'transform 0.15s ease-out'
      }}>
        <div
          style={{
            width: '100%',
            height: '100%',
            position: 'relative',
            transformStyle: 'preserve-3d',
          }}
        >
        {/* ── FRONT FACE (EYES & SMILE) ── */}
        <div style={{
          position: 'absolute',
          width: '100%',
          height: '100%',
          borderRadius: '50%',
          // Beautiful subtle gradient mimicking the vector moon
          background: 'radial-gradient(circle at 35% 65%, #f1f4ff 0%, #cfdbfa 35%, #8896d3 70%, #302b5e 100%)',
          boxShadow: 'inset -8px 8px 20px rgba(0,0,0,0.5), 0 0 20px rgba(136, 150, 211, 0.2)',
          backfaceVisibility: 'hidden',
          display: 'flex',
          alignItems: 'center',
          justifyContent: 'center',
          overflow: 'hidden'
        }}>
          {/* Craters (subtle circular borders) */}
          <div style={{ position:'absolute', width:'22px', height:'22px', borderRadius:'50%', border:'1.5px solid rgba(255,255,255,0.2)', top:'18%', right:'22%' }} />
          <div style={{ position:'absolute', width:'34px', height:'34px', borderRadius:'50%', border:'1.5px solid rgba(255,255,255,0.15)', bottom:'15%', right:'10%' }} />
          <div style={{ position:'absolute', width:'14px', height:'14px', borderRadius:'50%', border:'1.5px solid rgba(255,255,255,0.3)', top:'45%', left:'12%' }} />

          {/* Facial Features (moves with mouse) */}
          <div style={{
            display: 'flex',
            flexDirection: 'column',
            alignItems: 'center',
            gap: '5px',
            transform: `translate(${eyeX}px, ${eyeY}px)`,
            transition: 'transform 0.1s ease-out'
          }}>
            <div style={{ display: 'flex', gap: '16px' }}>
              {isTickled ? (
                <>
                  {/* Tickled Left Eye */}
                  <svg width="22" height="22" viewBox="0 0 24 24" fill="none" stroke="#0a0a1a" strokeWidth="4" strokeLinecap="round" strokeLinejoin="round">
                    <path d="M 6 15 L 12 9 L 18 15" />
                  </svg>
                  {/* Tickled Right Eye */}
                  <svg width="22" height="22" viewBox="0 0 24 24" fill="none" stroke="#0a0a1a" strokeWidth="4" strokeLinecap="round" strokeLinejoin="round">
                    <path d="M 6 15 L 12 9 L 18 15" />
                  </svg>
                </>
              ) : isPasswordFocused ? (
                <>
                  {/* Closed Left Eye */}
                  <svg width="22" height="22" viewBox="0 0 24 24" fill="none" stroke="#0a0a1a" strokeWidth="4" strokeLinecap="round">
                    <path d="M 4 12 Q 12 18 20 12" />
                  </svg>
                  {/* Closed Right Eye */}
                  <svg width="22" height="22" viewBox="0 0 24 24" fill="none" stroke="#0a0a1a" strokeWidth="4" strokeLinecap="round">
                    <path d="M 4 12 Q 12 18 20 12" />
                  </svg>
                </>
              ) : (
                <>
                  {/* Left Eye */}
                  <div style={{ width: '22px', height: '22px', borderRadius: '50%', background: '#0a0a1a', position: 'relative' }}>
                    <div style={{ width: '6px', height: '6px', borderRadius: '50%', background: '#fff', position: 'absolute', top: '4px', right: '5px' }} />
                  </div>
                  {/* Right Eye */}
                  <div style={{ width: '22px', height: '22px', borderRadius: '50%', background: '#0a0a1a', position: 'relative' }}>
                    <div style={{ width: '6px', height: '6px', borderRadius: '50%', background: '#fff', position: 'absolute', top: '4px', right: '5px' }} />
                  </div>
                </>
              )}
            </div>
            {/* Smile */}
            {isTickled ? (
              <svg width="34" height="18" viewBox="0 0 24 16" fill="#0a0a1a" stroke="#0a0a1a" strokeWidth="1" strokeLinejoin="round">
                <path d="M 2 2 Q 12 24 22 2 Z" />
              </svg>
            ) : (
              <svg width="34" height="16" viewBox="0 0 24 12" fill="none" stroke="#0a0a1a" strokeWidth="3" strokeLinecap="round">
                <path d="M 4 2 Q 12 14 20 2" />
              </svg>
            )}
          </div>
        </div>

        </div>
      </div>
    </div>
  );
}
