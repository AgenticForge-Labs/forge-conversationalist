export class PressToTalkController {
  #generation = 0;
  #held = false;
  #track;
  #stopped = new WeakSet();

  constructor(createTrack, publishTrack, unpublishTrack) {
    this.createTrack = createTrack;
    this.publishTrack = publishTrack;
    this.unpublishTrack = unpublishTrack;
  }

  press() {
    if (this.#held) return Promise.resolve();
    this.#held = true;
    const generation = ++this.#generation;
    return this.#start(generation);
  }

  release() {
    this.#held = false;
    ++this.#generation;
    const track = this.#track;
    this.#track = undefined;
    if (!track) return Promise.resolve();
    this.#stop(track);
    return this.unpublishTrack(track).then(() => undefined, () => undefined);
  }

  async #start(generation) {
    const track = await this.createTrack();
    if (!this.#held || generation !== this.#generation) {
      this.#stop(track);
      return;
    }
    this.#track = track;
    try {
      await this.publishTrack(track);
    } catch (error) {
      if (this.#track === track) this.#track = undefined;
      this.#stop(track);
      if (!this.#held || generation !== this.#generation) return;
      throw error;
    }
    if (!this.#held || generation !== this.#generation) {
      if (this.#track === track) this.#track = undefined;
      this.#stop(track);
      try {
        await this.unpublishTrack(track);
      } catch {
        // A concurrent release may already have unpublished this track.
      }
    }
  }

  #stop(track) {
    if (this.#stopped.has(track)) return;
    this.#stopped.add(track);
    track.stop();
  }
}

export function pttCaptureOptions(room) {
  const deviceId = room.getActiveDevice("audioinput");
  return deviceId ? { deviceId } : undefined;
}
