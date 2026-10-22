// Renders from FEN only — FEN is the display source of truth (INTERFACES.md §6).

const GLYPHS = { k: "♚", q: "♛", r: "♜", b: "♝", n: "♞", p: "♟" };
const FILES = "abcdefgh";

// FEN placement -> { "e4": "P", ... }
export function parsePlacement(fen) {
  const pieces = {};
  fen.split(" ")[0].split("/").forEach((row, i) => {
    let file = 0;
    for (const ch of row) {
      if (/\d/.test(ch)) file += Number(ch);
      else pieces[`${FILES[file++]}${8 - i}`] = ch;
    }
  });
  return pieces;
}

export default function Board({ fen, flipped, changed }) {
  const pieces = parsePlacement(fen);
  const ranks = flipped ? [1, 2, 3, 4, 5, 6, 7, 8] : [8, 7, 6, 5, 4, 3, 2, 1];
  const files = flipped ? [...FILES].reverse() : [...FILES];

  return (
    <div className="board" role="grid" aria-label="Chess board">
      {ranks.map((rank, r) =>
        files.map((file, f) => {
          const square = `${file}${rank}`;
          const piece = pieces[square];
          const dark = (FILES.indexOf(file) + rank) % 2 === 1;
          return (
            <div
              key={square}
              className={`sq ${dark ? "dark" : "light"} ${changed.has(square) ? "changed" : ""}`}
              aria-label={square}
            >
              {piece && (
                <span className={`piece ${piece === piece.toUpperCase() ? "white" : "black"}`}>
                  {GLYPHS[piece.toLowerCase()]}
                </span>
              )}
              {f === 0 && <span className="coord rank">{rank}</span>}
              {r === 7 && <span className="coord file">{file}</span>}
            </div>
          );
        }),
      )}
    </div>
  );
}
