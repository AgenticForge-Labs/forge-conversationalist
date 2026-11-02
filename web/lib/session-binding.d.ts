export type SessionBinding = { roomName: string; identity: string };
export function encodeSessionBinding(binding: SessionBinding, configured: string): string;
export function decodeSessionBinding(value: string, configured: string): SessionBinding | undefined;
