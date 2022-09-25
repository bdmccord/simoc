import datetime
import importlib


class NotLoaded(object):

    """Placeholder value for object not yet loaded from database.
    """

    def __init__(self, raw_value):
        self._db_raw_value = raw_value

    def __get__(self):
        raise ValueError("Object is not yet loaded from database.")

    def __set__(self):
        raise ValueError("Object is not yet loaded from database.")


def load_db_attributes_into_dict(attributes, target_values=None, target_details=None, load_later=[]):
    for attribute in attributes:
        attribute_name = attribute.name
        details = attribute.attribute_details
        if len(details) > 0:
            details = details[0].get_data()
        else:
            details = None
        if attribute.value_type is None:
            value = None
        else:
            try:
                value_type = eval(attribute.value_type)
            except NameError:
                p, m = attribute.value_type.rsplit('.', 1)

                mod = importlib.import_module(p)
                value_type = getattr(mod, m)
            value_str = attribute.value
            if not any([issubclass(value_type, c) for c in load_later]):
                try:
                    value = value_type(value_str)
                except ValueError as e:
                    raise ValueError("Error loading db attribute '{}' into dict.".format(attribute_name)) from e
            else:
                value = NotLoaded(value_str)

        target_values[attribute_name] = value
        target_details[attribute_name] = details

    return target_values, target_details


def extend_dict(dict_a, dict_b, in_place=False):
    return dict(dict_a, **dict_b)


def subdict_from_list(d, l):
    """Return a subset of d from a list of keys, l

    Parameters
    ----------
    d : dict
        Dictionary to take subset from
    l : list
        list of keys to make the subet

    Returns
    -------
    dict
        the subset of d defined by l
    """
    return {key:d[key] for key in l if key in d}


def timedelta_to_days(time_d):
    """Get total days from timedelta

    Parameters
    ----------
    time_d : datetime.timedelta
        the timedelta to get the days of

    Returns
    -------
    float
        total days represented by the timedelta object
    """
    return time_d / datetime.timedelta(days=1)


def timedelta_to_hours(time_d):
    """Get total days from timedelta

    Parameters
    ----------
    time_d : datetime.timedelta
        the timedelta to get the days of

    Returns
    -------
    float
        total days represented by the timedelta object
    """
    return time_d / datetime.timedelta(hours=1)


def timedelta_to_minutes(time_d):
    """Get total minutes from timedelta

    Parameters
    ----------
    time_d : datetime.timedelta
        the timedelta to get the minutes of

    Returns
    -------
    float
        total minutes represented by the timedelta object
    """
    return time_d / datetime.timedelta(minutes=1)


def timedelta_to_seconds(time_d):
    """Get total seconds from timedelta

    Parameters
    ----------
    time_d : datetime.timedelta
        the timedelta to get the seconds of

    Returns
    -------
    float
        total seconds represented by the timedelta object
    """
    return time_d.total_seconds()


def timedelta_hour_of_day(time_d):
    """Get the hour component of a timedelta object based on a
    24 hour day.

    This does not account for daylight savings time

    Parameters
    ----------
    time_d : datetime.timedelta
        the timedelta to get the hour of day from

    Returns
    -------
    float
        the hour of the day in the timedelta object
    """
    return time_d.seconds/float(datetime.timedelta(hours=1).total_seconds())


def sum_attributes(objects, attribute_name):
    """Sum all attributes in an iterable containing objects

    Parameters
    ----------
    objects : TYPE
        The objects containing attribute given
    attribute_name : TYPE
        the attribute to sum across the objects

    Returns
    -------
    type of object.attribute
        The sum of all of the attributes
    """
    return sum([getattr(x, attribute_name) for x in objects])


def avg_attributes(objects, attribute_name):
    """Take an average all attributes in an iterable containing objects

    Parameters
    ----------
    objects : TYPE
        The objects containing attribute given
    attribute_name : TYPE
        the attribute to sum across the objects

    Returns
    -------
    float
        The average of all of the attributes
    """
    return sum_attributes(objects, attribute_name)/float(len(objects))


def location_to_day_length_minutes(location):
    if location == "moon":
        return ((27 * 24 + 7) * 60) + 43
    elif location == "earth":
        return 24 * 60
    elif location == "mars":
        return (24 * 60) + 39
    else:
        raise Exception("Unknown location: {}".format(location))


# 23 Sept 2022: Relocated from simoc_server/front_end_routes so that it
# can be imported (e.g. in Jupyter) without loading the server.
# TODO: Add documentation for this function, importable as 'agent_model.utils'

def calc_air_storage(volume):
    """Return storage attributes for earth-normal atmosphere of volume"""
    # 1 m3 of air weighs ~1.25 kg (depending on temperature and humidity)
    AIR_DENSITY = 1.25  # kg/m3
    # convert from m3 to kg
    mass = volume * AIR_DENSITY
    # atmosphere component breakdown (see PRD)
    percentages = {
        "n2": 78.084,  # nitrogen
        "o2": 20.946,  # oxygen
        "co2": 0.041332,  # carbon dioxide
        "ch4": 0.000187,  # methane
        "h2": 0.000055,  # hydrogen
        "h2o": 1,  # water vapor
        # the followings are not included
        #"atmo_ar": 0.9340,  # argon
        #"atmo_ne": 0.001818,  # neon
        #"atmo_he": 0.000524,  # helium
        #"atmo_kr": 0.000114,  # krypton
    }
    # calculate the mass for each element
    return dict({label: mass*perc/100 for label, perc in percentages.items()},
                total_capacity=dict(value=mass, unit='kg'))


def calc_water_storage(volume):
    """Return storage attributes for starting water_storage of volume"""
    # the total_capacity is in kg, and it's equal to the volume'
    return dict({'potable': 0.9 * volume, 'treated': 0.1 * volume},
                total_capacity=dict(value=volume, unit='kg'))

def molar_mass(currency):
    molar_mass_ref = {
        'n2': 0,
        'o2': 0,
        'co2': 0,
        'ch4': 0,
        'h2': 0,
        'h2o': 0,
    }
    if currency not in molar_mass_ref:
        raise ValueError(f'No molar mass reference for {currency!r}')
    return molar_mass_ref[currency]

# Recursive function to extract data at path from arbitrary object
def parse_data(data, path):
    """Return what's at the end of the path"""
    if not data and data != 0:
        return None
    elif len(path) == 0:
        return 0 if data is None else data
    # Shift the first element of path, pastt on the rest of the path
    index = path[0]
    remainder = path[1:]
    if isinstance(data, list):
        # LISTS
        if index == '*':
            # All Items
            parsed = [parse_data(d, remainder) for d in data]
            return [d for d in parsed if d]
        elif isinstance(index, int):
            # Single index
            return parse_data(data[index], remainder)
        # Range i:j (string)
        start, end = [int(i) for i in index.split(':')]
        return [parse_data(d, remainder) for d in data[start:end]]
    elif isinstance(data, dict):
        # DICTS
        if index in ('*', 'SUM'):
            # All items, either a dict ('*') or a number ('SUM')
            parsed = [parse_data(d, remainder) for d in data.values()]
            output = {k: v for k, v in zip(data.keys(), parsed) if v or v == 0}
            return output if len(output) > 0 else None
        elif index in data:
            # Single Key
            return parse_data(data[index], remainder)
        elif isinstance(index, str):
            # Comma-separated list of keys. Return an object with all.
            indices = [i.strip() for i in index.split(',') if i in data]
            parsed = [parse_data(data[i], remainder) for i in indices]
            output = {k: v for k, v in zip(indices, parsed) if v or v == 0}
            return output if len(output) > 0 else None
