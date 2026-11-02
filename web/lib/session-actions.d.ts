export declare function endBoundSession(fetcher?: typeof fetch): Promise<void>;
export declare const STALE_AGENT_SESSION_MESSAGE: string;
export declare function describeSessionStartFailure(
  status: number,
  payload: unknown,
): { message: string; requiresReset: boolean };
export declare function canUseRoomInput(requiresHandshake: boolean, handshakeComplete: boolean): boolean;
export declare function shouldEnableContinuousMicrophone(mode: string, roomInputReady: boolean): boolean;
