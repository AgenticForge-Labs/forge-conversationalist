"use client";

import { useEffect, useRef } from "react";
import { useRoomContext } from "@livekit/components-react";
import {
  RoomEvent,
  Track,
  type RemoteTrack,
  type RemoteTrackPublication,
} from "livekit-client";

type Analysers = { you?: AnalyserNode; agent?: AnalyserNode };

const COLORS = { you: "#4ea1ff", agent: "#7ee2a8" } as const;

/**
 * Live level/waveform view for the local microphone and the agent's audio.
 * Purely observational: it reads the existing published tracks and never
 * creates a second capture or playback path.
 */
export function AudioWaveform() {
  const room = useRoomContext();
  const canvasRef = useRef<HTMLCanvasElement | null>(null);

  useEffect(() => {
    const canvas = canvasRef.current;
    if (!canvas) return;
    const Ctor = window.AudioContext ?? (window as unknown as { webkitAudioContext?: typeof AudioContext }).webkitAudioContext;
    if (!Ctor) return;
    const ctx = new Ctor();
    const analysers: Analysers = {};
    const attached = new Set<string>();

    const attach = (which: keyof Analysers, stream: MediaStream) => {
      try {
        const source = ctx.createMediaStreamSource(stream);
        const analyser = ctx.createAnalyser();
        analyser.fftSize = 512;
        analyser.smoothingTimeConstant = 0.6;
        source.connect(analyser);
        analysers[which] = analyser;
      } catch {
        // A track may end between the event and this call; ignore it.
      }
    };

    const attachLocalMic = () => {
      const publication = room.localParticipant.getTrackPublication(Track.Source.Microphone);
      const track = publication?.track;
      if (!track?.mediaStreamTrack || attached.has("you")) return;
      attached.add("you");
      attach("you", new MediaStream([track.mediaStreamTrack]));
    };

    const attachAgent = (track: RemoteTrack, publication: RemoteTrackPublication) => {
      if (publication.source !== Track.Source.Microphone || attached.has("agent")) return;
      attached.add("agent");
      attach("agent", new MediaStream([track.mediaStreamTrack]));
    };

    room.on(RoomEvent.LocalTrackPublished, attachLocalMic);
    room.on(RoomEvent.TrackSubscribed, attachAgent);
    attachLocalMic();
    for (const participant of room.remoteParticipants.values()) {
      for (const publication of participant.trackPublications.values()) {
        if (publication.source === Track.Source.Microphone && publication.track) {
          attachAgent(publication.track as RemoteTrack, publication as RemoteTrackPublication);
        }
      }
    }

    const buffer = new Uint8Array(512);
    let frame = 0;
    const draw = () => {
      frame = requestAnimationFrame(draw);
      const context = canvas.getContext("2d");
      if (!context) return;
      const width = canvas.clientWidth;
      const height = canvas.clientHeight;
      if (canvas.width !== width || canvas.height !== height) {
        canvas.width = width;
        canvas.height = height;
      }
      context.clearRect(0, 0, width, height);
      const rows: Array<[keyof Analysers, string, number]> = [
        ["you", "You", 0],
        ["agent", "4G", height / 2],
      ];
      for (const [key, label, top] of rows) {
        const rowHeight = height / 2;
        const analyser = analysers[key];
        context.fillStyle = "#ffffff10";
        context.fillRect(0, top, width, rowHeight);
        context.fillStyle = "#9aa4b2";
        context.font = "11px system-ui";
        context.fillText(label, 6, top + 13);
        if (!analyser) {
          context.fillStyle = "#5c6675";
          context.fillText("no audio", 6, top + rowHeight / 2 + 4);
          continue;
        }
        analyser.getByteTimeDomainData(buffer);
        context.beginPath();
        context.strokeStyle = COLORS[key];
        context.lineWidth = 1.5;
        const mid = top + rowHeight / 2;
        const step = width / buffer.length;
        let peak = 0;
        for (let i = 0; i < buffer.length; i += 1) {
          const value = (buffer[i] - 128) / 128;
          peak = Math.max(peak, Math.abs(value));
          const x = i * step;
          const y = mid + value * (rowHeight / 2 - 6);
          if (i === 0) context.moveTo(x, y);
          else context.lineTo(x, y);
        }
        context.stroke();
        // Level bar so quiet speech is still visible at a glance.
        context.fillStyle = COLORS[key];
        context.fillRect(width - 4, mid - (rowHeight / 2 - 8) * peak, 3, (rowHeight - 16) * peak);
      }
    };
    draw();

    return () => {
      cancelAnimationFrame(frame);
      room.off(RoomEvent.LocalTrackPublished, attachLocalMic);
      room.off(RoomEvent.TrackSubscribed, attachAgent);
      void ctx.close().catch(() => undefined);
    };
  }, [room]);

  return (
    <section className="audio-waveform" aria-label="Microphone and agent levels">
      <div className="status">
        <div>
          <p className="eyebrow">Live audio</p>
          <h2>Who is talking</h2>
        </div>
      </div>
      <canvas ref={canvasRef} style={{ width: "100%", height: 120, borderRadius: 8 }} />
      <p className="subtle">Shows captured microphone audio and the agent&apos;s published speech. It is a level view, not a measurement of playout.</p>
    </section>
  );
}
