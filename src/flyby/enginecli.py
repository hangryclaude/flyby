"""The engine's command line, run with the real air temperature.

The engine takes the speed of sound from one function that assumes 15 C, and none of its
commands has an option for it. The air at flying height is often 20 C colder than that, which
bends every predicted tone by a few percent. FLYBY_AIR_C sets the temperature for the whole
run: here, and in the worker processes the search starts (they are forked, or they import
this file again as their main module)."""
import os

from dopplertrack import doppler

if os.environ.get("FLYBY_AIR_C"):
    doppler.speed_of_sound.__defaults__ = (float(os.environ["FLYBY_AIR_C"]),)

if __name__ == "__main__":
    from dopplertrack.cli import main
    main()
