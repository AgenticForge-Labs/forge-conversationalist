export const STALE_AGENT_SESSION_MESSAGE = "The agent is no longer connected. End this conversation and start a new one.";

export async function endBoundSession(fetcher = fetch) {
  const response = await fetcher("/api/session", {
    method: "DELETE",
    credentials: "same-origin",
    cache: "no-store",
  });
  if (!response.ok) throw new Error("Could not end the conversation.");
}

export function describeSessionStartFailure(status, payload) {
  if (status === 401) {
    return { message: "Invalid local development access token.", requiresReset: false };
  }
  if (payload?.error === STALE_AGENT_SESSION_MESSAGE) {
    return { message: STALE_AGENT_SESSION_MESSAGE, requiresReset: true };
  }
  return {
    message: "Could not start a session. Check the LiveKit and worker configuration.",
    requiresReset: false,
  };
}

export function canUseRoomInput(requiresHandshake, handshakeComplete) {
  return !requiresHandshake || handshakeComplete;
}

export function shouldEnableContinuousMicrophone(mode, roomInputReady) {
  return mode === "continuous" && roomInputReady;
}
