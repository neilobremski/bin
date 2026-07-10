# Goal: terminal battleship

Build a small terminal battleship game in Python at `battleship.py` in this
repo:

- 5x5 grid holding 3 single-cell ships.
- The player enters guesses on stdin as `row col` (both 0-4), one per line.
- Print HIT or MISS for each guess; when all 3 ships are hit, print a win
  message and exit 0.
- If stdin ends before the player wins, print a lose message and exit 1.

Tester must run the game (for example by piping every grid coordinate to
stdin) and verify it exits 0 on a winning playthrough before the Lead
reports back.
