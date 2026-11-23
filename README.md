<p align="center">
  <img src="docs/banner.png" alt="FLYBY: send a video, get where it was filmed. A plane's tone falls as it passes, and how it bends depends on where the microphone was." width="100%">
</p>

<p align="center">
  <a href="LICENSE"><img src="https://img.shields.io/badge/licence-MIT-35d6ee" alt="Licence: MIT"></a>
  <img src="https://img.shields.io/badge/python-3.12+-35d6ee" alt="Python 3.12 or newer">
  <img src="https://img.shields.io/badge/built%20on-macOS-35d6ee" alt="Built on macOS">
  <img src="https://img.shields.io/badge/API%20keys-none-35d6ee" alt="No API keys">
  <a href="https://github.com/mjshiggins/dopplertrack"><img src="https://img.shields.io/badge/engine-dopplertrack-35d6ee" alt="Engine: dopplertrack"></a>
</p>

```
flyby https://youtu.be/4CxVxuMVkMc --date 2026-09-08
```

Propeller planes leave a faint falling tone in almost any outdoor recording. The
shape of that tone depends on where the microphone was. flyby finds those tones,
matches them against the public record of where every tracked aircraft was that
day, and reports the one spot on the ground that would have heard them all.

![The result page: a map pin on Oyster Bay, New York, 32 m from the true spot](docs/result-page.png)

## The video that started it

<p align="center">
  <a href="https://www.youtube.com/watch?v=UytYrhYLOKI">
    <img src="https://img.youtube.com/vi/UytYrhYLOKI/maxresdefault.jpg" alt="Watch on YouTube: colsto, I discovered a sound that leaks your exact location" width="640">
  </a>
  <br>
  <a href="https://www.youtube.com/watch?v=UytYrhYLOKI">▶ Watch: colsto, "I discovered a sound that leaks your exact location"</a>
</p>

colsto worked out the method and posted an hour of raw audio as a challenge.
[dopplertrack](https://github.com/mjshiggins/dopplertrack) by mjshiggins turned
the method into an engine. flyby is a one-command wrapper around that engine. It
adds what the engine leaves to you: getting the audio, working out the day and
the area, picking and measuring the flyovers, fetching the flight data, and
saying honestly whether the answer can be trusted.

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

## Use

```
flyby                         # asks for the link, then asks what you know
flyby <link or file> [options]
```

Nothing is required but the link. flyby reads what the video says about itself,
looks at 6 still frames, then asks a few short questions. Enter means "I do not
know".

| The most useful options | What it does |
|---|---|
| `--area "Florida"` | search one state or town: about 20 times faster than the whole USA |
| `--date 2026-09-08` | the local day it was recorded |
| `--known 41.39,-82.55` | practice: say where it really was, and see how flyby did |
| `--no-ask` | ask nothing |

Every question, option and picture tool: **[docs/usage.md](docs/usage.md)**

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

## How it works

| Step | What happens |
|---|---|
| 1. Listen | Get the audio and find the falling tones of passing planes. |
| 2. Look up | Fetch where every tracked propeller plane was that day (adsb.lol). |
| 3. Search | Find the one spot that would have heard those planes with those tones. |
| 4. Judge | Work out how often a wrong answer would look this good. |

| Verdict | Meaning | Shows a place? |
|---|---|---|
| **located** | a wrong answer would look this good less than 1 time in 100 | yes |
| **not sure** | less than 1 time in 10: a lead to check against the picture | yes, marked as a guess |
| **no match** | nothing stands out | no |
| **no flyovers** | fewer than two aircraft could be measured | no |

The engine always has a best-scoring spot, even for the wrong day or the wrong
country, so the number matters more than the map.

- How the number is kept honest: **[docs/accuracy.md](docs/accuracy.md)**
- The nine steps, the files, and what is sent where: **[docs/internals.md](docs/internals.md)**

## Honest odds

| Recording | Result |
|---|---|
| reference clip: a yard on Long Island, 1 h | located, 26 m off |
| rail camera, Huron, Ohio | no match |
| harbour camera, Port Canaveral, Florida | no match |
| bird feeder camera, Oklahoma | no match |

- It needs propeller aircraft that broadcast their position, passing within a
  few kilometres. Jets do not work.
- It needs a quiet outdoor recording of an hour or more: a yard, a field, a
  rooftop at night.
- One builder who measured this on random outdoor webcams got a confident fix
  on roughly 4% of 30-minute clips and 9% of one-hour clips.
- Outside the reference clip, flyby has not yet located a recording.

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
- Three public cameras were tried for practice and none was located. The
  reasons are in [docs/usage.md](docs/usage.md#practice).

## Privacy

The audio never leaves your computer. Pictures leave it only when you press a
search button or say yes to the AI guess. The full list of what is sent to which
service is in [docs/internals.md](docs/internals.md#what-leaves-this-mac).

Use flyby on your own recordings and on public footage such as webcams. It is
not for working out where a private person lives.

## Credits

- Method: colsto, ["I discovered a sound that leaks your exact location"](https://www.youtube.com/watch?v=UytYrhYLOKI).
- Engine: [dopplertrack](https://github.com/mjshiggins/dopplertrack) by mjshiggins, MIT, pinned to commit `2d899bb`.
- Flight tracks: [adsb.lol](https://adsb.lol), ODbL. Aircraft types: ICAO Doc 8643 via OpenSky.
  Elevation: Mapzen Terrarium tiles on AWS. Map and place names: OpenStreetMap.
  Weather: [Open-Meteo](https://open-meteo.com), CC BY 4.0.

## Licence

MIT. See [LICENSE](LICENSE).
