export interface PressToTalkTrack {
  stop(): void;
}

export declare class PressToTalkController<Track extends PressToTalkTrack> {
  constructor(
    createTrack: () => Promise<Track>,
    publishTrack: (track: Track) => Promise<unknown>,
    unpublishTrack: (track: Track) => Promise<unknown>,
  );
  press(): Promise<void>;
  release(): Promise<void>;
}

export declare function pttCaptureOptions(room: {
  getActiveDevice(kind: "audioinput"): string | undefined;
}): { deviceId: string } | undefined;
