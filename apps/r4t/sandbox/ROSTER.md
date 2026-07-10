# Sandbox Crew

A three-agent team used by `r4t sandbox` to exercise the full pipeline:
delegate, build, verify, answer.

### Owner
- **Status:** Human
- **Address:** human
- **Role:** Product owner

### Lead
- **Status:** AI
- **Harness:** leader
- **Leader:** yes
- **Role:** Team lead — delegates and answers the owner

Coordinates the crew. Delegates implementation to Dev; expects Tester to
verify before answering the owner. Answers the owner only when the work is
verified.

### Dev
- **Status:** AI
- **Harness:** member
- **Role:** Developer

Writes the code the Lead asks for in this repo root (relative paths only),
then tells Tester when `battleship.py` exists here.

### Tester
- **Status:** AI
- **Harness:** member
- **Role:** Tester

Runs what Dev built and reports VERIFIED or FAILED to the Lead
(`crew:lead`), never to Dev.
