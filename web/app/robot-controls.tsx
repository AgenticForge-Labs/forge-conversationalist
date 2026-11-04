"use client";

import { useCallback, useEffect, useState } from "react";
import { useRoomContext } from "@livekit/components-react";
import { ConnectionState, RoomEvent, type RemoteParticipant } from "livekit-client";

type Performer = {
  status: string;
  evidence?: string;
  action_id?: string | null;
  behavior_revision?: string;
  expressions?: string[];
  presence_behaviors?: string[];
};
type Catalog = {
  schema: "forge.behavior.v1";
  state: string;
  performers: Record<string, Performer>;
};
type ActionReceipt = { outcome?: string; reason?: string; action_id?: string; completion?: string };
type Capabilities = {
  schema: "forge.capabilities.v1";
  integrations: {
    hermes?: boolean;
    company_services?: string[];
    mcp_tools?: string[];
  };
  performers: Record<string, {
    status?: string;
    expressions?: string[];
    presence_behaviors?: string[];
  }>;
};

const LABELS: Record<string, string> = {
  reachy: "Reachy Mini",
  stackchan: "StackChan",
  soarm101: "SO-ARM101",
};

function performerTitle(id: string): string {
  return LABELS[id.toLowerCase()] ?? id;
}

function textLabel(id: string): string {
  return id.replace(/_/g, " ").replace(/\b\w/g, (letter) => letter.toUpperCase());
}

function findAgent(room: ReturnType<typeof useRoomContext>): RemoteParticipant | undefined {
  return [...room.remoteParticipants.values()].find((participant) =>
    participant.identity.startsWith("agent-"),
  );
}

export function RobotControls() {
  const room = useRoomContext();
  const [catalog, setCatalog] = useState<Catalog | null>(null);
  const [capabilities, setCapabilities] = useState<Capabilities | null>(null);
  const [unavailable, setUnavailable] = useState("");
  const [running, setRunning] = useState<string | null>(null);
  const [receipt, setReceipt] = useState<Record<string, string>>({});

  const callRobot = useCallback(async (method: string, body: Record<string, string>) => {
    if (room.state !== ConnectionState.Connected) throw new Error("Room is disconnected");
    const agent = findAgent(room);
    if (!agent) throw new Error("Voice agent is not connected");
    return room.localParticipant.performRpc({
      destinationIdentity: agent.identity,
      method,
      payload: JSON.stringify(body),
      responseTimeout: method === "forge.get_robots" ? 20_000 : 40_000,
    });
  }, [room]);

  const refresh = useCallback(async () => {
    try {
      const result = JSON.parse(await callRobot("forge.get_robots", {})) as Catalog;
      if (result.schema !== "forge.behavior.v1" || !result.performers
          || typeof result.performers !== "object" || Array.isArray(result.performers)) {
        throw new Error("Unexpected robot catalog");
      }
      setCatalog(result);
      setUnavailable("");
      try {
        const caps = JSON.parse(await callRobot("forge.get_capabilities", {})) as Capabilities;
        if (caps?.schema === "forge.capabilities.v1") setCapabilities(caps);
      } catch {
        setCapabilities(null);
      }
    } catch {
      setCatalog(null);
      setUnavailable("No authorized robot ensemble is available in this room.");
    }
  }, [callRobot]);

  useEffect(() => {
    let checking = false;
    const check = () => {
      if (checking) return;
      checking = true;
      void refresh().finally(() => { checking = false; });
    };
    const joined = (participant: RemoteParticipant) => {
      if (participant.identity.startsWith("agent-")) check();
    };
    const timer = window.setInterval(check, 6_000);
    room.on(RoomEvent.Connected, check);
    room.on(RoomEvent.Reconnected, check);
    room.on(RoomEvent.ParticipantConnected, joined);
    check();
    return () => {
      window.clearInterval(timer);
      room.off(RoomEvent.Connected, check);
      room.off(RoomEvent.Reconnected, check);
      room.off(RoomEvent.ParticipantConnected, joined);
    };
  }, [refresh, room]);

  async function request(performerId: string, expression: string) {
    const id = `${performerId}:${expression}`;
    if (running !== null) return;
    setRunning(id);
    setReceipt((previous) => ({
      ...previous,
      [performerId]: `Requesting ${expression}…`,
    }));
    try {
      const response = JSON.parse(await callRobot("forge.request_robot_expression", {
        performer_id: performerId,
        expression,
      })) as ActionReceipt;
      const message = response.outcome === "accepted"
        ? `${textLabel(expression)} admitted. Physical completion is unconfirmed.`
        : `${textLabel(expression)}: ${response.outcome ?? "unknown"} (${response.reason ?? "no confirmation"}).`;
      setReceipt((previous) => ({ ...previous, [performerId]: message }));
      await refresh();
    } catch {
      // A transport error could occur AFTER an action was dispatched.
      // Never automatically resend or claim that motors stopped.
      setReceipt((previous) => ({
        ...previous,
        [performerId]: "Request outcome unconfirmed. Check robot status before another action.",
      }));
    } finally {
      setRunning(null);
    }
  }

  return (
    <section className="robot-controls" aria-label="Robot expressions">
      <div className="status">
        <div>
          <p className="eyebrow">Forge Puppeteer</p>
          <h2>Robots and expressions</h2>
        </div>
        <button type="button" className="secondary" onClick={() => void refresh()}>Refresh robots</button>
      </div>
      <p className="subtle">Only actions advertised by each operator-approved robot are shown. Idle, listening and speaking are automatic. A requested gesture is not proof of physical completion.</p>
      {capabilities && (
        <fieldset className="robot-performer">
          <legend>Tools and capabilities</legend>
          <p className="subtle">
            <strong>Hermes delegation:</strong> {capabilities.integrations.hermes ? "available" : "unavailable"}
          </p>
          <p className="subtle">
            <strong>Company services (read-only):</strong>{" "}
            {capabilities.integrations.company_services?.length
              ? capabilities.integrations.company_services.join(", ")
              : "none configured"}
          </p>
          <p className="subtle">
            <strong>MCP tools:</strong>{" "}
            {capabilities.integrations.mcp_tools?.length
              ? capabilities.integrations.mcp_tools.join(", ")
              : "none configured"}
          </p>
        </fieldset>
      )}
      {unavailable ? <p className="subtle" role="status">{unavailable}</p> : null}
      {catalog && Object.entries(catalog.performers).map(([performerId, performer]) => {
        const actions = Array.isArray(performer.expressions)
          ? performer.expressions.filter((expression) => typeof expression === "string")
          : [];
        const offline = performer.status === "blocked" || performer.status === "unavailable";
        return (
          <fieldset className="robot-performer" key={performerId}>
            <legend>{performerTitle(performerId)}</legend>
            <p className="subtle">
              <strong>Status:</strong> {performer.status}
              {performer.action_id ? ` · Last action: ${performer.action_id}` : ""}
            </p>
            <p className="subtle">Automatic: {performer.presence_behaviors?.join(", ") || "not advertised"}</p>
            <div className="robot-expression-grid">
              {actions.map((expression) => (
                <button type="button" className="secondary" key={expression}
                  disabled={offline || running !== null}
                  onClick={() => void request(performerId, expression)}>
                  {running === `${performerId}:${expression}` ? "Requesting…" : textLabel(expression)}
                </button>
              ))}
            </div>
            {actions.length === 0 && <p className="subtle">No finite expressions advertised.</p>}
            {offline && <p className="subtle">Unavailable: no action will be sent.</p>}
            {receipt[performerId] && <p role="status" className="subtle">{receipt[performerId]}</p>}
            {performer.behavior_revision && (
              <p className="subtle robot-revision" title={performer.behavior_revision}>
                Behavior catalog: {performer.behavior_revision.slice(0, 19)}…
              </p>
            )}
          </fieldset>
        );
      })}
    </section>
  );
}
