export type MicrophoneModePreference = "continuous" | "press-to-talk";
export declare const MICROPHONE_MODE_KEY: string;
export declare function parseMicrophoneMode(value: string | null): MicrophoneModePreference;
