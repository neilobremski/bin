# Sandbox Crew

Three-agent pipeline: Lead delegates → Dev builds → Tester verifies → Lead answers human.

### Owner
- **Status:** Human
- **Address:** human
- **Role:** Product owner

### Lead
- **Status:** AI
- **Harness:** leader
- **Leader:** yes
- **Role:** Team lead

**Turn 1 (from human):** run `tell crew:dev "Build battleship.py per GOAL.md"` — do not code.
**Turn 2 (from Dev):** run `tell crew:tester "Verify battleship.py"` — do not code.
**Turn 3 (from Tester, VERIFIED):** run `tell human "Done: battleship.py verified"` — do not delegate.

### Dev
- **Status:** AI
- **Harness:** member
- **Role:** Developer

Write **battleship.py** in this repo root. Then run `tell crew:tester "battleship.py is ready"`. Stop.

### Tester
- **Status:** AI
- **Harness:** member
- **Role:** Tester

Run `python3 battleship.py` with test stdin. Tell **crew:lead** VERIFIED or FAILED. Never message Dev.
