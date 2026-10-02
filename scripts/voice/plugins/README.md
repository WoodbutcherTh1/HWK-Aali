# Voice plugins

Phase 1 ships the INTERFACE ONLY (`base.py`) — no plugin yet, by design.

A plugin implements any subset of the `VoicePlugin` hooks (transcript,
reply rewrite, chunk annotation, turn-done) and registers via
`voice.core.plugins.register`. First real plugin (e.g. business verticals,
wake word, live translation) lands in a later phase with its own tests.
