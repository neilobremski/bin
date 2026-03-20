## PR Checklist

### Code Quality
- [ ] No `__pycache__` or `.pyc` files in git
- [ ] No hardcoded secrets (API keys, passwords, bridge URLs, MQTT creds)
- [ ] No SQL string interpolation (all queries use `?` parameters)
- [ ] No raw `except:` without specific exception types
- [ ] No dead imports or unused variables
- [ ] Runtime artifacts gitignored (`.db`, `.log`, `.count`, `health.txt`, `stimulus.txt`)

### Testing
- [ ] Integration tests pass (`lifetime.sh`)
- [ ] Unit tests pass (if applicable: `pytest`)
- [ ] Manual verification of new features

### Documentation
- [ ] Textbook updated if architecture changed
- [ ] README updated if organ/CLI interface changed

### Review
- [ ] Critic agent reviewed before merge
