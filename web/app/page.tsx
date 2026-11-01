"use client";

import { useEffect, useRef, useState } from "react";
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

type RoomSpeechSettings = {
  models: OpenRouterTtsModel[];
  catalogError: string;
  model: string;
  voice: string;
  onChange: (model: string, voice: string) => Promise<void>;
  vadSensitivity: string;
  onVadSensitivityChange: (value: string) => void;
};

function RoomConversation({ speech }: { speech: RoomSpeechSettings }) {
  const { state, audioTrack } = useVoiceAssistant();
  const transcriptions = useTranscriptions();
  const room = useRoomContext();
  const [text, setText] = useState("");
  const [isSending, setSending] = useState(false);
  const [sendError, setSendError] = useState("");
  const [speechError, setSpeechError] = useState("");
  const [changingSpeech, setChangingSpeech] = useState(false);
  const selectedModel = speech.models.find((model) => model.id === speech.model);

  async function selectSpeech(model: string, voice: string) {
    setChangingSpeech(true);
    setSpeechError("");
    try {
      const destination = [...room.remoteParticipants.values()].find((participant) =>
        participant.identity.startsWith("agent-"),
      );
      if (!destination) throw new Error("The voice agent is not connected yet.");
      const result = await room.localParticipant.performRpc({
        destinationIdentity: destination.identity,
        method: "forge.change_speech",
        payload: JSON.stringify({ tts_model: model || null, tts_voice: model ? voice : null }),
        responseTimeout: 20_000,
      });
      const confirmed = JSON.parse(result) as { model: string; voice: string };
      speech.onChange(
        speech.models.some((item) => item.id === confirmed.model) ? confirmed.model : "",
        speech.models.some((item) => item.id === confirmed.model) ? confirmed.voice : "",
      );
    } catch (error) {
      setSpeechError(error instanceof Error ? error.message : "Could not change speech settings.");
    } finally {
      setChangingSpeech(false);
    }
  }

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
      <fieldset className="device-chooser">
        <legend>Microphone sensitivity</legend>
        <label htmlFor="vad-sensitivity">Speech detection</label>
        <select
          id="vad-sensitivity"
          value={speech.vadSensitivity}
          onChange={(event) => speech.onVadSensitivityChange(event.target.value)}
        >
          <option value="less">Less sensitive (fewer noise triggers)</option>
          <option value="standard">Standard</option>
          <option value="more">More sensitive</option>
        </select>
        <p className="subtle">Applies on the next conversation. Less sensitive requires stronger speech cues and may miss quiet speech; more sensitive can react to background noise.</p>
      </fieldset>
      <fieldset className="device-chooser" disabled={changingSpeech}>
        <legend>Speech model and voice</legend>
        {speech.catalogError && <p className="subtle" role="status">{speech.catalogError}</p>}
        <label htmlFor="tts-model">Speech model</label>
        <select
          id="tts-model"
          value={speech.model}
          onChange={(event) => {
            const next = speech.models.find((model) => model.id === event.target.value);
            void selectSpeech(event.target.value, next?.voices[0] ?? "");
          }}
        >
          <option value="">Configured default</option>
          {speech.models.map((model) => (
            <option key={model.id} value={model.id} disabled={!model.selectable}>
              {model.name} — ${model.pricePerMillion.toLocaleString(undefined, { maximumFractionDigits: 4 })} / million {model.priceUnit}{model.selectable ? "" : " (no published voices; unavailable)"}
            </option>
          ))}
        </select>
        <label htmlFor="tts-voice">Supported voice</label>
        <select
          id="tts-voice"
          value={speech.voice}
          disabled={!selectedModel?.selectable || changingSpeech}
          onChange={(event) => void selectSpeech(speech.model, event.target.value)}
        >
          {selectedModel?.selectable
            ? selectedModel.voices.map((voice) => <option key={voice} value={voice}>{voice}</option>)
            : <option value="">Configured default voice</option>}
        </select>
        {selectedModel && <p className="subtle">{selectedModel.formatVerified ? `Output format ${selectedModel.responseFormat} documented; live audio support varies by model.` : "Output format compatibility is unverified; MP3 is requested."}</p>}
        {changingSpeech && <p className="subtle" role="status">Changing voice…</p>}
        {speechError && <p className="error" role="alert">{speechError}</p>}
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
  const [authChecking, setAuthChecking] = useState(true);
  const [hasSavedAccess, setHasSavedAccess] = useState(false);
  const startRef = useRef<() => Promise<void>>(async () => {});
  const autoStartRef = useRef(false);

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

  useEffect(() => {
    let cancelled = false;
    fetch("/api/session", { cache: "no-store", credentials: "same-origin" })
      .then((response) => {
        if (cancelled) return;
        setAuthChecking(false);
        setHasSavedAccess(response.ok);
        if (response.ok && !autoStartRef.current) {
          autoStartRef.current = true;
          void startRef.current();
        }
      })
      .catch(() => { if (!cancelled) setAuthChecking(false); });
    return () => { cancelled = true; };
  }, []);

  async function start(credential?: string) {
    setError("");
    setLoading(true);
    try {
      const response = await fetch("/api/session", {
        method: "POST",
        cache: "no-store",
        credentials: "same-origin",
        headers: {
          ...((credential ?? accessToken) ? { Authorization: `Bearer ${credential ?? accessToken}` } : {}),
          "Content-Type": "application/json",
        },
        body: JSON.stringify({
          ...(ttsModel ? { ttsModel, ttsVoice } : {}),
          vadSensitivity,
        }),
      });
      if (!response.ok) {
        if (response.status === 401 && !credential) setHasSavedAccess(false);
        throw new Error(response.status === 401
          ? "Invalid local development access token."
          : "Could not start a session. Check the LiveKit and worker configuration.");
      }
      const grant: RoomGrant = await response.json();
      setRoom(grant);
      setHasSavedAccess(true);
      setAccessToken("");
    } catch (e) {
      setError(e instanceof Error ? e.message : "Unable to connect.");
    } finally {
      setLoading(false);
    }
  }

  startRef.current = () => start("");

  async function changeSpeech(model: string, voice: string) {
    setTtsModel(model);
    setTtsVoice(voice);
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
          {authChecking ? <p className="subtle">Restoring local session…</p> : (
            !hasSavedAccess && <>
              <label htmlFor="access-token">Local development access token</label>
              <input
                id="access-token"
                type="password"
                autoComplete="off"
                value={accessToken}
                onChange={(e) => setAccessToken(e.target.value)}
                placeholder="Enter once; saved securely for this site"
              />
            </>
          )}
          <button disabled={(!hasSavedAccess && !accessToken) || loading || authChecking} onClick={() => void start(hasSavedAccess ? "" : undefined)}>
            {loading ? "Connecting…" : hasSavedAccess ? "Start conversation" : "Continue"}
          </button>
          {error ? <p className="error" role="alert">{error}</p> : null}
          <p className="subtle">Your local development token is kept in an HttpOnly site cookie. No transcript storage is implemented here.</p>
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
            <RoomConversation speech={{ models: ttsModels, catalogError: ttsCatalogError, model: ttsModel, voice: ttsVoice, onChange: changeSpeech, vadSensitivity, onVadSensitivityChange: setVadSensitivity }} />
          </LiveKitRoom>
          {error ? <p className="error" role="alert">{error}</p> : null}
        </>
      )}
      <PresentationPreview />
    </main>
  );
}
