# `@hypermemetic-ai/qq-dictation`

The in-process Cordis voice plugin. This repository is the only DSH dictation
generation and binds under the normal `qq-dictation` name when present beside
`qq-core`. Core boots without it.

The plugin owns the Handy recognizer child process, one short browser capture
lease, and loopback `/qq/dictate` routes. A capture freezes its full DSH session
UUID at start. End returns speech through the ordinary composer handoff; cancel,
empty recognition, expired ownership, and deleted sessions send nothing.
There is no workstation application checkout in host composition.
