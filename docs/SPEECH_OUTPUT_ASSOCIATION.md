# Speech feature timing and output identity

The current TTS-node observer runs when synthesized audio is generated. It
can generate an entire response before LiveKit sends it to an output track.
Sending each feature immediately causes Puppeteer's freshness window to expire
while the response is still playing. Generated PCM offsets do not establish
track playback or speaker acoustic timing.

The existing bounded speech pacer can buffer native-gain or generic-envelope
features and release them at 50 ms intervals after an explicitly associated
output start. It is not connected to live output and does not make the current
`smooth_v2` or `original_v1` profile playback synchronized by itself.

## Identity gap

The pinned `livekit-agents` 1.8.11 source at
`296f28b0198dea71211f9a5e5cb8187292ab896a` generates independent TTS segments
ahead of playout. `PlaybackStartedEvent` contains a timestamp but no generated
segment identity. The SDK's own `test_stale_event_during_unaccepted_capture_stays_skipped`
shows that a start event can arrive while a subsequent segment's capture has
not been accepted. Using the most recently generated stream, the current
speech handle, or FIFO alone can therefore attach the wrong features to output.
Resampling can also replace `AudioFrame` objects, so Python object identity is
not a dependable association.

## Required integration

Obtain a trustworthy, unique generated-segment identity at output capture and
the associated start/progress/finish or interruption evidence from LiveKit's
existing audio path. Carry the identity across buffering and resampling, and
distinguish a segment dropped before capture from one that started. Use the
existing room-owned feature pacer and MCP child; do not make another audio
sink, player, robot scheduler or SDK connection. If the association cannot be
proven, do not arm that segment.

Exercise concurrent and pre-generated segments, late start events, capture
failure, resampling, silence, interruption, room close, timeouts, queue limits,
and missing SDK capabilities with fake devices. A generated-PCM offset or
track event is not a measured acoustic or physical motion timestamp.
