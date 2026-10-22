import { useEffect, useRef, useState } from "react";
import Board, { parsePlacement } from "./Board.jsx";
import { useBackend } from "./useBackend.js";

const START_FEN = "rnbqkbnr/pppppppp/8/8/8/8/PPPPPPPP/RNBQKBNR w KQkq - 0 1";
const DEV = new URLSearchParams(location.search).has("dev");

// game_state -> banner tone and fallback title. Canonical values: INTERFACES.md §6.
const STATES = {
  idle: { tone: "neutral", title: "Ready" },
  waiting_human: { tone: "go", title: "Your turn" },
  scanning: { tone: "busy", title: "Reading board" },
  thinking: { tone: "busy", title: "Thinking" },
  executing: { tone: "warn", title: "Hands off the board" },
  pause_for_promotion: { tone: "go", title: "Promotion" },
  error: { tone: "bad", title: "Problem" },
  recovery: { tone: "bad", title: "Restore the board" },
};

// Squares whose contents differ between two FENs — highlights the last move.
function diffSquares(prevFen, fen) {
  const a = parsePlacement(prevFen);
  const b = parsePlacement(fen);
  return new Set(Object.keys({ ...a, ...b }).filter((sq) => a[sq] !== b[sq]));
}

export default function App() {
  const { update, connected, send } = useBackend();
  const fen = update?.board_fen ?? START_FEN;
  const state = update?.game_state ?? "idle";
  const info = STATES[state] ?? STATES.idle;

  const [changed, setChanged] = useState(new Set());
  const lastPlacement = useRef(fen);
  useEffect(() => {
    if (fen.split(" ")[0] !== lastPlacement.current.split(" ")[0]) {
      setChanged(diffSquares(lastPlacement.current, fen));
      lastPlacement.current = fen;
    }
  }, [fen]);

  return (
    <div className="app">
      <header>
        <h1>CXRA</h1>
        <span className={`conn ${connected ? "on" : "off"}`}>
          {connected ? "Connected" : "Connecting to robot…"}
        </span>
      </header>

      <main>
        <section className="board-wrap">
          <Board fen={fen} flipped={update?.human_color === "black"} changed={changed} />
        </section>

        <section className="panel">
          <div className={`banner ${info.tone}`} role="status" aria-live="polite">
            <div className="banner-title">
              {info.tone === "busy" && <span className="spinner" aria-hidden />}
              {info.title}
            </div>
            {update?.message && update.message !== info.title && (
              <div className="banner-msg">{update.message}</div>
            )}
          </div>

          <Controls state={state} update={update} send={send} disabled={!connected} />

          {state !== "idle" && (
            <Difficulty value={update?.difficulty ?? 5} send={send} disabled={!connected} />
          )}
          {DEV && <DevPanel send={send} disabled={!connected || state !== "waiting_human"} />}
        </section>
      </main>
    </div>
  );
}

function Controls({ state, update, send, disabled }) {
  if (state === "idle") return <NewGame send={send} disabled={disabled} initial={update} />;

  if (state === "waiting_human")
    return (
      <button className="primary big" disabled={disabled} onClick={() => send({ type: "attempt_move" })}>
        I've made my move
      </button>
    );

  if (state === "pause_for_promotion")
    return (
      <div className="stack">
        <p className="hint">Place your new piece on the board, then pick it:</p>
        <div className="promo">
          {[["q", "♛", "Queen"], ["r", "♜", "Rook"], ["b", "♝", "Bishop"], ["n", "♞", "Knight"]].map(
            ([piece, glyph, name]) => (
              <button key={piece} disabled={disabled} onClick={() => send({ type: "promotion_choice", piece })}>
                <span className="glyph">{glyph}</span>
                {name}
              </button>
            ),
          )}
        </div>
      </div>
    );

  if (state === "recovery")
    return (
      <div className="stack">
        <p className="hint">Make the real board match the one shown here, then confirm.</p>
        <button className="primary big" disabled={disabled} onClick={() => send({ type: "recovery_confirm" })}>
          Board restored — check again
        </button>
        <button className="ghost" disabled={disabled} onClick={() => send({ type: "new_game" })}>
          Abandon and start over
        </button>
      </div>
    );

  return null; // scanning / thinking / executing / error: nothing to press, the banner says why
}

function NewGame({ send, disabled, initial }) {
  const [color, setColor] = useState("white");
  const [level, setLevel] = useState(initial?.difficulty ?? 5);

  return (
    <div className="stack">
      <fieldset className="seg">
        <legend>You play</legend>
        {["white", "black"].map((c) => (
          <button key={c} className={color === c ? "on" : ""} aria-pressed={color === c} onClick={() => setColor(c)}>
            {c === "white" ? "♔ White" : "♚ Black"}
          </button>
        ))}
      </fieldset>

      <label className="slider">
        <span>
          Difficulty <strong>{level}</strong> <em>{levelName(level)}</em>
        </span>
        <input type="range" min="1" max="10" value={level} onChange={(e) => setLevel(Number(e.target.value))} />
      </label>

      <button
        className="primary big"
        disabled={disabled}
        onClick={() => send({ type: "new_game", human_color: color, difficulty: level })}
      >
        Start game
      </button>
      <p className="hint">Set up the starting position first — the robot checks it before play.</p>

      <button
        className="ghost"
        disabled={disabled}
        onClick={() => {
          if (confirm("Remove ALL pieces from the board first. Recalibrate now?")) send({ type: "recalibrate" });
        }}
      >
        Recalibrate camera (empty board)
      </button>
    </div>
  );
}

function Difficulty({ value, send, disabled }) {
  return (
    <label className="slider small">
      <span>
        Difficulty <strong>{value}</strong> <em>{levelName(value)}</em>
      </span>
      <input
        type="range"
        min="1"
        max="10"
        defaultValue={value}
        key={value}
        disabled={disabled}
        onChange={(e) => send({ type: "set_difficulty", level: Number(e.target.value) })}
      />
    </label>
  );
}

// Matches backend/engine.py: 1-6 are Elo-limited (~1320-2220), 7-10 unrestricted skill.
function levelName(level) {
  if (level <= 2) return "beginner";
  if (level <= 4) return "casual";
  if (level <= 6) return "club";
  return "strong";
}

function DevPanel({ send, disabled }) {
  const [uci, setUci] = useState("");
  return (
    <form
      className="dev"
      onSubmit={(e) => {
        e.preventDefault();
        send({ type: "debug_move", uci: uci.trim().toLowerCase() });
        setUci("");
      }}
    >
      <label>
        Dev: simulate my move (fake vision only)
        <input value={uci} onChange={(e) => setUci(e.target.value)} placeholder="e2e4" disabled={disabled} />
      </label>
      <button disabled={disabled || !uci.trim()}>Play</button>
    </form>
  );
}
