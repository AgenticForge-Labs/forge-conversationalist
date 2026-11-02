export const MICROPHONE_MODE_KEY = "forge_conversationalist_microphone_mode";

export function parseMicrophoneMode(value) {
  return value === "continuous" ? "continuous" : "press-to-talk";
}
