# Third-party capability attribution

The JARVIS live-briefing design was adapted from concepts in
[`SAGAR-TAMANG/friday-tony-stark-demo`](https://github.com/SAGAR-TAMANG/friday-tony-stark-demo),
reviewed at commit `4d85750ae4c60c88850a6c69107e91bf02300673` on 2026-08-23. That repository's
README identifies the project as MIT licensed.

JARVIS reimplements the useful capability boundary rather than copying the application wholesale:

- fixed-source world and finance RSS briefings;
- World Monitor and Finance World Monitor launch actions;
- current-time, system-information, JSON-formatting, and word-count utilities;
- voice-first access through the existing JARVIS HUD microphone and system speech facilities.

The FRIDAY demo's arbitrary URL fetcher, placeholder search response, automatic unapproved browser
launch, provider-specific character prompt, and cloud credential assumptions were not carried over.
All JARVIS state changes and externally visible actions remain subject to its own path, approval,
audit, and secret-handling policies.

The local hand-control interaction was independently implemented from the normalized pinch,
smoothing, and two-hand zoom concepts in
[`SAGAR-TAMANG/ultron-by-sagar-builds`](https://github.com/SAGAR-TAMANG/ultron-by-sagar-builds),
reviewed on 2026-08-24. That repository is MIT licensed. JARVIS adds confidence thresholds,
multi-frame hysteresis, target bounds, opt-in camera consent, low-power sampling, file-panel
interactions, and an emergency stop. It does not store or upload camera frames.

The locally vendored MediaPipe Tasks Vision browser runtime and Hand Landmarker model are provided
by Google under the Apache License 2.0. They are served from JARVIS itself so hand tracking does not
depend on a runtime CDN connection. Upstream project: https://github.com/google-ai-edge/mediapipe.

The v0.4 capability review also examined the public feature descriptions of
[`dmrr35/Open.Jarvis`](https://github.com/dmrr35/Open.Jarvis),
[`134ertel/jarvis`](https://github.com/134ertel/jarvis),
[`ndunl075/Jarvis`](https://github.com/ndunl075/Jarvis), and
[`isair/jarvis`](https://github.com/isair/jarvis) on 2026-08-25. JARVIS independently implements
four generally useful ideas identified in that comparison: a searchable command palette,
privacy-aware memory context and credential masking, approval-gated bounded clipboard reading, and
due-time task reminders. No source code or visual assets were copied from those projects.
