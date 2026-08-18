# `@hypermemetic-ai/qq-dictation`

One repository, one Cordis plugin, one version. Loading this plugin is how a
qq host gets voice input. Loading qq does not imply dictation; qq still boots
when this package is absent.

The plugin owns the Handy recognizer process and the controls that talk to it.
Rollback is this directory. It is not a second install-root product. Bind is
a DSH session id frozen at start.

## Surfaces

- **Phone / PWA / mobile browser:** mic inside `#composer`, right of the
  textarea, lower-right when the box is expanded. Tap starts and binds that
  session. While recording the same control is X and cancels. Send stays on
  the right and **ends** (stop, recognize, autosubmit).
- **Desktop:** Right-Alt starts (last composer focus in this process, else
  the resume session). Right-Alt again ends and autosubmits. Delete cancels.

One live recording at a time. Bind is frozen at start. Navigating away does
not retarget. A deleted bound session drops the result. Empty recognition
sends nothing. Cancel sends nothing.

## Send

End calls `qq.prompt` on the bound session. The utterance is ordinary user
speech, never a slash line and never a composer draft.

## Transport

HTTP is this plugin's own prefix (`/qq/dictate`) on the loopback `webServer`.
The phone reaches it the same way it already reaches qq. Audio is recognized
on the host. The PWA stays fail-closed: no offline queue, no cached session.
