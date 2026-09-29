"use client";

import { useEffect, useRef } from "react";

export type VoiceVisualState =
  | "idle"
  | "connecting"
  | "listening"
  | "thinking"
  | "speaking"
  | "error";

type VoiceOrbProps = { state: VoiceVisualState; inputLevel: number };

/**
 * Dependency-free point-cloud sphere. Canvas is intentionally used here instead
 * of a WebGL dependency: the voice screen needs one responsive visual, not a
 * complete 3D scene graph.
 */
export function VoiceOrb({ state, inputLevel }: VoiceOrbProps) {
  const canvasRef = useRef<HTMLCanvasElement>(null);
  const stateRef = useRef(state);
  const levelRef = useRef(inputLevel);
  stateRef.current = state;
  levelRef.current = inputLevel;

  useEffect(() => {
    const canvas = canvasRef.current;
    if (!canvas) return;
    const context = canvas.getContext("2d");
    if (!context) return;
    let frame = 0;
    let width = 0;
    let height = 0;
    let pixelRatio = 1;
    const reduceMotion = window.matchMedia("(prefers-reduced-motion: reduce)").matches;

    const resize = () => {
      const box = canvas.getBoundingClientRect();
      pixelRatio = Math.min(window.devicePixelRatio || 1, 2);
      width = Math.max(1, box.width);
      height = Math.max(1, box.height);
      canvas.width = Math.round(width * pixelRatio);
      canvas.height = Math.round(height * pixelRatio);
      context.setTransform(pixelRatio, 0, 0, pixelRatio, 0, 0);
    };
    const observer = new ResizeObserver(resize);
    observer.observe(canvas);
    resize();

    const draw = (milliseconds: number) => {
      const activeState = stateRef.current;
      const level = Math.min(1, Math.max(0, levelRef.current));
      const seconds = milliseconds / 1000;
      const size = Math.min(width, height);
      const centerX = width / 2;
      const centerY = height / 2;
      const baseRadius = size * 0.34;
      const phase = reduceMotion ? 0 : seconds;
      const intensity =
        activeState === "listening"
          ? 0.16 + level * 0.8
          : activeState === "speaking"
            ? 0.46 + Math.sin(phase * 7) * 0.12
            : activeState === "thinking" || activeState === "connecting"
              ? 0.28
              : activeState === "error"
                ? 0.08
                : 0.05;
      const spin = reduceMotion ? 0 : phase * (activeState === "thinking" ? 0.34 : 0.16);

      context.clearRect(0, 0, width, height);
      const glow = context.createRadialGradient(centerX, centerY, size * 0.06, centerX, centerY, size * 0.5);
      glow.addColorStop(0, "rgba(66, 119, 255, 0.16)");
      glow.addColorStop(0.55, "rgba(44, 90, 255, 0.055)");
      glow.addColorStop(1, "rgba(0, 0, 0, 0)");
      context.fillStyle = glow;
      context.fillRect(0, 0, width, height);

      const points: { x: number; y: number; z: number; alpha: number; radius: number }[] = [];
      const rows = 42;
      const columns = 58;
      for (let row = 0; row <= rows; row += 1) {
        const latitude = (row / rows) * Math.PI;
        for (let column = 0; column < columns; column += 1) {
          const longitude = (column / columns) * Math.PI * 2;
          const wave =
            Math.sin(longitude * 4 + phase * 1.8) * Math.sin(latitude * 3 - phase * 1.1) * intensity * 0.18 +
            Math.sin(longitude * 7 - phase * 1.3) * intensity * 0.05;
          const radius = baseRadius * (1 + wave);
          const x0 = Math.sin(latitude) * Math.cos(longitude + spin) * radius;
          const y0 = Math.cos(latitude) * radius;
          const z0 = Math.sin(latitude) * Math.sin(longitude + spin) * radius;
          const tilt = 0.23;
          const y = y0 * Math.cos(tilt) - z0 * Math.sin(tilt);
          const z = y0 * Math.sin(tilt) + z0 * Math.cos(tilt);
          const perspective = 2.55 / (2.55 - z / baseRadius);
          const front = (z / baseRadius + 1) / 2;
          points.push({
            x: centerX + x0 * perspective,
            y: centerY + y * perspective,
            z,
            alpha: 0.18 + front * 0.7,
            radius: (0.55 + front * 0.85) * (0.7 + intensity * 0.35),
          });
        }
      }
      points.sort((a, b) => a.z - b.z);
      for (const point of points) {
        const blue = Math.min(255, Math.round(177 + point.alpha * 70));
        context.fillStyle = `rgba(${activeState === "error" ? "255, 145, 155" : `190, 218, ${blue}`}, ${point.alpha})`;
        context.beginPath();
        context.arc(point.x, point.y, point.radius, 0, Math.PI * 2);
        context.fill();
      }
      if (!reduceMotion || frame === 0) frame = window.requestAnimationFrame(draw);
    };
    frame = window.requestAnimationFrame(draw);
    return () => {
      window.cancelAnimationFrame(frame);
      observer.disconnect();
    };
  }, []);

  return <canvas ref={canvasRef} className="h-full w-full" aria-hidden="true" />;
}
