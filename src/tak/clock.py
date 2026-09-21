# @author Daniel McCoy Stephenson
"""Clock formatting for games that run on an hour-of-day."""

_HOURS = {
    0: "12:00 AM",
    1: "1:00 AM",
    2: "2:00 AM",
    3: "3:00 AM",
    4: "4:00 AM",
    5: "5:00 AM",
    6: "6:00 AM",
    7: "7:00 AM",
    8: "8:00 AM",
    9: "9:00 AM",
    10: "10:00 AM",
    11: "11:00 AM",
    12: "12:00 PM",
    13: "1:00 PM",
    14: "2:00 PM",
    15: "3:00 PM",
    16: "4:00 PM",
    17: "5:00 PM",
    18: "6:00 PM",
    19: "7:00 PM",
    20: "8:00 PM",
    21: "9:00 PM",
    22: "10:00 PM",
    23: "11:00 PM",
}


def formatHour(hour):
    """An hour of the day (0-23) as a 12-hour clock string, e.g. 8 -> "8:00 AM".

    Raises ValueError outside 0-23 rather than wrapping: a game whose clock
    has run past 23 has a bug in its clock, and a header that silently showed
    "12:00 AM" for hour 24 would hide it."""
    try:
        return _HOURS[hour]
    except (KeyError, TypeError):
        raise ValueError("hour must be an integer from 0 to 23, got %r" % (hour,))
