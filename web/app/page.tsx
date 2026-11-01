"use client";

import { useEffect, useState } from "react";
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
import type { OpenRouterTtsModel } from "@/lib/openrouter-tts";

type RoomGrant = { token: string; serverUrl: string; roomName: string };

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
  const [ttsModels, setTtsModels] = useState<OpenRouterTtsModel[]>([]);
  const [ttsCatalogError, setTtsCatalogError] = useState("");
  const [ttsModel, setTtsModel] = useState("");
  const [ttsVoice, setTtsVoice] = useState("");
  const [vadSensitivity, setVadSensitivity] = useState("standard");
  const [room, setRoom] = useState<RoomGrant | null>(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState("");

  useEffect(() => {
    let cancelled = false;
    fetch("/api/tts-models", { cache: "no-store" })
      .then(async (response) => {
        if (!response.ok) throw new Error("catalog unavailable");
        return response.json() as Promise<{ models: OpenRouterTtsModel[] }>;
      })
      .then(({ models }) => { if (!cancelled) setTtsModels(models); })
      .catch(() => { if (!cancelled) setTtsCatalogError("Could not load OpenRouter speech models. The configured voice remains available."); });
    return () => { cancelled = true; };
  }, []);

  const selectedTtsModel = ttsModels.find((model) => model.id === ttsModel);

  async function start() {
    setError("");
    setLoading(true);
    try {
      const response = await fetch("/api/session", {
        method: "POST",
        cache: "no-store",
        headers: { Authorization: `Bearer ${accessToken}`, "Content-Type": "application/json" },
        body: JSON.stringify({
          ...(ttsModel ? { ttsModel, ttsVoice } : {}),
          vadSensitivity,
        }),
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
          <label htmlFor="tts-model">Speech model (OpenRouter, up to $20 per million characters/bytes)</label>
          <select id="tts-model" value={ttsModel} onChange={(event) => {
            const nextModel = ttsModels.find((model) => model.id === event.target.value);
            setTtsModel(event.target.value);
            setTtsVoice(nextModel?.voices[0] ?? "");
          }}>
            <option value="">Configured default</option>
            {ttsModels.map((model) => (
              <option key={model.id} value={model.id} disabled={!model.selectable}>
                {model.name} — ${model.pricePerMillion.toLocaleString(undefined, { maximumFractionDigits: 4 })} / million {model.priceUnit}{model.selectable ? "" : " (no published voices; unavailable)"}
              </option>
            ))}
          </select>
          {ttsCatalogError && <p className="subtle" role="status">{ttsCatalogError}</p>}
          <label htmlFor="tts-voice">Supported voice</label>
          <select id="tts-voice" value={ttsVoice} disabled={!selectedTtsModel?.selectable} onChange={(event) => setTtsVoice(event.target.value)}>
            {selectedTtsModel?.selectable
              ? selectedTtsModel.voices.map((voice) => <option key={voice} value={voice}>{voice}</option>)
              : <option value="">Choose a selectable speech model</option>}
          </select>
          {selectedTtsModel && <p className="subtle">{selectedTtsModel.formatVerified ? `Output format ${selectedTtsModel.responseFormat} documented; this app path is not yet live-tested for this model.` : "Output format compatibility is unverified; MP3 is requested."}</p>}
          <label htmlFor="vad-sensitivity">Microphone sensitivity</label>
          <select id="vad-sensitivity" value={vadSensitivity} onChange={(event) => setVadSensitivity(event.target.value)}>
            <option value="less">Less sensitive (fewer noise triggers)</option>
            <option value="standard">Standard</option>
            <option value="more">More sensitive</option>
          </select>
          <p className="subtle">Less sensitive requires stronger speech cues and may miss quiet speech; more sensitive can react to background noise.</p>
          <button disabled={!accessToken || loading || Boolean(ttsModel && !ttsVoice)} onClick={start}>
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
