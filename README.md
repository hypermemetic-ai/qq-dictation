# `@hypermemetic-ai/qq-dictation`

The in-process Cordis voice plugin. This repository is the only DSH dictation
generation and binds under the normal `qq-dictation` name when present beside
`qq-core`. Core boots without it.

The plugin owns the Handy recognizer child process, one short browser capture
lease, and loopback `/qq/dictate` routes. There is no workstation application
checkout in host composition.

## Frozen-session delivery

A capture freezes one full DSH session UUID at start. A successful, non-empty
end is delivered with `qq.prompt` to that frozen session even if the operator
switches sessions before sending or while Handy is transcribing. Dictation is
never rebound to the currently visible session and is not bound to a pane ID.

The frozen session's composer draft is part of the utterance. At the send
gesture, the browser reads `#prompt` only when `#composer[data-session-id]`
still belongs to the frozen session. Otherwise it reads qq-ui's persisted
`sessionStorage["qq:composer:" + sessionId]` draft. The new
`server-delivery-v1` end protocol sends that draft and the audio together. The
server strips command-like leading slashes from recognized speech, appends the
speech to a non-empty draft using the existing whitespace-boundary rule, and
submits the merged text to the frozen session.

After the server confirms delivery, the browser clears only the frozen
session's visible composer (when it is still visible) and always removes that
session's persisted draft. Another session's composer and draft are untouched.
Cancel, empty recognition, expired or foreign ownership, and a deleted target
send nothing and do not clear a draft.

Once `/end` starts, recognition and delivery are server-owned. `pagehide` does
not cancel an in-flight end; an explicit abort while recording still cancels.
Capture leases and their frozen session binding remain resumable across a
Cordis fiber replacement until completion, cancellation, or expiry.

## End protocol compatibility

- `server-delivery-v1` is the current protocol and server-delivers the frozen
  draft plus speech.
- `composer-handoff-v1` remains recognize-only so a retained older page can
  submit through its composer without a duplicate server prompt.
- An end request with no protocol marker retains legacy direct delivery of
  recognized speech.
