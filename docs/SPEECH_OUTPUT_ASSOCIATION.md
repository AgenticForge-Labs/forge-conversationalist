# Speech feature timing and output identity

The current TTS-node observer runs when synthesized audio is generated. It
can generate an entire response before LiveKit sends it to an output track.
Sending each feature immediately causes Puppeteer's freshness window to expire
while the response is still playing. Generated PCM offsets do not establish
track playback or speaker acoustic timing.

The bounded speech pacer can buffer native-gain or generic-envelope features
and release them at 50 ms intervals after an explicitly associated output start.
The original profile can use an experimental identified-output SDK capability;
the stock SDK lacks it, and the worker then continues voice without the robot.
Generic `smooth_v2` envelope delivery is not yet connected to this paced path.

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

## Experimental integration

An unmerged local LiveKit experiment carries an opaque generated-segment ID
through resampling and room-output buffering and emits it after the first
frame is accepted. The worker validates the capability on the live output
before enabling original speech. It uses the existing room-owned feature
pacer and MCP child; it does not make another audio sink, player, robot
scheduler or SDK connection. If association fails, it does not arm that
segment. The SDK experiment requires review and is not installed by default.

Fake-device tests exercise pre-generated segments, late events, capture
failure, resampling, interruption, room close, timeouts, queue limits and
missing SDK capabilities. This is not complete physical qualification. A
generated-PCM offset or track event is not a measured acoustic or physical
motion timestamp.
