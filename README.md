# flyby

Send a video, get where it was filmed.

```
flyby https://youtu.be/4CxVxuMVkMc --date 2026-09-08
```

![The result page: a map pin on Oyster Bay, New York, 32 m from the true spot](docs/result-page.png)

Propeller planes leave a faint falling tone in almost any outdoor recording. The
shape of that tone depends on where the microphone was. flyby finds those tones,
matches them against the public record of where every tracked aircraft was that
day, and reports the one spot on the ground that would have heard them all.

It is a one-command wrapper around [dopplertrack](https://github.com/mjshiggins/dopplertrack),
which does the physics. flyby adds what that engine leaves to you: getting the
audio, working out the day and the area, picking and measuring the flyovers,
fetching the flight data, and saying honestly whether the answer can be trusted.

## Use

```
flyby                         # asks for the link, then asks what you know
flyby <link or file> [options]
```

Nothing is required but the link. In a terminal flyby first reads what the video
says about itself (title, description, place tag), then asks a few short
questions. Enter takes the answer shown, or means "I do not know".

| It asks | What your answer buys |
|---|---|
| Where was this filmed? | A state or town is searched instead of the whole USA: about 20 times faster, and 2 GB of memory instead of 15. A town is also a second test (below). |
| Which day? | No guessing of days. Each day guessed is another search. |
| What time did it start, and how sure are you? | A second test: a wrong answer agrees with your clock only by luck. |
| Which part? (videos over 3 hours) | Only that part is searched: two hours of sound instead of twelve. |
| Did the camera stay still? Is the sound real? | A "no" is carried into the result as a warning. |
| How long may flyby work? | 1, 6 or 12 searches. |

The options below answer a question ahead of time, and a question that is
already answered is not asked. (The camera and sound questions have no option:
they only add a warning.) `--no-ask` skips them all. Then flyby takes what the
video states outright ("LIVE from Huron, Ohio", a place tag, a dated title) and
leaves weak guesses alone. Two places said equally surely ("Tampa, FL to
Boston, MA") count as a weak guess.

The day and the place are checked before anything is downloaded, so a typing
slip costs seconds. `--date` and `--start` must agree, and `--start` needs a
time of day.

A town read off a title is checked on the map before it is used. "LIVE Bowling
Green, KY" could be read three ways, and the wrong one is real: Green, Kentucky
is a hamlet 300 km from Bowling Green. flyby asks the map for each reading,
longest first, and takes the first that is a town. If none is, it keeps the
state.

| Option | What it does |
|---|---|
| `--area "Florida"` | search one state or town; `"lat,lon"` also works |
| `--bbox LATMIN LATMAX LONMIN LONMAX` | search an exact box |
| `--date 2026-09-08` | the local day it was recorded (or a range `A..B`) |
| `--start 2026-09-08T20:17` | when the video began, in UTC |
| `--start-tol 10m` | how far off `--start` may be (default 3 minutes) |
| `--section 2:00-4:00` | use only this part of a long video |
| `--effort quick` | `quick` 1 search, `normal` 6, `thorough` 12 |
| `--searches 6` | most searches to run, instead of `--effort` |
| `--days-back 2` | for uploads: how many days before the upload day to try |
| `--workers 12` | cores for the search (default: two thirds of them) |
| `--known 41.39,-82.55` | practice: where it really was filmed (see Practice) |
| `--no-ask`, `-y` | ask nothing |
| `--no-open` | do not open the result page |

With no answers at all, the day and area come from the source:

| Source | Day | Area |
|---|---|---|
| replay of a finished live stream | exact, from the stream's own start time | whole USA |
| normal upload | the upload day, then back `--days-back` days | whole USA |
| local file | the file's recording time; its GPS tag if it has one | whole USA, or 150 km round the GPS tag |
| old footage uploaded later | cannot be known: give `--date` | whole USA |

## Install

```
git clone https://github.com/hangryclaude/flyby
cd flyby
uv tool install --editable .
```

It needs `yt-dlp`, `ffmpeg` and `uv`. No API key and no account. It was built
and tested on a Mac. The search itself should run anywhere Python does, but
reading text in the picture uses Apple's text reader, so that one tool is
Mac-only.

### What you see while it works

A title, then one finished line per step. The step in hand gets three short lines:

```
  ██▀▀▀▀▀  ██       ██    ██  ██▀▀▀█▄  ██    ██
  ██▄▄▄    ██        ▀█▄▄█▀   ██▄▄▄█▀   ▀█▄▄█▀
  ██       ██          ██     ██   ██     ██
  ▀▀       ▀▀▀▀▀▀▀     ▀▀     ▀▀▀▀▀▀      ▀▀
  find where a video was filmed
  ──────────────────────────────────────────────
  ▍Pictures
  ✓ 6 pictures taken from the video
  NOW   Reading the writing in the pictures
        ⠧  ━━━━━━━━━━━━╺━━━━━━━━━━━   50%  0:03
  WHY   Captions, clocks and signs can name the
        place or the time.
  NEXT  Build the picture search page
```

### The picture tools

flyby also looks at the picture, before it asks anything. `--no-pictures` skips all of it.

| Tool | What it does |
|---|---|
| Still frames | Takes 6 frames spread through the video, straight from the site. The video is not downloaded. |
| Read the picture | Reads captions, clocks and signs with Apple's text reader. A caption like "Huron OHIO" becomes a place clue. A camera clock, set against the stream's own clock, gives the time zone. |
| Light | Measures how bright the video is from start to end. A sunset or sunrise in a timed stream marks a strip of the map. |
| Picture search page | One page with every frame and a button each for Google Lens, Yandex, Bing and TinEye. Nothing is sent until you press a button. |
| AI guess (`--ai-guess`, or say yes when asked) | Three frames go to Claude through the `claude` command, on your own plan. The AI has no tools and cannot read files. Its guess is only ever a weak clue. |
| Check-it links | After an answer: Street View, satellite, the sun's position, Mapillary and more, each opened at the spot. A flight replay link is added for flights in the last 40 days. |
| Sun and clock check | After an answer: does the light in the picture fit the sun at that spot, and does the spot lie in the camera clock's time zone? |

A clue read off a picture is treated with care. A state name alone ("ALABAMA POWER") is
only a guess. A town with its state is checked on the map first. A black picture over the
sound is left out. flyby finds places, not people: there is no face search.

## What you get

The run on the reference clip, an hour of audio from a yard
(`--date 2026-09-08 --area "New York"`):

```
LOCATED  Recorded near Oyster Bay, New York.
Location: 40.87634, -73.52521
p = 0.0044; a wrong answer would look this good about 1 time in 228
Recording started: 2026-09-08 20:17:30 UTC

Aircraft   Type                            Heard (UTC)   Distance km
N22NY      PILATUS PC-12                   20:53:26      6.1
N93CQ      BEECHCRAFT Super King Air 350   21:08:49      3.6
N501EX     CESSNA 208 Caravan              20:30:32      8.4
N7681Y     PIPER PA-30 Twin Comanche       20:40:58      8.2
```

That is 26 m from the true spot, with no window picked by hand. Given the day
alone, it searched the whole continental USA and landed 32 m away. A page with
the map, the numbers and a picture of each predicted tone lying over the real
one opens in the browser (`data.noindex/pages/flyby-<video>.html`; set
`FLYBY_PAGES` to put pages somewhere else).

![The tone a King Air 350 should have made at that spot, drawn in blue over the recording](docs/flyover-match.png)

## The four answers

| Verdict | Meaning | Shows a place? |
|---|---|---|
| **located** | a wrong answer would look this good less than 1 time in 100 | yes |
| **not sure** | less than 1 time in 10: a lead to check against the picture | yes, marked as a guess |
| **no match** | nothing stands out | no |
| **no flyovers** | fewer than two aircraft could be measured | no |

The engine always has a best-scoring spot, even for the wrong day or the wrong
country, so the number matters more than the map. Three things keep it honest:

- **A stricter bar.** The engine calls p < 0.1 "located", and its author saw 2
  of 20 searches with no right answer pass that. flyby asks for 0.01.
- **An honest p.** The engine's p treats the width of the wrong-answer crowd as
  known, though it is measured from a handful of scores (15 when further
  flyovers are used). Worked through properly, its 0.1 is then really 0.16 and
  its 0.001 is really 0.0075. flyby reports the corrected figure (`_lead_p` in
  `engine.py`; a test simulates 1,500 searches of pure noise and checks that
  10% of them, not 16%, fall under 0.1). With only two flyovers the crowd is
  measured from 100 scores and the correction is small (0.1 becomes 0.11).
- **Every search counts.** Each extra day or pair of flyovers searched is one
  more chance for a wrong answer to look good. The list of searches is fixed
  before the first one runs, best bet first, and the 1-in-100 allowance is
  shared out over that list: the first search gets the largest share (7/12 of
  it in a list of six) and each later one gets less. So a wrong "located" stays
  under 1 run in 100 however many searches run (`_share` in `engine.py`; a test
  plays 400,000 runs of wrong searches). Multiplying p by the number of
  searches run, as flyby did at first, let through 2.4 in 100.

### What you tell it is a second test

Every candidate answer implies a start time and a place. A wrong answer agrees
with what you said only by luck, and flyby counts how often that luck happens
among the wrong answers of the same search.

| You gave | The test |
|---|---|
| a start time (a stream replay, a phone video, `--start`) | does the answer's start time fall within your tolerance? |
| a town (`--area "Huron, Ohio"`, a GPS tag, `"lat,lon"`) | does the answer fall in or near it? The search itself still covers a box at least 3 degrees wide, so there are enough wrong answers to judge against. |
| both | each, and both together |

flyby takes the best of these looks and multiplies p by the number of looks
taken, so asking twice is paid for. On the reference clip the two main flyovers
alone give p = 0.29, which is nothing; add the true start time and it is 0.005.
A looser clock is weaker evidence: "within the hour" lets in about 30 times as
many wrong answers as "within two minutes".

### The other aircraft

When a spot is named, flyby predicts every other tracked aircraft that passed
within 12 km of it during the recording, and looks for each one's tone at the
second the tracks say. The search never used those aircraft, so a tone that is
there is fresh evidence. On the reference clip it finds a Robinson R44
helicopter, 4.1 km away, that the search knew nothing about.

| Measured on the reference clip | Result |
|---|---|
| other aircraft at the true spot | 5, of which 1 is seen |
| other aircraft at 39 wrong spots | 563, of which 4 are seen (0.7%) |

This check is shown in the result and does not change the verdict. The
wrong-time copies it compares against overlap too much to give an honest p to
multiply in, so it is left as a picture and a count you can weigh yourself.

### The air

The pitch a moving aircraft is heard at depends on the speed of sound around
the aircraft, and that follows the temperature at its height, not on the
ground. flyby looks the temperature up (Open-Meteo, free, no key).

- The search uses one temperature for the whole search area: the air 1,000 m
  above the ground at its middle. That only means something for an area up to
  14 degrees across (a state). A whole-USA search runs at the engine's 15 C.
- The last step, pinning the spot to 50 m, always uses the air at the answer,
  at the height each aircraft flew.

| Air temperature used on the reference clip | Distance from the true spot |
|---|---|
| 4 C, what flyby works out (below) | 26 m |
| 15 C, the engine's fixed value | 29 to 32 m |
| 18 to 20 C | 19 m |
| 26 C, what it was on the ground | 130 m |

The two matched aircraft passed 1,100 m and 5,500 m above the yard, in air at
16 C and about -8 C. The engine takes one speed of sound for both, so flyby
gives it the middle: 4 C. The first three rows are the same answer, because the
spot is pinned to a 50 m grid. What the lookup buys is not landing on the last
row: the ground temperature is the natural guess, and it is the wrong one.

## What to expect

- It needs propeller aircraft that broadcast their position, passing within a
  few kilometres. Jets do not work. Quiet rural recordings work best.
- An hour of audio is a realistic minimum. One builder who measured this on
  random outdoor webcams got a confident fix on roughly 4% of 30-minute clips
  and 9% of one-hour clips. A short phone clip will almost always say "no
  flyovers".
- Time, measured on this Mac (18 cores, shared with other work):

| Step | Time |
|---|---|
| audio, tones, measuring (1 h clip) | about 2 min |
| flight tracks for one day, first time only | 15 to 30 min (a 4 GB stream) |
| one whole-USA search | 10 to 20 min |
| the whole run when the state or town is given | 3 to 5 min (9 min for New York state while the Mac was busy) |
| the same run again | 20 s: every finished search is kept |
| downloading the sound of a 12 h stream replay | 2 min (485 MB) |

- Memory is what a search costs, so name the place when you can:

| Search | Held in memory | Index on disk, per day |
|---|---|---|
| a state or town (New York, the busiest sky) | about 2 GB | 0.9 GB |
| the whole USA | about 15 GB, for the whole search | 6 GB |
| building a whole-USA day index | about 14 GB, for a minute or two | |

- A named area gets its own small index. If a whole-USA index for that day is
  already on disk, the area is cut out of it in seconds (and gives the same
  flights: checked at 60 spots); if not, only that area is kept from the stream.
- One search at a time. A second flyby waits for the first: two whole-USA
  searches at once pushed a 48 GB Mac 15 GB past full, and both ran at a
  quarter of their speed.
- flyby will not start a whole-USA index with under 8 GB free, and says so
  when a search is larger than what is free.
- The search runs at low priority on two thirds of the cores, so the Mac stays
  usable. Ctrl-C stops it and every worker it started. Run the same command
  again and every finished step is kept (audio, tones, day indexes, finished
  searches); a download or search that was cut short starts over.
- Exit codes: 0 located, 3 searched but not located, 1 could not run, 130
  stopped.

## Practice

`--known LAT,LON` is for a recording whose true spot you already know. The
search still runs blind. Afterwards flyby measures its answer against the true
spot, counts the aircraft the flight data puts within 12 km of it while the
recording ran, and looks for each one's tone in the sound. So a "no match"
comes with its reason.

| Recording | Verdict | Aircraft passes in the data | Seen in the sound | Measured flyovers that were one of them |
|---|---|---|---|---|
| reference clip: a yard on Long Island, 1 h | located, 26 m off | 9 | 4 (luck: 0.1) | 4 of 4 (luck: 1.6) |
| rail camera, Huron, Ohio: 2 h of a 12 h replay | no match | 3 | 0 | 0 of 3 (luck: 0.2) |
| harbour camera, Port Canaveral, Florida: 1 h | no match | 43, only 2 within 3 km | 1 (luck: 0.4) | 2 of 2 (luck: 1.5) |
| bird feeder camera, Oklahoma: 1 h | no match | true spot not known | | |

"Luck" is what a wrong spot or a random moment would score. At Huron the three
sounds flyby measured were not aircraft in the data at all (it is a rail yard).
At Port Canaveral the sky was full, but far off, and nothing could be made out
over the harbour. Both "no match" answers were the right answer. The true spots
given for those two are the town and the harbour, good to a kilometre or two;
for the count of tones seen, a spot good to 100 m is needed.

The recordings this works on are quiet ones: a yard, a field, a rooftop at
night. One hour is the least worth trying.

## How it works

```
1 probe     yt-dlp / ffprobe: length, upload or stream time, title, place tag, GPS tag
2 ask       read the video's own words for a place and a day, check a town's name on
            the map, then ask the person
3 clues     which UTC days to search, which box, and what to test the answer against
4 fetch     the audio. A link's sound comes down whole (asked for a part, the site
            sends it at playing speed) and the part wanted is cut out here
5 measure   the engine finds candidate tones; flyby refits each in a tight window
            and sets aside hums, trucks and unstable fits
6 tracks    that day's adsb.lol archive as an index of propeller flights: the whole
            USA, or only the named area
7 search    the engine ranks every spot + flight pair, at the real air temperature;
            further flyovers confirm
8 judge     how likely a wrong answer would look this good, and does it agree with
            the clock and the town given; stop at the first strong match
9 report    50 m refinement, the matched aircraft, the other aircraft that passed,
            terminal summary, map page
```

| File | Job |
|---|---|
| `src/flyby/cli.py` | the command |
| `src/flyby/ingest.py` | link or file to facts, then to audio |
| `src/flyby/hints.py` | a place and a day from the title, description and tags |
| `src/flyby/ask.py` | the questions |
| `src/flyby/clues.py` | days, search box, and what the answer is tested against |
| `src/flyby/measure.py` | finding and measuring flyovers |
| `src/flyby/dayindex.py` | streaming a day of flight tracks, cutting an area out of it |
| `src/flyby/weather.py` | air temperature at flying height |
| `src/flyby/engine.py` | search plan, judging, pinning the spot down |
| `src/flyby/verify.py` | looking for the aircraft the search did not use |
| `src/flyby/practice.py` | `--known`: how the run did against the true spot, and why |
| `src/flyby/report.py` | terminal summary and the page |
| `src/flyby/runner.py`, `enginecli.py` | the engine as its own process, one at a time, at the real air temperature |
| `src/flyby/pictures.py` | runs the picture tools in order and joins what they find |
| `src/flyby/frames.py` | still frames, the site's own pictures, the light through the video |
| `src/flyby/readtext.py`, `areacodes.py` | reading writing in the frames: places, clocks, time zones |
| `src/flyby/sun.py` | the sun's height, dusk and dawn, the light check |
| `src/flyby/lookup.py` | the picture search page |
| `src/flyby/checks.py` | the check-it links |
| `src/flyby/guess.py` | the AI guess |
| `src/flyby/ui.py`, `captions.py`, `paths.py`, `model.py` | progress display and its words, folders, shared shapes |

Everything flyby keeps is in `data.noindex/` inside this folder (set `FLYBY_HOME`
to move it): `days/` holds the day indexes, `runs/<video>/` holds one run's
audio, measurements, search tables, logs and figures. Delete a day index or a
run folder whenever you want the space back; flyby rebuilds what it needs.

```
uv run pytest          # 757 tests, offline, about 10 s
```

### What leaves this Mac

| Service | What it is sent |
|---|---|
| the video site (through `yt-dlp`) | the link |
| GitHub (adsb.lol's archive) | which day |
| OpenStreetMap Nominatim | the place name you typed, or the town a video's title names; the answer's coordinates, for its name |
| Open-Meteo | the search area's centre, the answer and a `--known` spot, each rounded to 0.1 degree (about 10 km), and the hour; the answer again, rounded the same way, for its time zone |
| the video site, again | the link, for 6 still frames, its own cover pictures and its preview strips |
| Google Lens, Yandex, Bing, TinEye | a frame, only when you press that button on the picture search page |
| Claude (through the `claude` command) | 3 small frames, only with `--ai-guess` or a yes to the question |
| cdnjs and OpenStreetMap tiles | the map on the result page loads when you open it, centred on the answer |
| AWS terrain tiles | which map tiles the search area covers |

The audio never leaves the Mac. Pictures leave it only as listed above.

## Limits

- The lower 48 states by default. Anywhere else works with `--area` or `--bbox`
  if adsb.lol has receivers there.
- Aircraft that do not broadcast (many small or old planes, most military) are
  invisible to it. Then the honest answer is "no match".
- A stream that is still live is refused: the flight archive for a day appears a
  few hours after the day ends.
- A recording that runs across midnight UTC (late afternoon in the USA) is
  searched one day at a time, so a pair of flyovers on either side of midnight
  is missed.
- Slow passes fitted at the engine's lowest speed (35 m/s) are set aside with
  the hums.
- The junk filter and the 110 s fit window were tuned on one reference clip.
  So were the figures for the other-aircraft check and the air temperature.
- Nothing recorded before 2023 can be matched: the free flight archive starts
  then.
- Clues read off the picture (captions, signs, a camera clock) are weak. They
  narrow the search only when the map confirms the town.
- Three public cameras were tried for practice and none was located (see
  Practice). Outside the reference clip, flyby has not yet located a recording.

## Credits

- Method: colsto, ["I discovered a sound that leaks your exact location"](https://www.youtube.com/watch?v=UytYrhYLOKI).
- Engine: [dopplertrack](https://github.com/mjshiggins/dopplertrack) by mjshiggins, MIT, pinned to commit `2d899bb`.
- Flight tracks: [adsb.lol](https://adsb.lol), ODbL. Aircraft types: ICAO Doc 8643 via OpenSky.
  Elevation: Mapzen Terrarium tiles on AWS. Map and place names: OpenStreetMap.
  Weather: [Open-Meteo](https://open-meteo.com), CC BY 4.0.

Use it on your own recordings and on public footage such as webcams. It is not
for working out where a private person lives.

## Licence

MIT. See [LICENSE](LICENSE).
