"""The words shown while a step runs: what flyby is doing, why it matters, what comes next.

They live in one table so they can be read, and tested, as one piece of writing. The live
block sets them in a column 44 characters wide: that is where the limits come from, and
tests/test_ui.py holds every caption to them.

`then` is what follows when the step does its job, on every path through the code. Where
that depends on the run, the caller passes its own `then=` to `ui.step`.
"""
from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class Caption:
    now: str     # may hold fields like {day}, filled by the caller
    why: str
    then: str


STEPS: dict[str, Caption] = {
    # the video
    "file": Caption(
        "Reading the file's audio and tags",
        "The file may hold the day and the place it was filmed.",
        "Look for clues in what it says about itself"),
    "site": Caption(
        "Asking the site about this video",
        "The site knows the title, the length and when it went up. Each one is a clue.",
        "Look for clues in what it says about itself"),
    "towns": Caption(
        "Looking up the town the video names",
        "A title can be read more than one way. The map says which town is real.",
        "Settle the day and the place to search"),
    "download": Caption(
        "Downloading the audio",
        "The search runs on the sound. A long video can take a while.",
        "Find the aircraft in the sound"),
    "cut": Caption(
        "Cutting that part out of the audio",
        "A short part is much faster to search than the whole video.",
        "Find the aircraft in the sound"),

    # the pictures
    "frames": Caption(
        "Taking still pictures from the video",
        "A picture can show a sign, a clock or the sun. Each one is a clue.",
        "Look in the pictures for clues"),
    "light": Caption(
        "Reading how light the video is, start to end",
        "The picture goes dark at sunset. That moment depends on where you are.",
        "Look for a sunset or sunrise in the light"),
    "read": Caption(
        "Reading the writing in the pictures",
        "Captions, clocks and signs can name the place or the time.",
        "Turn what it found into clues"),
    "sun": Caption(
        "Working out where the sun set or rose",
        "At one moment the sun sets along one line on the map. The camera is near it.",
        "Add that line to the clues"),
    "guess": Caption(
        "Asking an AI to look at the pictures",
        "An AI can know a skyline or a road sign. Its guess is only a lead.",
        "Add its guess to the clues"),
    "pictures_page": Caption(
        "Building the picture search page",
        "One page of links lets you search the web for each picture.",
        "Show you where the page is"),

    # the sound
    "spectrogram": Caption(
        "Turning the sound into a chart of its tones",
        "An engine hum shows up there as a line. An aircraft going past bends it.",
        "Listen for aircraft"),
    "listen": Caption(
        "Listening for aircraft",
        "Each aircraft leaves a falling tone. flyby needs two.",
        "Measure each pass"),
    "passes": Caption(
        "Measuring each pass",
        "The shape of the fall gives each aircraft's speed and distance.",
        "See if two passes are clear enough to use"),

    # the search
    "tracks": Caption(
        "Streaming the flight tracks for {day}",
        "It is about 4 GB over the network. flyby keeps what it needs for next time.",
        "Search that day's flights, if it has any"),
    "cut_day": Caption(
        "Cutting {day} down to the search area",
        "A whole day of USA flights fills 7 GB of memory. The search needs only a part.",
        "Search those flights, if there are any"),
    "search": Caption(
        "Searching {area}: {what}",
        "Each pair of flights is tried. Only the true spot hears both at these times.",
        "See if one match stands out from the rest"),
    "pin": Caption(
        "Pinning the spot down",
        "The search gives a rough spot. This tries every 50 m around it.",
        "Check the other aircraft that flew past"),
    "others": Caption(
        "Checking the other aircraft that flew past",
        "They were not used in the search. If one shows in the sound, that backs the spot.",
        "Put the result together"),

    # the answer
    "daylight": Caption(
        "Checking the light against the sun",
        "Day and night at the answer must fit what the picture shows.",
        "Add what it found to the result"),
    "practice": Caption(
        "Practice: checking the true spot",
        "You gave the real place. flyby checks what the flight data holds there.",
        "Look up the name of the place"),
    "place": Caption(
        "Looking up the place name",
        "A town name is easier to check than two numbers.",
        "Show the result"),
}

# ingest reports its steps as sentences through `log=`; this is which caption each one is
SAID: dict[str, str] = {
    "Reading the file's audio and tags": "file",
    "Asking the site about this video": "site",
    "Downloading the audio (this can take a while for a long video)": "download",
    "Cutting that part out of the audio": "cut",
}
