from datetime import date

import pytest

from flyby.hints import read_hints, read_places
from flyby.model import Hint

UPLOADED = date(2026, 11, 20)


def got(about, uploaded=UPLOADED):
    return [(h.kind, h.value, h.strength) for h in read_hints(about, uploaded)]


# (about, expected hints in order) -- each row is a title or description a real channel could publish
CASES = [
    # the four real ones
    ({"title": "Backyard Birding - LIVE from NE Oklahoma", "location": "OKLAHOMA"},
     [("place", "Oklahoma", "stated")]),
    ({"title": "September 8, 2026 #1 - Huron, Ohio"},
     [("place", "Huron, Ohio", "likely"), ("day", "2026-09-08", "likely")]),
    ({"title": "Watching Cruise Ships Leaving Port Canaveral LIVE! (November 12, 2026)",
      "description": "From Jetty Park in Florida. Ships leave every evening."},
     [("day", "2026-11-12", "likely"), ("place", "Florida", "weak")]),
    ({"title": "Unknown location - raw audio"}, []),

    # live camera titles are the publisher saying where the camera is
    ({"title": "Port Canaveral, FL webcam"}, [("place", "Port Canaveral, Florida", "stated")]),
    ({"title": "Huron, Ohio live cam - September 8, 2026"},
     [("place", "Huron, Ohio", "stated"), ("day", "2026-09-08", "stated")]),
    ({"title": "Sunrise over Southern California"}, [("place", "California", "likely")]),
    ({"title": "Quiet night in Upstate New York"}, [("place", "New York", "likely")]),
    ({"title": "Northeast Oklahoma storms"}, [("place", "Oklahoma", "likely")]),
    ({"title": "Float trip through the Ozarks"}, []),

    # names inside other names
    ({"title": "Morning fog in West Virginia"}, [("place", "West Virginia", "likely")]),
    ({"title": "Desert wind in New Mexico"}, [("place", "New Mexico", "likely")]),
    ({"title": "Planes over Kansas City"}, []),
    ({"title": "Indiana Jones theme on a ukulele"}, []),
    ({"title": "Reading the New York Times on the porch"}, []),
    ({"title": "Dinner at Texas Roadhouse"}, []),
    ({"title": "Why the Washington Post got it wrong"}, []),
    ({"title": "George Washington slept here"}, []),
    ({"title": "Georgia sings for her grandma"}, []),
    ({"title": "Fishing the Gulf of Mexico"}, []),
    ({"title": "Fall colors in New England"}, []),
    ({"title": "Ohio State game day crowd"}, []),
    ({"title": "Storm chasing in Georgia"}, [("place", "Georgia", "likely")]),
    ({"title": "A walk around Tbilisi, Georgia"}, []),
    ({"title": "Birds in Washington State"}, [("place", "Washington", "likely")]),
    ({"title": "Cherry blossoms, Washington DC"}, [("place", "Washington, DC", "likely")]),

    # two-letter codes only in the City, ST form with a capital code
    ({"title": "Kansas City, MO rooftop"}, [("place", "Kansas City, Missouri", "likely")]),
    ({"title": "Thanks, OK see you soon"}, []),
    ({"title": "Huron, oh what a night"}, []),
    ({"title": "BIRDS, IN THE SNOW - HURON, OH"}, [("place", "Huron, Ohio", "likely")]),
    ({"title": "LIVE FROM OKLAHOMA CITY"}, []),
    ({"title": "St. Louis, MO at dusk"}, [("place", "St. Louis, Missouri", "likely")]),
    ({"title": "Paris, Texas at noon"}, [("place", "Paris, Texas", "likely")]),
    # three places said equally surely settle nothing, so none is sure enough to be taken unasked
    ({"title": "Ohio, Michigan and Iowa skies"}, [("place", "Ohio", "weak"), ("place", "Michigan", "weak"),
                                                  ("place", "Iowa", "weak")]),

    # countries outside the USA
    ({"title": "Planes over Germany"}, [("place", "Germany", "likely")]),
    ({"title": "Spotting at Heathrow UK"}, [("place", "United Kingdom", "likely")]),
    ({"description": "Canada geese on the pond again."}, []),

    # coordinates
    ({"description": "Camera at 41.3918, -82.5577 on the lake shore."}, [("place", "41.39180,-82.55770", "likely")]),
    ({"description": "Position: 41.3918° N, 82.5577° W"}, [("place", "41.39180,-82.55770", "likely")]),
    ({"description": "Spot: 41°23'30\"N 82°33'28\"W"}, [("place", "41.39167,-82.55778", "likely")]),
    ({"description": "Map: https://www.google.com/maps/@41.39,-82.55,15z"},
     [("place", "41.39000,-82.55000", "likely")]),
    ({"description": "Map: https://maps.google.com/?q=41.3918,-82.5577"}, [("place", "41.39180,-82.55770", "likely")]),
    ({"description": "Map: https://maps.google.com/?ll=41.3918%2C-82.5577&z=12"},
     [("place", "41.39180,-82.55770", "likely")]),
    ({"description": "Final score 3.5, 2.25 after overtime."}, []),
    ({"description": "Build 1.2.3, 4.5.6 fixes audio."}, []),
    ({"description": "Clip from 12.30, 14.45 and 16.00."}, []),
    ({"description": "Not a place: 95.12345, -82.55770"}, []),
    ({"location": "41.3918, -82.5577"}, [("place", "41.39180,-82.55770", "stated")]),

    # where in the description and which field
    ({"description": "x" * 650 + " Filmed in Huron, OH."}, [("place", "Huron, Ohio", "weak")]),
    ({"description": "Filmed in Huron, OH."}, [("place", "Huron, Ohio", "likely")]),
    ({"channel": "Ohio Skies"}, [("place", "Ohio", "weak")]),
    ({"tags": "ohio, birds, georgia tech, ne oklahoma"}, [("place", "Ohio", "weak"), ("place", "Oklahoma", "weak")]),
    ({"tags": "georgia"}, [("place", "Georgia", "weak")]),
    ({"location": "Jetty Park"}, [("place", "Jetty Park", "stated")]),
    ({"location": "PORT CANAVERAL, FL"}, [("place", "Port Canaveral, Florida", "stated")]),
    ({"location": "Unknown"}, []),
    ({"location": "USA"}, []),

    # days
    ({"title": "Sept 8 2026 evening"}, [("day", "2026-09-08", "likely")]),
    ({"title": "8 Sep 2026 evening"}, [("day", "2026-09-08", "likely")]),
    ({"title": "Recorded 2026-09-08"}, [("day", "2026-09-08", "likely")]),
    ({"title": "Recorded 9/8/26"}, [("day", "2026-09-08", "likely")]),
    ({"title": "Recorded 11.12.2026"}, []),
    ({"title": "Recorded 25.10.2026"}, [("day", "2026-10-25", "likely")]),
    ({"title": "Recorded December 25, 2022"}, []),
    ({"title": "Recorded November 30, 2026"}, []),
    ({"title": "Recorded February 30, 2026"}, []),
    ({"description": "x" * 650 + " September 8, 2026"}, []),

    # starts: only a date, a time and a zone written together
    ({"description": "Filmed September 8, 2026 7:45 PM EDT."},
     [("day", "2026-09-08", "likely"), ("start", "2026-09-08T23:45:00+00:00", "likely")]),
    ({"description": "Start: 2026-09-08 23:45 UTC"},
     [("day", "2026-09-08", "likely"), ("start", "2026-09-08T23:45:00+00:00", "likely")]),
    ({"description": "Start: January 5, 2026 at 6:30 am PST"},
     [("day", "2026-01-05", "likely"), ("start", "2026-01-05T14:30:00+00:00", "likely")]),
    ({"description": "Start: 2026-07-04 9:00 HST"},
     [("day", "2026-07-04", "likely"), ("start", "2026-07-04T19:00:00+00:00", "likely")]),
    ({"description": "Filmed September 8, 2026 at 7:45 PM."}, [("day", "2026-09-08", "likely")]),

    # empty and missing
    ({}, []),
    ({"title": "", "description": "", "channel": "", "location": "", "tags": ""}, []),
    ({"tags": " , ,"}, []),
]


@pytest.mark.parametrize("about, expected", CASES, ids=[str(i) for i in range(len(CASES))])
def test_table(about, expected):
    assert got(about) == expected


def test_because_quotes_the_source():
    hints = read_hints({"title": "Backyard Birding - LIVE from NE Oklahoma", "location": "OKLAHOMA"})
    assert hints == [Hint("place", "Oklahoma", 'the title says "LIVE from NE Oklahoma"', "stated")]
    [tag] = read_hints({"tags": "birds, Ohio"})
    assert tag.because == 'a tag says "Ohio"'
    [loc] = read_hints({"location": "OKLAHOMA"})
    assert loc.because == 'the video\'s place is set to "OKLAHOMA"'


def test_duplicates_keep_the_stronger_source():
    hints = read_hints({"title": "Huron, Ohio sunset", "tags": "Huron, Ohio", "location": "Huron, OH"})
    assert [(h.value, h.strength) for h in hints] == [("Huron, Ohio", "stated")]
    assert hints[0].because.startswith("the video's place")


def test_state_dropped_beside_a_town_in_it():
    assert got({"title": "Huron, Ohio sunset", "tags": "ohio"}) == [("place", "Huron, Ohio", "likely")]
    # a stated state is kept beside a weaker town
    deep = {"location": "Ohio", "description": "x" * 650 + " Huron, OH"}
    assert got(deep) == [("place", "Ohio", "stated"), ("place", "Huron, Ohio", "weak")]


def test_strongest_first():
    about = {"title": "Planes over Texas", "tags": "nevada", "location": "Huron, OH",
             "description": "x" * 650 + " Lake Charles, LA"}
    assert got(about) == [("place", "Huron, Ohio", "stated"), ("place", "Texas", "likely"),
                          ("place", "Lake Charles, Louisiana", "weak"), ("place", "Nevada", "weak")]


def test_upload_day_limits_days():
    about = {"title": "September 8, 2026 - Huron, Ohio"}
    assert ("day", "2026-09-08", "likely") not in got(about, uploaded=date(2026, 9, 7))
    assert ("day", "2026-09-08", "likely") in got(about, uploaded=date(2026, 9, 8))
    assert ("day", "2027-03-01", "likely") in got({"title": "March 1, 2027"}, uploaded=None)


def test_same_day_written_twice_is_one_hint():
    about = {"title": "September 8, 2026", "description": "Recorded 9/8/26 and 2026-09-08."}
    assert got(about) == [("day", "2026-09-08", "likely")]


def test_a_bare_town_in_the_place_tag_takes_the_state_from_the_title():
    # "Huron" alone is in South Dakota, Ohio and Ontario; the title says which
    hints = read_hints({"title": "September 9, 2026 #1 - Huron, Ohio, USA | LIVE Train Camera", "location": "HURON"},
                       date(2026, 9, 10))
    places = [h for h in hints if h.kind == "place"]
    assert [(h.value, h.strength) for h in places] == [("Huron, Ohio", "stated")]
    assert 'place is set to "HURON"' in places[0].because and 'the title says "Huron, Ohio"' in places[0].because


@pytest.mark.parametrize("title, want", [
    # a town with a state's name: the scan used to start again inside it and find the town of York
    ("LIVE from New York, NY", [("New York, New York", "stated")]),
    ("Flyover in New York, NY", [("New York, New York", "likely")]),
    ("Plane landing in Indiana, PA", [("Indiana, Pennsylvania", "likely")]),
    # a clock's zone is not part of the town's name
    ("Backyard 2026-09-08 20:17 UTC Rochester, NY", [("Rochester, New York", "likely")]),
    # a route names two places, and neither is where the camera stood for sure
    ("Tampa, FL to Boston, MA", [("Tampa, Florida", "weak"), ("Boston, Massachusetts", "weak")]),
])
def test_towns_that_used_to_be_misread(title, want):
    assert [(h.value, h.strength) for h in read_hints({"title": title}, date(2026, 9, 10)) if h.kind == "place"] == want


def test_where_a_pilot_is_based_is_not_where_the_video_was_filmed():
    about = {"description": "My name is Dan Smith, Houston, TX based pilot. This video is a compilation."}
    assert [h for h in read_hints(about, date(2026, 9, 10)) if h.kind == "place"] == []


def places(title=None, **about):
    if title:
        about["title"] = title
    return [(h.value, h.strength) for h in read_hints(about, date(2026, 9, 10)) if h.kind == "place"]


@pytest.mark.parametrize("title, town", [
    # a rule that cut at every "-ing" word left these as Green, Hill, Heights and City
    ("Bowling Green, KY live cam", "Bowling Green, Kentucky"),
    ("Spring Hill, FL live cam", "Spring Hill, Florida"),
    ("Sterling Heights, MI live cam", "Sterling Heights, Michigan"),
    ("King City, CA live cam", "King City, California"),
    # Mt and Pt are also Mountain and Pacific time
    ("Mt. Vernon, OH live cam", "Mt. Vernon, Ohio"),
    ("LIVE FROM MT. VERNON, OH", "Mt. Vernon, Ohio"),
    ("LIVE FROM MT VERNON, OH", "Mt Vernon, Ohio"),
    ("Pt. Pleasant, NJ live cam", "Pt. Pleasant, New Jersey"),
    # a month's name can end a town's name
    ("Cape May, NJ live cam", "Cape May, New Jersey"),
    ("Del Mar, CA live cam", "Del Mar, California"),
    # and the words these rules are for are still cut
    ("Live cam leaving Port Canaveral, FL", "Port Canaveral, Florida"),
    ("Live cam 8:17 PM UTC Rochester, NY", "Rochester, New York"),
    ("Live cam 9pm PT Huron, OH", "Huron, Ohio"),
    ("Live cam Flying Over Norman, OK", "Norman, Oklahoma"),
])
def test_town_names_are_cut_in_the_right_place(title, town):
    assert places(title) == [(town, "stated")]


def test_a_month_alone_before_a_state_is_not_a_town():
    assert places("Flight on 8 May, NJ skies live cam") in ([], [("New Jersey", "stated")])


@pytest.mark.parametrize("title, readings", [
    ("LIVE Bowling Green, KY", ("LIVE Bowling Green", "Bowling Green", "Green")),
    ("Isle of Palms, SC live cam", ("Isle of Palms", "Palms")),
    ("Friday Harbor, WA live cam", ("Friday Harbor", "Harbor")),
    ("Truth or Consequences, NM live cam", ("Truth or Consequences", "Consequences")),
    ("Coeur d'Alene, ID live cam", ("Coeur d'Alene", "d'Alene")),
    ("LIVE FROM MT. VERNON, OH", ("Live From Mt. Vernon", "From Mt. Vernon", "Mt. Vernon", "Vernon")),
    ("Sunset Cam Port of Cleveland, OH", ("Cam Port of Cleveland", "Port of Cleveland", "Cleveland")),
])
def test_every_reading_of_a_town_is_kept_for_the_map_to_judge(title, readings):
    town = next(h for h in read_hints({"title": title}, date(2026, 9, 10)) if h.kind == "place")
    assert town.towns == readings


def test_a_state_or_a_position_carries_no_readings():
    hints = read_hints({"title": "Ohio skies", "description": "Camera at 41.3918, -82.5577"}, date(2026, 9, 10))
    assert hints and all(h.towns == () for h in hints)


def test_rival_towns_in_one_state_leave_the_state_sure():
    got = places("Huron, OH railcam", description="Recorded near Sandusky, OH today.")
    assert ("Ohio", "likely") in got and ("Huron, Ohio", "weak") in got and ("Sandusky, Ohio", "weak") in got
    assert got[0] == ("Ohio", "likely")                    # the sure one comes first, so it is the one taken


def test_a_position_is_no_rival_to_the_town_it_is_in():
    got = places("Huron, OH railcam", description="Camera at 41.3918, -82.5577 by the tracks.")
    assert ("Huron, Ohio", "likely") in got and len([p for p in got if p[1] == "likely"]) == 2


# ---- lines read off the picture ----

def seen(*lines, strength="weak"):
    return [(h.value, h.strength) for h in read_places([(t, f'a sign says "{t}"', strength) for t in lines])]


@pytest.mark.parametrize("line, expected", [
    ("Main Street Railfan - Huron OHIO", [("Huron, Ohio", "weak")]),
    ("Huron OH", [("Huron, Ohio", "weak")]),
    ("KANSAS CITY MO", [("Kansas City, Missouri", "weak")]),
    ("Wilkes-Barre PA", [("Wilkes-Barre, Pennsylvania", "weak")]),
    ("Railcam | Port Clinton Ohio", [("Port Clinton, Ohio", "weak")]),
    ("Huron, OH", [("Huron, Ohio", "weak")]),
    ("Welcome to Ohio", [("Ohio", "weak")]),
    ("Upstate New York", [("New York", "weak")]),
    ("West Virginia", [("West Virginia", "weak")]),
    ("Visiting New Hampshire", [("New Hampshire", "weak")]),
    ("Huron OHIO trains", [("Ohio", "weak")]),   # no town: the state must end the line
    ("Huron oh", []),
    ("EXIT 12", []),
    ("2026-09-09 10.15:13 Wed", []),
])
def test_places_on_a_line(line, expected):
    assert seen(line) == expected


def test_a_dash_breaks_the_run_of_words_before_the_state():
    hint = read_places([("Main Street Railfan - Huron OHIO", "b", "likely")])[0]
    assert hint.towns == ("Huron",)
    longer = read_places([("Main Street Railfan Huron OHIO", "b", "likely")])[0]
    assert longer.towns[-1] == "Huron" and longer.towns[0] == "Main Street Railfan Huron"


def test_the_surer_line_wins_and_carries_its_reason():
    hints = read_places([("Huron OHIO", "a sign", "weak"), ("Huron OHIO", "a caption", "likely")])
    assert [(h.value, h.strength, h.because) for h in hints] == [("Huron, Ohio", "likely", "a caption")]


def test_a_state_adds_nothing_next_to_a_town_in_it():
    assert seen("Ohio", "Huron OHIO") == [("Huron, Ohio", "weak")]
