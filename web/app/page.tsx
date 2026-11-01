"use client";

import { useState } from "react";
import { PresentationPreview } from "./presentation-preview";
import {
  BarVisualizer,
  MediaDeviceSelect,
  LiveKitRoom,
  RoomAudioRenderer,
  useTranscriptions,
  useVoiceAssistant,
  VoiceAssistantControlBar,
  useRoomContext,
} from "@livekit/components-react";

type RoomGrant = { token: string; serverUrl: string; roomName: string };
const QWEN_TTS_VOICES = [
  { id: "", label: "Configured default" },
  { id: "loongjohn", label: "Loong John" },
  { id: "longanhuan_v3.6", label: "Long An Huan" },
] as const;

function RoomConversation() {
  const { state, audioTrack } = useVoiceAssistant();
  const transcriptions = useTranscriptions();
  const room = useRoomContext();
  const [text, setText] = useState("");
  const [isSending, setSending] = useState(false);
  const [sendError, setSendError] = useState("");

  async function sendText(event: React.FormEvent<HTMLFormElement>) {
    event.preventDefault();
    const message = text.trim();
    if (!message || isSending) return;
    setSending(true);
    setSendError("");
    try {
      await room.localParticipant.sendText(message, { topic: "lk.chat" });
      setText("");
    } catch {
      setSendError("Message not sent. Check the room connection.");
    } finally {
      setSending(false);
    }
  }

  return (
    <section className="conversation">
      <div className="status">
        <div>
          <p className="eyebrow">Voice agent</p>
          <h2>{state ?? "connecting"}</h2>
        </div>
        {audioTrack ? <BarVisualizer state={state} trackRef={audioTrack} barCount={14} /> : null}
      </div>
      <VoiceAssistantControlBar saveUserChoices={false} />
      <fieldset className="device-chooser">
        <legend>Audio devices</legend>
        <label>Microphone <MediaDeviceSelect kind="audioinput" /></label>
        <label>Speaker <MediaDeviceSelect kind="audiooutput" /></label>
        <p className="subtle">Use any system-supported device. Some browsers cannot switch speaker output.</p>
      </fieldset>
      <div className="presence" aria-live="polite">
        Conversation state: {state === "listening" || state === "speaking" ? state : "idle"}
      </div>
      <div className="transcript" aria-live="polite" aria-label="Conversation transcript">
        {transcriptions.length === 0 ? (
          <p className="placeholder">Speak naturally or type below to start a conversation.</p>
        ) : (
          transcriptions.map((item, i) => (
            <p key={item.streamInfo?.id ?? i}>{item.text}</p>
          ))
        )}
      </div>
      <form onSubmit={sendText} className="message-form">
        <label htmlFor="message">Text alternative</label>
        <div className="send-row">
          <input
            id="message"
            value={text}
            onChange={(e) => setText(e.target.value)}
            placeholder="Write a message…"
            maxLength={4000}
          />
          <button type="submit" disabled={!text.trim() || isSending}>
            {isSending ? "Sending…" : "Send"}
          </button>
        </div>
        {sendError ? <p className="error">{sendError}</p> : null}
      </form>
      <RoomAudioRenderer />
    </section>
  );
}

export default function Home() {
  const [accessToken, setAccessToken] = useState("");
  const [ttsVoice, setTtsVoice] = useState<(typeof QWEN_TTS_VOICES)[number]["id"]>("");
  const [room, setRoom] = useState<RoomGrant | null>(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState("");

  async function start() {
    setError("");
    setLoading(true);
    try {
      const response = await fetch("/api/session", {
        method: "POST",
        cache: "no-store",
        headers: { Authorization: `Bearer ${accessToken}`, "Content-Type": "application/json" },
        body: JSON.stringify(ttsVoice ? { ttsVoice } : {}),
      });
      if (!response.ok) throw new Error(response.status === 401
        ? "Invalid local development access token."
        : "Could not start a session. Check the LiveKit and worker configuration.");
      const grant: RoomGrant = await response.json();
      setRoom(grant);
      setAccessToken("");
    } catch (e) {
      setError(e instanceof Error ? e.message : "Unable to connect.");
    } finally {
      setLoading(false);
    }
  }

  return (
    <main className="shell">
      <header>
        <p className="eyebrow">AgenticForge Labs</p>
        <h1>Forge Conversationalist</h1>
        <p className="intro">A voice interface with no robot required. LiveKit-powered, cloud-inference conversation.</p>
      </header>
      {!room ? (
        <section className="onboard">
          <label htmlFor="access-token">Local development access token</label>
          <input
            id="access-token"
            type="password"
            autoComplete="off"
            value={accessToken}
            onChange={(e) => setAccessToken(e.target.value)}
            placeholder="Configured in web/.env.local"
          />
          <label htmlFor="tts-voice">Voice</label>
          <select id="tts-voice" value={ttsVoice} onChange={(event) => setTtsVoice(event.target.value as typeof ttsVoice)}>
            {QWEN_TTS_VOICES.map((voice) => <option key={voice.id} value={voice.id}>{voice.label}</option>)}
          </select>
          <p className="subtle">Qwen voices apply when OpenRouter Qwen TTS is configured. Configured default keeps the worker’s voice setting.</p>
          <button disabled={!accessToken || loading} onClick={start}>
            {loading ? "Connecting…" : "Start conversation"}
          </button>
          {error ? <p className="error" role="alert">{error}</p> : null}
          <p className="subtle">LiveKit Cloud processes your voice through the selected AI services. No transcript storage is implemented here.</p>
        </section>
      ) : (
        <>
          <div className="room-meta">
            <span>Connected room: {room.roomName.slice(0, 28)}</span>
            <button className="secondary" onClick={() => setRoom(null)}>End conversation</button>
          </div>
          <LiveKitRoom
            serverUrl={room.serverUrl}
            token={room.token}
            connect={true}
            audio={true}
            video={false}
            onDisconnected={() => setRoom(null)}
            onError={() => setError("The LiveKit connection failed.")}
          >
            <RoomConversation />
          </LiveKitRoom>
          {error ? <p className="error" role="alert">{error}</p> : null}
        </>
      )}
      <PresentationPreview />
    </main>
  );
}
