# Using flyby

```
flyby                         # asks for the link, then asks what you know
flyby <link or file> [options]
```

Nothing is required but the link. In a terminal flyby first reads what the video
says about itself (title, description, place tag), then asks a few short
questions. Enter takes the answer shown, or means "I do not know".

| It asks | What your answer buys |
|---|---|
| Where was this filmed? | A state or town is searched instead of the whole USA: about 20 times faster, and 2 GB of memory instead of 15. A town is also a second test ([how sure is an answer](accuracy.md#what-you-tell-it-is-a-second-test)). |
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

## What you see while it works

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

## The picture tools

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

---

[Back to the README](../README.md)
