# How flyby works inside

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

## What leaves this Mac

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

---

[Back to the README](../README.md)
