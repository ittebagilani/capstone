#!/usr/bin/env python3
"""You type a move, Stockfish answers, the answer is sent to the ESP32.
Usage: python3 pi_play.py [socket://chessbot.local:3333 | /dev/ttyUSB0]   ('!text' sends raw text, 'q' quits)"""
import shutil, sys, time
import chess, chess.engine, serial

PORT = sys.argv[1] if len(sys.argv) > 1 else "socket://chessbot.local:3333"
THINK_SECONDS = 0.5  # tune: higher = stronger, slower

esp = serial.serial_for_url(PORT, baudrate=115200, timeout=10)  # pyserial handles socket:// and /dev/... alike
if PORT.startswith("/dev"):
    time.sleep(2)  # opening a USB port resets the ESP32; let it boot
esp.reset_input_buffer()

def send(text):
    esp.write(f"{text}\n".encode())
    reply = esp.readline().decode(errors="replace").strip()
    print("esp32:", reply or "(no reply, timed out)")
    return reply == f"DONE {text}"

engine = chess.engine.SimpleEngine.popen_uci(shutil.which("stockfish") or "/usr/games/stockfish")
board = chess.Board()
try:
    while not board.is_game_over():
        print(board, "\n")
        line = input("your move (e.g. e2e4): ").strip()
        if line == "q":
            break
        if line.startswith("!"):
            send(line[1:])
            continue
        try:
            board.push_uci(line)
        except ValueError:
            print("illegal move")
            continue
        if board.is_game_over():
            break
        move = engine.play(board, chess.engine.Limit(time=THINK_SECONDS)).move
        board.push(move)
        print("stockfish:", move.uci())
        if not send(move.uci()):
            print("ESP32 didn't confirm the move, check the board")
    print("game over:", board.result())
finally:
    engine.quit()
    esp.close()
