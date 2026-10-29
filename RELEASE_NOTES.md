# Presentia — what's new

Each version's notes, newest first. These are what teachers see in
Settings → Updates and on the GitHub release page.

Write new changes under **Next release** as they are made, as short bullet
points in plain words. The Release workflow publishes them with the next
version and starts a new empty "Next release" section.

## Next release

<!-- Write what changed for teachers here, as short bullet points. -->

## 1.6.3 — 2026-10-08

- **Share the meeting's browser tab.** New **Browser Tab** choice on the Meeting Monitor and the bubble: Presentia watches the Meet (or Zoom / Teams web) tab itself, the way sharing a tab in Meet works, so monitoring keeps going while you switch to other apps or tabs — no more "completely covered and has stopped updating". Works in Chrome, Edge and Brave; Presentia opens a small page in your meeting's browser where you pick the tab.
- **Switch what's watched without stopping.** If a browser window gets covered, press **Use browser tab instead**; monitoring carries on with the same session.
- **"Not seen" instead of "camera off".** A student whose face isn't recognised is no longer called camera off. Presentia reads the meeting's video tiles: a live tile means **On camera** (counted present, even with half a face), Meet's camera-off picture means **Camera off**, and only when it can't tell does it say **Not seen** — after 30 seconds, not 5.
- **"This is…" for unknown faces.** Click an unknown face and pick the student it is; that face is added to their record so they're recognised from then on (for when someone's registration photo looks different from how they look in the meeting).
- **Better recognition of turned and half-visible faces.** Registration keeps each angle (straight, left, right) instead of only their average, faces cut off at the edge of a tile are looked for, and Presentia learns how students look in meetings from clear sightings (up to 5 pictures each, on this computer only — Settings → Accessibility to turn off).
- The bubble's Copy message now says "we can't see your face" unless the camera is known to be off.

## 1.6.2 — 2026-10-08

- **Monitoring keeps going when you change pages.** You can open Students, Reports or Register while a class is being monitored; it only stops when you press Stop or open another class.
- **Half-visible faces still count.** A student whose camera is on but whose face is turned or partly hidden (behind a laptop, cut off by the meeting's toolbar) keeps their name instead of being marked as camera off.
- **Recovers from engine problems by itself.** If the recognition engine stops unexpectedly, Presentia restarts it and says so in the activity list, instead of everything stopping until the app is reopened.
- **The bubble moves smoothly.** It fades in, its panel slides open and closed, and the live dot gently pulses while monitoring. Turn this off with Settings → Appearance → Animations.
- **Simpler bubble panel.** The "On top" button is gone (the bubble is always on top); "Open Presentia" and "Hide bubble" remain.
- **Clearer Live View.** Presentia's own windows show blurred with a "Presentia" label instead of dark boxes; they are still ignored when counting faces.

## 1.6.1 — 2026-10-08

- New floating bubble: a small capsule that shows the time, who is here and how many cameras are off. Click it for Start/Stop, the list of students not on camera, and a Copy button for a "please turn your camera on" message.
- The bubble follows your theme in Settings → Appearance and can be dragged anywhere.
- Start or pick an area from the bubble works from any page.
- Settings → Updates now always shows notes, including what's new in your current version.
