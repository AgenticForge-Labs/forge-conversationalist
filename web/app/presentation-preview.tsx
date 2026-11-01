"use client";

import { useState } from "react";

type Notice = "mail" | "order" | "failure";
type Emotion = "neutral" | "happy" | "curious" | "confused" | "concerned" | "excited";

const notifications: Record<Notice, { label: string; sound: number[] }> = {
  mail: { label: "New mail", sound: [720] },
  order: { label: "New order", sound: [784, 1047] },
  failure: { label: "Something needs attention", sound: [420, 360] },
};

async function previewTone(notes: number[]): Promise<void> {
  if (typeof window === "undefined" || !window.AudioContext) return;
  const ctx = new window.AudioContext();
  try {
    if (ctx.state === "suspended") await ctx.resume();
    notes.forEach((frequency, index) => {
      const oscillator = ctx.createOscillator();
      const gain = ctx.createGain();
      const start = ctx.currentTime + 0.025 + index * 0.18;
      oscillator.type = "sine";
      oscillator.frequency.value = frequency;
      gain.gain.setValueAtTime(0, start);
      gain.gain.linearRampToValueAtTime(0.10, start + 0.02);
      gain.gain.exponentialRampToValueAtTime(0.001, start + 0.16);
      oscillator.connect(gain);
      gain.connect(ctx.destination);
      oscillator.start(start);
      oscillator.stop(start + 0.17);
    });
    // Never persist audio; close the demonstration's browser-only graph.
    window.setTimeout(() => void ctx.close(), notes.length * 180 + 500);
  } catch {
    await ctx.close();
  }
}

export function PresentationPreview() {
  const [emotion, setEmotion] = useState<Emotion>("neutral");
  const [notice, setNotice] = useState<Notice | null>(null);
  const [muted, setMuted] = useState(false);

  function preview(kind: Notice) {
    setNotice(kind);
    if (!muted) void previewTone(notifications[kind].sound);
  }

  return (
    <section className="presentation-preview">
      <p className="eyebrow">Device-independent presentation</p>
      <h2>Expressions and notification previews</h2>
      <p className="subtle">Local demonstration only. No real mail, order, failure, or Platform event has been received; no robot is connected.</p>
      <div className="preview-grid">
        <div>
          <label htmlFor="emotion-select">Expression (semantic preview)</label>
          <select id="emotion-select" value={emotion}
            onChange={(e) => setEmotion(e.target.value as Emotion)}>
            {(["neutral", "happy", "curious", "confused", "concerned", "excited"] as Emotion[]).map(
              (value) => <option key={value} value={value}>{value}</option>
            )}
          </select>
          <div className="preview-output" aria-live="polite">Expression: {emotion}</div>
        </div>
        <div>
          <label>Notification signals (manual test)</label>
          <div className="preview-actions">
            <button type="button" onClick={() => preview("mail")}>Preview mail</button>
            <button type="button" onClick={() => preview("order")}>Preview order</button>
            <button type="button" onClick={() => preview("failure")}>Preview failure</button>
          </div>
          <label className="mute-choice">
            <input type="checkbox" checked={muted} onChange={(e) => setMuted(e.target.checked)} />
            Mute notification chimes
          </label>
          <div className="preview-output" role="status" aria-live="polite">
            {notice ? `Preview only: ${notifications[notice].label}` : "No notification preview selected"}
          </div>
        </div>
      </div>
    </section>
  );
}
