"use client";

import { useEffect, useRef, useState } from "react";
import { PresentationPreview } from "./presentation-preview";
import { RobotControls } from "./robot-controls";
import { AudioWaveform } from "./audio-waveform";
import {
  BarVisualizer,
  LiveKitRoom,
  RoomAudioRenderer,
  useTranscriptions,
  useVoiceAssistant,
  VoiceAssistantControlBar,
  useMediaDeviceSelect,
  useRoomContext,
} from "@livekit/components-react";
import type { OpenRouterTtsModel } from "@/lib/openrouter-tts";
import { ConnectionState, createLocalAudioTrack, RoomEvent, type LocalAudioTrack, type RemoteParticipant } from "livekit-client";
import { PressToTalkController, pttCaptureOptions } from "@/lib/press-to-talk";
import { canUseRoomInput, describeSessionStartFailure, endBoundSession, shouldEnableContinuousMicrophone } from "@/lib/session-actions.mjs";
import { MICROPHONE_MODE_KEY, parseMicrophoneMode } from "@/lib/microphone-mode.mjs";
import { combineTranscript, parseTranscriptResponse, shouldRestoreTranscript } from "@/lib/transcript.mjs";
import type { TranscriptMessage } from "@/lib/transcript";

type RoomGrant = { token: string; serverUrl: string; roomName: string; resumed: boolean };

type RoomSpeechSettings = {
  models: OpenRouterTtsModel[];
  catalogError: string;
  model: string;
  voice: string;
  onChange: (model: string, voice: string) => Promise<void>;
  onEditStart: () => void;
  editRevision: number;
  vadSensitivity: string;
  onVadSensitivityChange: (value: string) => void;
  turnPacing: string;
  onTurnPacingChange: (value: string) => void;
  bargeIn: string;
  onBargeInChange: (value: string) => void;
  resumeFalseInterruption: boolean;
  onResumeFalseInterruptionChange: (value: boolean) => void;
};

type MicrophoneMode = "continuous" | "press-to-talk";

function AudioDevicePicker({ kind, label }: { kind: "audioinput" | "audiooutput"; label: string }) {
  const { devices, activeDeviceId, setActiveMediaDevice } = useMediaDeviceSelect({
    kind,
    requestPermissions: false,
  });
  const [error, setError] = useState("");

  return (
    <label>
      {label}
      <select
        value={activeDeviceId}
        onChange={(event) => {
          setError("");
          void setActiveMediaDevice(event.target.value, { exact: true }).catch(() => {
            setError(`Could not switch ${label.toLowerCase()}.`);
          });
        }}
      >
        {(devices.length === 0 || !devices.some((device) => device.deviceId === activeDeviceId)) && (
          <option value={activeDeviceId}>
            {activeDeviceId === "default" ? "Default device" : "Current device (unavailable)"}
          </option>
        )}
        {devices.map((device) => (
          <option key={device.deviceId} value={device.deviceId}>
            {device.label || `${label} ${device.deviceId.slice(0, 8)}`}
          </option>
        ))}
      </select>
      {error && <span className="error" role="alert">{error}</span>}
    </label>
  );
}

function RoomConversation({ speech, microphoneMode, onMicrophoneModeChange, ending, onEnd, resumed }: {
  speech: RoomSpeechSettings;
  microphoneMode: MicrophoneMode;
  onMicrophoneModeChange: (mode: MicrophoneMode) => void;
  ending: boolean;
  onEnd: () => void;
  resumed: boolean;
}) {
  const { state, audioTrack } = useVoiceAssistant();
  const transcriptions = useTranscriptions();
  const room = useRoomContext();
  const [text, setText] = useState("");
  const [isSending, setSending] = useState(false);
  const [sendError, setSendError] = useState("");
  const [speechError, setSpeechError] = useState("");
  const [changingSpeech, setChangingSpeech] = useState(false);
  const [pttHeld, setPttHeld] = useState(false);
  const [microphoneError, setMicrophoneError] = useState("");
  const [restoredTranscript, setRestoredTranscript] = useState<TranscriptMessage[]>([]);
  const [transcriptRestoreStatus, setTranscriptRestoreStatus] = useState<"" | "loading" | "unavailable">(resumed ? "loading" : "");
  const [requiresTranscriptHandshake, setRequiresTranscriptHandshake] = useState(resumed);
  const [transcriptHandshakeComplete, setTranscriptHandshakeComplete] = useState(!resumed);
  const transcriptHandshakeRef = useRef(!resumed);
  const pttHeldRef = useRef(false);
  const pttControllerRef = useRef<PressToTalkController<LocalAudioTrack> | null>(null);
  const retryTranscriptRestoreRef = useRef<() => void>(() => {});
  const microphoneModeRef = useRef(microphoneMode);
  microphoneModeRef.current = microphoneMode;
  const speechEditRevisionRef = useRef(speech.editRevision);
  speechEditRevisionRef.current = speech.editRevision;
  const selectedModel = speech.models.find((model) => model.id === speech.model);
  const transcriptRows = combineTranscript(restoredTranscript, transcriptions, room.localParticipant.identity);
  const roomInputReady = canUseRoomInput(requiresTranscriptHandshake, transcriptHandshakeComplete);
  transcriptHandshakeRef.current = roomInputReady;

  if (!pttControllerRef.current) {
    pttControllerRef.current = new PressToTalkController(
      () => createLocalAudioTrack(pttCaptureOptions(room)),
      (track) => room.localParticipant.publishTrack(track),
      (track) => room.localParticipant.unpublishTrack(track),
    );
  }

  async function setPtt(enabled: boolean) {
    if (enabled === pttHeldRef.current) return;
    if (enabled && !transcriptHandshakeRef.current) return;
    pttHeldRef.current = enabled;
    setPttHeld(enabled);
    setMicrophoneError("");
    try {
      if (enabled) await pttControllerRef.current?.press();
      else await pttControllerRef.current?.release();
    } catch {
      pttHeldRef.current = false;
      setPttHeld(false);
      setMicrophoneError("Could not start the microphone. Check browser permission and try again.");
    }
  }

  async function endRoom() {
    pttHeldRef.current = false;
    setPttHeld(false);
    await pttControllerRef.current?.release();
    try {
      await room.localParticipant.setMicrophoneEnabled(false);
    } catch {
      setMicrophoneError("The room is ending; microphone shutdown may take a moment.");
    }
    onEnd();
  }

  async function setMicrophoneEnabled(enabled: boolean) {
    try {
      await room.localParticipant.setMicrophoneEnabled(enabled);
    } catch {
      setMicrophoneError(enabled
        ? "Could not start the microphone. Check browser permission and try again."
        : "Could not stop microphone capture.");
    }
  }

  async function selectSpeech(model: string, voice: string) {
    speech.onEditStart();
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
    if (!transcriptHandshakeRef.current) {
      setSendError("Reconnecting… wait until the conversation is ready before sending.");
      return;
    }
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

  useEffect(() => () => {
    pttHeldRef.current = false;
    void pttControllerRef.current?.release();
  }, [room]);

  useEffect(() => {
    const releasePtt = () => {
      if (!pttHeldRef.current) return;
      pttHeldRef.current = false;
      setPttHeld(false);
      void pttControllerRef.current?.release();
    };
    const releaseWhenHidden = () => {
      if (document.visibilityState === "hidden") releasePtt();
    };
    window.addEventListener("blur", releasePtt);
    document.addEventListener("visibilitychange", releaseWhenHidden);
    return () => {
      window.removeEventListener("blur", releasePtt);
      document.removeEventListener("visibilitychange", releaseWhenHidden);
    };
  }, [room]);

  useEffect(() => {
    let cancelled = false;
    let waitingForAgent = false;
    let restoring = false;
    let restoreAgain = false;
    let mayRestore = resumed;

    async function restoreTranscript() {
      if (restoring) {
        restoreAgain = true;
        return;
      }
      transcriptHandshakeRef.current = false;
      setRequiresTranscriptHandshake(true);
      setTranscriptHandshakeComplete(false);
      setTranscriptRestoreStatus("loading");
      const agent = [...room.remoteParticipants.values()].find((participant) =>
        participant.identity.startsWith("agent-"),
      );
      if (!agent) {
        waitingForAgent = true;
        return;
      }
      waitingForAgent = false;
      restoring = true;
      try {
        const result = await room.localParticipant.performRpc({
          destinationIdentity: agent.identity,
          method: "forge.get_transcript",
          payload: "{}",
          responseTimeout: 8_000,
        });
        if (cancelled) return;
        const messages = parseTranscriptResponse(result);
        setRestoredTranscript(messages);
        setSendError("");
        transcriptHandshakeRef.current = true;
        setTranscriptHandshakeComplete(true);
        setTranscriptRestoreStatus("");
        if (shouldEnableContinuousMicrophone(microphoneModeRef.current, true)) {
          void setMicrophoneEnabled(true);
        }
      } catch {
        if (!cancelled) {
          transcriptHandshakeRef.current = false;
          setTranscriptHandshakeComplete(false);
          setTranscriptRestoreStatus("unavailable");
        }
      } finally {
        restoring = false;
        if (!cancelled && restoreAgain) {
          restoreAgain = false;
          void restoreTranscript();
        }
      }
    }
    retryTranscriptRestoreRef.current = () => { void restoreTranscript(); };

    const onParticipantConnected = (participant: RemoteParticipant) => {
      if (mayRestore && waitingForAgent && participant.identity.startsWith("agent-")) {
        void restoreTranscript();
      }
    };
    const onConnected = () => {
      if (shouldRestoreTranscript("connected", resumed)) void restoreTranscript();
    };
    const onReconnected = () => {
      mayRestore = true;
      if (shouldRestoreTranscript("reconnected", resumed)) void restoreTranscript();
    };
    const onReconnecting = () => {
      transcriptHandshakeRef.current = false;
      setRequiresTranscriptHandshake(true);
      setTranscriptHandshakeComplete(false);
      setTranscriptRestoreStatus("loading");
      pttHeldRef.current = false;
      setPttHeld(false);
      void pttControllerRef.current?.release();
      if (microphoneModeRef.current === "continuous") void setMicrophoneEnabled(false);
    };
    room.on(RoomEvent.Reconnecting, onReconnecting);
    room.on(RoomEvent.Connected, onConnected);
    room.on(RoomEvent.Reconnected, onReconnected);
    room.on(RoomEvent.ParticipantConnected, onParticipantConnected);
    if (room.state === ConnectionState.Connected && shouldRestoreTranscript("connected", resumed)) {
      void restoreTranscript();
    }
    else waitingForAgent = true;

    return () => {
      cancelled = true;
      room.off(RoomEvent.Reconnecting, onReconnecting);
      room.off(RoomEvent.Connected, onConnected);
      room.off(RoomEvent.Reconnected, onReconnected);
      room.off(RoomEvent.ParticipantConnected, onParticipantConnected);
    };
  }, [room, resumed]);

  useEffect(() => {
    const enableContinuousIfReady = () => {
      if (shouldEnableContinuousMicrophone(microphoneModeRef.current, transcriptHandshakeRef.current)) {
        void setMicrophoneEnabled(true);
      }
    };
    room.on(RoomEvent.Connected, enableContinuousIfReady);
    if (room.state === ConnectionState.Connected) enableContinuousIfReady();
    return () => {
      room.off(RoomEvent.Connected, enableContinuousIfReady);
    };
  }, [room]);

  useEffect(() => {
    if (!resumed) return;
    let cancelled = false;
    let waitingForAgent = false;
    let restoring = false;
    let revisionAtStart = speechEditRevisionRef.current;

    async function restoreSpeech() {
      if (restoring) return;
      const agent = [...room.remoteParticipants.values()].find((participant) =>
        participant.identity.startsWith("agent-"),
      );
      if (!agent) {
        waitingForAgent = true;
        return;
      }
      waitingForAgent = false;
      restoring = true;
      revisionAtStart = speechEditRevisionRef.current;
      try {
        const result = await room.localParticipant.performRpc({
          destinationIdentity: agent.identity,
          method: "forge.get_speech",
          payload: "{}",
          responseTimeout: 8_000,
        });
        if (cancelled || revisionAtStart !== speechEditRevisionRef.current) return;
        const settings = JSON.parse(result) as { model?: unknown; voice?: unknown };
        if (typeof settings.model === "string" && typeof settings.voice === "string") {
          await speech.onChange(settings.model, settings.voice);
        }
      } catch {
        if (!cancelled && revisionAtStart === speechEditRevisionRef.current) {
          setSpeechError("Could not restore the current speech settings; keeping the selected voice.");
        }
      } finally {
        restoring = false;
      }
    }

    const onParticipantConnected = (participant: RemoteParticipant) => {
      if (waitingForAgent && participant.identity.startsWith("agent-")) void restoreSpeech();
    };
    room.on(RoomEvent.Connected, restoreSpeech);
    room.on(RoomEvent.ParticipantConnected, onParticipantConnected);
    if (room.state === ConnectionState.Connected) void restoreSpeech();
    else waitingForAgent = true;
    return () => {
      cancelled = true;
      room.off(RoomEvent.Connected, restoreSpeech);
      room.off(RoomEvent.ParticipantConnected, onParticipantConnected);
    };
  }, [room, resumed]);

  return (
    <section className="conversation">
      <div className="status">
        <div>
          <p className="eyebrow">Voice agent</p>
          <h2>{state ?? "connecting"}</h2>
        </div>
        {audioTrack ? <BarVisualizer state={state} trackRef={audioTrack} barCount={14} /> : null}
        <button className="secondary" disabled={ending} onClick={() => void endRoom()}>
          {ending ? "Ending…" : "End conversation"}
        </button>
      </div>
      <VoiceAssistantControlBar saveUserChoices={false} controls={{ microphone: shouldEnableContinuousMicrophone(microphoneMode, roomInputReady) }} />
      <fieldset className="device-chooser">
        <legend>Microphone mode</legend>
        <label htmlFor="microphone-mode">How to send audio</label>
        <select
          id="microphone-mode"
          value={microphoneMode}
          onChange={(event) => {
            const mode = event.target.value as MicrophoneMode;
            microphoneModeRef.current = mode;
            pttHeldRef.current = false;
            setPttHeld(false);
            void (async () => {
              await pttControllerRef.current?.release();
              await setMicrophoneEnabled(shouldEnableContinuousMicrophone(mode, roomInputReady));
            })();
            onMicrophoneModeChange(mode);
          }}
        >
          <option value="continuous">Continuous listening</option>
          <option value="press-to-talk">Press to talk</option>
        </select>
        {microphoneMode === "press-to-talk" && (
          <>
            <button
              type="button"
              disabled={!roomInputReady}
              aria-pressed={pttHeld}
              onPointerDown={(event) => {
                event.preventDefault();
                event.currentTarget.setPointerCapture(event.pointerId);
                void setPtt(true);
              }}
              onPointerUp={() => void setPtt(false)}
              onPointerCancel={() => void setPtt(false)}
              onLostPointerCapture={() => void setPtt(false)}
              onBlur={() => void setPtt(false)}
              onKeyDown={(event) => {
                if ((event.key === " " || event.key === "Enter") && !event.repeat) {
                  event.preventDefault();
                  void setPtt(true);
                }
              }}
              onKeyUp={(event) => {
                if (event.key === " " || event.key === "Enter") {
                  event.preventDefault();
                  void setPtt(false);
                }
              }}
            >
              {pttHeld ? "Listening… release to send" : "Hold to talk"}
            </button>
            <p className="subtle" role="status">Microphone audio is sent only while you hold this button. Release it to stop capture.</p>
          </>
        )}
        {microphoneError && <p className="error" role="alert">{microphoneError}</p>}
      </fieldset>
      <fieldset className="device-chooser">
        <legend>Audio devices</legend>
        <AudioDevicePicker kind="audioinput" label="Microphone" />
        <AudioDevicePicker kind="audiooutput" label="Speaker" />
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
        <label htmlFor="turn-pacing">Wait after you stop speaking</label>
        <select id="turn-pacing" value={speech.turnPacing} onChange={(event) => speech.onTurnPacingChange(event.target.value)}>
          <option value="quick">Shorter pause</option>
          <option value="standard">Standard</option>
          <option value="patient">Longer pause</option>
        </select>
        <p className="subtle">Applies on the next conversation. A longer pause gives you more time to think before 4G responds.</p>
        <label htmlFor="barge-in">Interrupt 4G while it is speaking</label>
        <select id="barge-in" value={speech.bargeIn} onChange={(event) => speech.onBargeInChange(event.target.value)}>
          <option value="off">Off — let 4G finish</option>
          <option value="hard">Hard — needs a few clear words</option>
          <option value="standard">Standard — one short word</option>
          <option value="easy">Easily — any speech</option>
        </select>
        <p className="subtle">Applies on the next conversation. &quot;Hard&quot; stops accidental cut-offs from coughs and background noise.</p>
        <label>
          <input
            type="checkbox"
            checked={speech.resumeFalseInterruption}
            onChange={(event) => speech.onResumeFalseInterruptionChange(event.target.checked)}
          />{" "}
          Resume speaking if the interruption was a false alarm
        </label>
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
      <RobotControls />
      <div className="transcript" aria-live="polite" aria-label="Conversation transcript">
        {transcriptRestoreStatus === "loading" && <p className="subtle" role="status">Reconnecting… restoring the conversation.</p>}
        {transcriptRows.length === 0 ? (
          <p className="placeholder">Speak naturally or type below to start a conversation.</p>
        ) : (
          transcriptRows.map((item) => (
            <p key={item.id}><strong>{item.role === "user" ? "You" : "Agent"}:</strong> {item.text}</p>
          ))
        )}
        {transcriptRestoreStatus === "unavailable" && (
          <div role="status">
            <p className="subtle">Reconnection is not ready. Retry before sending audio or text.</p>
            <button type="button" className="secondary" onClick={() => retryTranscriptRestoreRef.current()}>Retry reconnect</button>
          </div>
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
          <button type="submit" disabled={!text.trim() || isSending || !roomInputReady}>
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
  const [speechEditRevision, setSpeechEditRevision] = useState(0);
  const [vadSensitivity, setVadSensitivity] = useState("standard");
  const [turnPacing, setTurnPacing] = useState("standard");
  const [bargeIn, setBargeIn] = useState("hard");
  const [resumeFalseInterruption, setResumeFalseInterruption] = useState(true);
  const [microphoneMode, setMicrophoneMode] = useState<MicrophoneMode>("press-to-talk");
  const [microphoneModeReady, setMicrophoneModeReady] = useState(false);
  const [room, setRoom] = useState<RoomGrant | null>(null);
  const [loading, setLoading] = useState(false);
  const [ending, setEnding] = useState(false);
  const [error, setError] = useState("");
  const [authChecking, setAuthChecking] = useState(true);
  const [hasSavedAccess, setHasSavedAccess] = useState(false);
  const [canResetSession, setCanResetSession] = useState(false);
  const startRef = useRef<() => Promise<void>>(async () => {});
  const autoStartRef = useRef(false);

  useEffect(() => {
    try {
      setMicrophoneMode(parseMicrophoneMode(window.localStorage.getItem(MICROPHONE_MODE_KEY)));
    } catch {
      setMicrophoneMode("press-to-talk");
    } finally {
      setMicrophoneModeReady(true);
    }
  }, []);

  useEffect(() => {
    let cancelled = false;
    fetch("/api/tts-models", { cache: "no-store" })
      .then(async (response) => {
        if (!response.ok) throw new Error("catalog unavailable");
        return response.json() as Promise<{ models: OpenRouterTtsModel[] }>;
      })
      .then(({ models }) => {
        if (cancelled) return;
        setTtsModels(models);
        const heygenTurbo = models.find((model) => model.id === "heygen/heygen-voice-1-turbo");
        if (heygenTurbo?.voices.includes("lucian")) {
          setTtsModel((current) => current || heygenTurbo.id);
          setTtsVoice((current) => current || "lucian");
        }
      })
      .catch(() => { if (!cancelled) setTtsCatalogError("Could not load OpenRouter speech models. The configured voice remains available."); });
    return () => { cancelled = true; };
  }, []);

  useEffect(() => {
    if (!microphoneModeReady) return;
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
  }, [microphoneModeReady]);

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
          turnPacing,
          interruption: {
            enabled: bargeIn !== "off",
            mode: "vad",
            min_words: bargeIn === "hard" ? 3 : bargeIn === "standard" ? 1 : 0,
            min_duration: bargeIn === "hard" ? 0.6 : bargeIn === "standard" ? 0.3 : 0.1,
            resume_false_interruption: resumeFalseInterruption,
          },
        }),
      });
      const responseBody = await response.json().catch(() => ({})) as Record<string, unknown>;
      if (!response.ok) {
        if (response.status === 401 && !credential) setHasSavedAccess(false);
        const failure = describeSessionStartFailure(response.status, responseBody);
        setCanResetSession(failure.requiresReset);
        throw new Error(failure.message);
      }
      const grant = responseBody as unknown as RoomGrant;
      setRoom(grant);
      setCanResetSession(false);
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
    setSpeechEditRevision((revision) => revision + 1);
    setTtsModel(model);
    setTtsVoice(voice);
  }

  function changeMicrophoneMode(mode: MicrophoneMode) {
    setMicrophoneMode(mode);
    try {
      window.localStorage.setItem(MICROPHONE_MODE_KEY, mode);
    } catch {
      // Keep the current conversation usable if storage is unavailable.
    }
  }

  async function endConversation() {
    setEnding(true);
    setError("");
    try {
      await endBoundSession();
      setRoom(null);
      setCanResetSession(false);
    } catch (endError) {
      setError(endError instanceof Error ? endError.message : "Could not end the conversation.");
    } finally {
      setEnding(false);
    }
  }

  return (
    <main className="shell" data-lk-theme="default">
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
          <button disabled={(!hasSavedAccess && !accessToken) || loading || authChecking || canResetSession} onClick={() => void start(hasSavedAccess ? "" : undefined)}>
            {loading ? "Connecting…" : hasSavedAccess ? "Start conversation" : "Continue"}
          </button>
          {canResetSession && (
            <button className="secondary" disabled={ending} onClick={() => void endConversation()}>
              {ending ? "Ending stale session…" : "End stale conversation and reset"}
            </button>
          )}
          {error ? <p className="error" role="alert">{error}</p> : null}
          <p className="subtle">Your local development token is kept in an HttpOnly site cookie. Conversation history stays in the running agent and is not saved to disk.</p>
        </section>
      ) : (
        <>
          <div className="room-meta">
            <span>Connected room: {room.roomName.slice(0, 28)}</span>
          </div>
          <LiveKitRoom
            serverUrl={room.serverUrl}
            token={room.token}
            connect={true}
            audio={false}
            video={false}
            onDisconnected={() => setRoom(null)}
            onError={() => setError("The LiveKit connection failed.")}
          >
            <RoomConversation
              speech={{ models: ttsModels, catalogError: ttsCatalogError, model: ttsModel, voice: ttsVoice, onChange: changeSpeech, onEditStart: () => setSpeechEditRevision((revision) => revision + 1), editRevision: speechEditRevision, vadSensitivity, onVadSensitivityChange: setVadSensitivity, turnPacing, onTurnPacingChange: setTurnPacing, bargeIn, onBargeInChange: setBargeIn, resumeFalseInterruption, onResumeFalseInterruptionChange: setResumeFalseInterruption }}
              microphoneMode={microphoneMode}
              onMicrophoneModeChange={changeMicrophoneMode}
              ending={ending}
              onEnd={() => void endConversation()}
              resumed={room.resumed}
            />
            <AudioWaveform />
          </LiveKitRoom>
          {error ? <p className="error" role="alert">{error}</p> : null}
        </>
      )}
      <PresentationPreview />
    </main>
  );
}
