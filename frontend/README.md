# frontend

Owner: Itteba. The HMI: React + Vite, talks to the backend over one WebSocket
(INTERFACES.md §6) and renders only from the latest `state_update`.

```bash
npm install
npm run build        # -> dist/, served by the backend at http://<pi>:8000/
npm run dev          # hot reload on :5173, proxies /ws to a backend on :8000
```

Add `?dev` to the URL for a "simulate my move" box — with `CXRA_VISION=fake` (the default)
it stands in for a human moving a piece, so a whole game runs with no camera or pieces.

Controls per `game_state`: idle → colour/difficulty/start + recalibrate; waiting_human →
"I've made my move" (`attempt_move`); pause_for_promotion → Q/R/B/N; recovery → "board
restored" (`recovery_confirm`). The other states only show the banner.
