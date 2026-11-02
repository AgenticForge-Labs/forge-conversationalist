export type TranscriptMessage = {
  id: string;
  role: "user" | "assistant";
  text: string;
};

export declare function parseTranscriptResponse(payload: string | unknown): TranscriptMessage[];
export declare function combineTranscript(
  history: TranscriptMessage[],
  live: Array<{ streamInfo?: { id?: string }; participantInfo?: { identity?: string }; text?: string }>,
  localIdentity: string,
): TranscriptMessage[];
export declare function shouldRestoreTranscript(
  reason: "connected" | "reconnected",
  resumed: boolean,
): boolean;
