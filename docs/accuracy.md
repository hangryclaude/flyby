# How sure is an answer

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

## What you tell it is a second test

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

## The other aircraft

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

## The air

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

---

[Back to the README](../README.md)
