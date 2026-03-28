"""PFC organ: the commander in chief.

Does NOTHING without stimulus. Cycle: check stimulus -> if any, think -> respond.
"""
import sys
from stimulus import consume_stimulus_files, process_stimuli, log


def run_cycle():
    """One PFC cycle. Returns count of processed stimuli."""
    stimuli = consume_stimulus_files()
    if not stimuli:
        return 0

    processed = process_stimuli(stimuli)
    log(f"cycle: {processed}/{len(stimuli)} stimuli processed")
    return processed


def main():
    try:
        run_cycle()
    except Exception as e:
        log(f"cycle error: {e}")
        sys.exit(1)


if __name__ == "__main__":
    main()
