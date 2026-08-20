# `@hypermemetic-ai/qq-dictation`

One repository, one Cordis plugin, one version. Loading this plugin is how a
qq host gets voice input. Loading qq does not imply dictation; qq still boots
when this package is absent.

The plugin owns the Handy recognizer process and the controls that talk to it.
Rollback is this directory. It is not a second install-root product. Bind is
a DSH session id frozen at start.

## Surfaces

- **Phone / PWA / mobile browser:** mic icon inside `#composer`, right of the
  textarea. Tap starts and binds that session. While recording the same
  control is an X and cancels. There is no Send button; Enter submits.
  Form submit still **ends** (stop, recognize, autosubmit).
- **Desktop:** the mic is hidden. In desktop command mode, Space starts with
  visible feedback and Space again stops, recognizes, and autosubmits.
  Right-Alt remains an equivalent start/end shortcut. Delete cancels. Literal
  spaces in the composer and other editable controls are never intercepted.

One browser capture owns recording at a time through a short, opaque lease.
Other phones or desktops show a non-owning busy state and cannot end or cancel
it. Owner polling renews the lease; a crashed owner's lease expires, while an
ordinary page exit stops local tracks and best-effort cancels. Bind is frozen
at start. A deleted bound session drops the result. Empty recognition sends
nothing. Cancel sends nothing.

## Send

End calls `qq.prompt` on the bound session. The utterance is ordinary user
speech, never a slash line and never a composer draft.

## Transport

HTTP is this plugin's own prefix (`/qq/dictate`) on the loopback `webServer`.
The phone reaches it the same way it already reaches qq. The browser captures
16 kHz mono PCM and posts one RIFF WAVE on end. The host writes that file and
asks Handy to transcribe it. Missing microphone fails closed (no recording UI).
The PWA stays fail-closed: no offline queue, no cached session.
