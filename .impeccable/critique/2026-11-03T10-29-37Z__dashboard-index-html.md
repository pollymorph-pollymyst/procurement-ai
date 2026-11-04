---
target: dashboard/index.html
total_score: 20
max_score: 40
na_heuristics: 
p0_count: 1
p1_count: 2
target_identity: "file:dashboard/index.html"
target_fingerprint: "sha256:7d81289f64771ef6da3773bfb37698dd87ed2d12bd818908e6c270f35a71d684"
target_path: "dashboard/index.html"
timestamp: 2026-11-03T10-29-37Z
slug: dashboard-index-html
---
Method: dual-agent (A: design review · B: detector + browser)

| # | Heuristic | Score | Key issue |
|---|---|---|---|
| 1 | Visibility of system status | 1 | «Сегодня» over 82-day-old data; schedule/last run/failure invisible |
| 2 | Match real world | 3 | plain buyer language; bare «CSV» |
| 3 | User control | 1 | no refresh, settings, schedule, "sent" in UI |
| 4 | Consistency | 3 | «Сегодня» vs «обновлено» mean different dates |
| 5 | Error prevention | 1 | nothing stops sending stale orders; hand-editing TOML |
| 6 | Recognition vs recall | 2 | must remember .bat roles, `off` param, config.toml |
| 7 | Flexibility | 2 | no rerun, no sent state, no quantity override |
| 8 | Minimalist | 3 | 7 tabs + 7 risk segments below a clean hero |
| 9 | Error recovery | 1 | failed run = silent; scheduled run fails invisibly |
| 10 | Help | 3 | method tab good; no operational help |
| Total | | 20/40 | Acceptable (low) |

P0 stale data presented as today. P1 no control/status panel (settings, schedule, run, history). P1 no "order sent" state. P2 contrast: ink-3 on ground 3.9:1, amber chip 3.89:1, orange chip 4.2:1; tap targets 36px; text 12-13px. P2 decision overload: 7 tabs, 7 risk segments, 6 hero actions.
Detector: 13 advisory (font-size drift, 1px radius, 2 false-positive colors). Overlay: 82 low-contrast on #all (orange chips + ink-3), straggler, em-dash placeholders (false positive).
