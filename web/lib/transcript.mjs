export function parseTranscriptResponse(payload) {
  let value;
  try {
    value = typeof payload === "string" ? JSON.parse(payload) : payload;
  } catch {
    return [];
  }
  if (!Array.isArray(value?.messages)) return [];

  const seen = new Set();
  return value.messages
    .filter((message) => (
      message && typeof message.id === "string" && message.id.length > 0
      && (message.role === "user" || message.role === "assistant")
      && typeof message.text === "string" && message.text.trim().length > 0
    ))
    .slice(-50)
    .filter((message) => {
      if (seen.has(message.id)) return false;
      seen.add(message.id);
      return true;
    })
    .map((message) => ({
      id: `history:${message.id}`,
      role: message.role,
      text: message.text.slice(0, 2000),
    }));
}

export function combineTranscript(history, live, localIdentity) {
  const rows = new Map();
  for (const message of history) rows.set(message.id, message);
  for (const stream of live) {
    const id = stream?.streamInfo?.id;
    const text = typeof stream?.text === "string" ? stream.text.trim() : "";
    if (typeof id !== "string" || !id || !text) continue;
    const identity = stream?.participantInfo?.identity;
    rows.set(`live:${id}`, {
      id: `live:${id}`,
      role: identity === localIdentity ? "user" : "assistant",
      text,
    });
  }
  return [...rows.values()];
}

export function shouldRestoreTranscript(reason, resumed) {
  return reason === "reconnected" || (reason === "connected" && resumed === true);
}
