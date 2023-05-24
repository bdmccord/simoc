"""
These functions perform calculations or assignments which
are to be passed back to the front end, but none of the functions
are called directly from the front end.
These functions were originally in views.py.
The front end routes which call the functions here are in views.py
Note: the name of this script is misleading and should be changed
"""

import os
import sys
import json
import math
import copy
import gzip
import pathlib
import datetime

import numpy as np
from flask import request, Response
from werkzeug.security import safe_join

from simoc_server import app, db, redis_conn
from simoc_abm.util import load_data_file, get_default_agent_data
from simoc_abm.agents import SunAgent, ConcreteAgent

@app.route('/simdata/<path:filename>')
def serve_simdata(filename):
    """Serve static gzipped simdata files."""
    simdata_dir = pathlib.Path(__file__).resolve().parent / "dist" / "simdata"
    simdata_file = safe_join(simdata_dir, filename)  # prevent path traversal
    app.logger.info(f'Serving preset simdata file: {simdata_file}')
    try:
        with open(simdata_file, 'rb') as f:
            data = f.read()
    except FileNotFoundError:
        return "Invalid simdata file", 404
    else:
        return Response(data, mimetype='application/json',
                        headers={'Content-Encoding': 'gzip'})
    

@app.route('/get_energy', methods=['GET'])
def get_energy():
    """
    Sends front end energy values for config wizard.
    Takes in the request values 'agent_name' and 'quantity'

    Returns
    -------
    json object with energy value for agent
    """

    agent_name = request.args.get('agent_name', type=str)
    agent_quantity = request.args.get('quantity', 1, type=int) or 1
    agent_desc = load_data_file('agent_desc.json')
    data = agent_desc.get(agent_name, None)
    if agent_name == 'solar_pv_array_mars':
        value_type = 'energy_output'
        total_kwh = data['flows']['out']['kwh']['value']
    elif agent_name == 'power_storage':
        value_type = 'energy_capacity'
        total_kwh = data['capacity']['kwh']
    else:
        value_type = 'energy_input'
        def input_kwh(agent):
            return agent.get('flows', {}).get('in', {}).get('kwh', {}).get('value', 0)
        if agent_name == 'eclss':
            # Sum of all eclss agents
            total_kwh = sum(input_kwh(v) for v in agent_desc.values()
                            if v.get('agent_class') == 'eclss')
        elif data is None:
            total_kwh = 0
        elif data.get('agent_class', None) == 'plants':
            # Electricity used by lamp for plant
            par_baseline = data['properties']['par_baseline']['value']
            total_kwh = par_baseline * input_kwh(agent_desc['lamp'])
        else:
            total_kwh = input_kwh(data)
    value = total_kwh * agent_quantity
    total = {value_type : value}
    return json.dumps(total)

monthly_par = SunAgent.monthly_par
hourly_par_fraction = SunAgent.hourly_par_fraction
def b2_plant_factor(plant, data, cache={}):
    """Calculate and return an estimate of the actual:ideal exchange ratios at b2"""
    if plant not in cache:
        # par_factor
        mean_monthly_par = sum(monthly_par) / len(monthly_par)
        available_light = np.array(hourly_par_fraction) * mean_monthly_par
        par_baseline = data['properties']['par_baseline']['value']
        photoperiod = data['properties']['photoperiod']['value']
        photo_start = int((24 // 2) - (photoperiod // 2))
        photo_end = int(photo_start + photoperiod)
        photo_rate = 24 / photoperiod
        ideal_light = np.zeros(24)
        ideal_light[photo_start:photo_end] = photo_rate * par_baseline
        usable_light = np.minimum(available_light, ideal_light)
        par_factor_hourly = usable_light / ideal_light
        par_factor = np.mean(par_factor_hourly[photo_start:photo_end])
        # density_factor
        density_factor = 0.5
        cache[plant] = par_factor * density_factor
    return cache[plant]

carbonation_rate = (ConcreteAgent.rate_scale[0] * 
                    ConcreteAgent.density * 
                    ConcreteAgent.diffusion_rate)
@app.route('/get_o2_co2', methods=['GET'])
def get_o2_co2():
    """
    Sends front end o2 and co2 values for config wizard.
    Takes in the request values 'agent_name' and 'quantity'

    Returns
    -------
    json object with o2 and co2 values for agent
    """

    agent_name = request.args.get('agent_name', type=str)
    agent_quantity = request.args.get('quantity', 1, type=int) or 1
    location = request.args.get('location', 'mars', type=str)
    total = {'o2': {'input': 0, 'output': 0}, 'co2': {'input': 0, 'output': 0}}

    substitute_names = {'human_agent': 'human'}
    agent_name = substitute_names.get(agent_name, agent_name)
    data = get_default_agent_data(agent_name)
    if data is None:
        raise ValueError('Agent not found in agent_desc:', agent_name)
    agent_class = data.get('agent_class', None)
    if 'flows' not in data:
        return json.dumps(total)
    for direction, flows in data['flows'].items():  # input, output
        for currency in {'o2', 'co2'}:
            if currency not in flows:
                continue
            amount = flows[currency]['value'] * agent_quantity
            # for B2, adjust for expected model outputs
            if location == 'b2':
                if agent_class == 'plants':
                    amount *= b2_plant_factor(agent_name, data)
                elif agent_name == 'concrete':
                    amount *= carbonation_rate
            _direction = {'in': 'input', 'out': 'output'}[direction]
            total[currency][_direction] += amount
    return json.dumps(total)


def calc_air_storage(volume, weights=None):
    # 1 m3 of air weighs ~1.25 kg (depending on temperature and humidity)
    AIR_DENSITY = 1.25  # kg/m3
    # convert from m3 to kg
    mass = volume * AIR_DENSITY
    # atmosphere component breakdown (see PRD)
    weights = weights if weights is not None else {}
    percentages = {
        "n2": weights.get('n2', 78.084),  # nitrogen
        "o2": weights.get("o2", 20.946),  # oxygen
        "co2": weights.get("co2", 0.041332),  # carbon dioxide
        "ch4": weights.get("ch4", 0.000187),  # methane
        "h2": weights.get("h2", 0.000055),  # hydrogen
        "h2o": weights.get("h2o", 1),  # water vapor
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
    # the total_capacity is in kg, and it's equal to the volume'
    return dict({'potable': 0.9 * volume, 'treated': 0.1 * volume},
                total_capacity=dict(value=volume, unit='kg'))


def convert_configuration(game_config, agent_desc=None, save_output=False):
    """
    This method converts the json configuration from a post into a more complete configuration
    with connections.

    GRANT: This file is undergoing a major change as of October '21.
      - In the first step, I load connections programmatically from
        'agent_conn.json', and do some reorganization to make the function
        easier to read.
      - Next, I remove the distinction between Agent and Storage.
      - I allow for new, user-defined agents to be parsed using the same methodology
    """
    full_game_config = {  # The object to be returned by this function.
        'agents': {},
        'termination': [],
    }
    # Create a working_config which will be modified by this function, so as
    # not to modify the original.
    working_config = copy.deepcopy(game_config)

    # Load the agent_desc to validate agents in config and get structure volume
    # November '22: This was formerly done by the database.
    if agent_desc is None:
        agent_desc = load_from_basedir('data_files/agent_desc.json')

    # Initialize supplemental agent_desc and agent_conn
    user_agent_desc = {}
    user_agent_conn = []
    is_b2 = False

    ###########################################################################
    #                   STEP 1: Add non-agent fields                          #
    ###########################################################################

    full_game_config['seed'] = working_config.get('seed', 1000)
    # Determines if multi-instance agents are independent agents or a group
    if 'single_agent' in working_config:
        single_agent = working_config.pop('single_agent')
    else:
        single_agent = 0
    full_game_config['single_agent'] = single_agent
    # Sets the start date of the simulation
    if 'start_time' in working_config and isinstance(working_config['start_time'], str):
        start_time = working_config.pop('start_time')
        full_game_config['start_time'] = start_time
    # Sets the length of the simulation
    if 'duration' in working_config and isinstance(working_config['duration'], dict):
        duration = working_config.pop('duration')
        full_game_config['termination'].append({
            'condition': 'time',
            'value': duration.get('value', 0) or 0,
            'unit': duration.get('type', 'day')})
    location = working_config.pop('location', 'mars')
    is_b2 = location == 'b2'
    full_game_config['location'] = 'earth' if is_b2 else location
    full_game_config['minutes_per_step'] = working_config.pop('minutes_per_step', 60)
    # Structures MUST step before other agents so that the `.._ratio` fields
    # are availble for threshold tests.
    if 'priorities' in working_config:
        priorities = working_config.pop('priorities')
        if isinstance(priorities, dict):
            full_game_config['priorities'] = priorities
    if 'priorities' not in full_game_config:
        full_game_config['priorities'] = ['structures', 'storage', 'power_generation',
                                          'inhabitants', 'eclss', 'plants']

    ###########################################################################
    #             STEP 2: Flatten the working_config object                   #
    ###########################################################################
    # The game_config object sent by the frontend has some irregularities. In
    # order to accomodate user-created agents in the future, we first flatten
    # items that are known to be irregular, add some custom information, and
    # then add everything in working_config, validating based on whether
    # they're in the database.

    # Structures: A string under 'habitat' and 'greenhouse' with selected type,
    # and optionally a dict of amounts (area) of b2-specific biomes.
    total_volume = 0  # Used to calculate starting water_storage
    if not is_b2:
        weights = {}
    elif working_config.get('startWithM1EndingAtmosphere'):
        weights = {
            'o2': 14.95,
            'co2': 0.32,
            'h2o': 0.9,
            'n2': 83.83,
            'h2': 0,
            'ch4': 0,
        }
    else:
        weights = {
            'o2': 18.75,  # Estimated from Severinghaus Figure 1
            'co2': 0.04,  # Estimated from Severinghaus Figure 1
            'h2o': 1,     # SIMOC default
            'n2': 80.21,  # Remainder of 100%
            'h2': 0,
            'ch4': 0,
        }
    active_structures = {}
    for generic in {'habitat', 'greenhouse'}:
        if generic in working_config:
            struct = working_config.pop(generic)
            amount = {'greenhouse_b2': 2000, 'crew_habitat_b2': 1000}.get(struct, 1)
            active_structures[struct] = amount
    if is_b2:
        biomes = working_config.pop('biomes', {})
        b2_structs = {'rainforest_biome': biomes.get('rainforest', 2000),
                      'desert_biome': biomes.get('desert', 1400),
                      'ocean_biome': biomes.get('ocean', 863),
                      'savannah_biome': biomes.get('savannah', 1637),
                      'west_lung': 1800,
                      'south_lung': 1800}
        active_structures.update(b2_structs)
    valid_structures = {k: v for k, v in active_structures.items()
                        if k in agent_desc['structures']}
    for struct, amount in valid_structures.items():
        volume = amount * next((
            char['value'] for char in
            agent_desc['structures'][struct]['data']['characteristics']
            if char['type'] == 'volume'), 0)
        total_volume += volume  # Used below to calculate starting water
        atmosphere = calc_air_storage(volume, weights)  # Fill with earth-normal atmosphere
        working_config[struct] = dict(id=1, amount=amount, **atmosphere)
    if len(valid_structures) > 1:
        working_config['atmosphere_equalizer'] = dict(id=1, amount=1)

    # Plants: A list of objects with 'species' and 'amount'
    plants_in_config = []
    input_food_storage = working_config.pop('food_storage', None)
    crop_mgmt_input = working_config.pop('improvedCropManagement', False)
    crop_mgmt_factor = 1.5 if crop_mgmt_input is True else 1
    crop_mgmt_char = {'type': 'crop_management_factor', 'value': crop_mgmt_factor}
    density_char = {'type': 'density_factor', 'value': 0.5}
    if 'plants' in working_config and isinstance(working_config['plants'], list):
        plants = working_config.pop('plants')
        for plant in plants:
            amount = plant.get('amount', 0) or 0
            plant_type = plant.get('species', None)
            if not (plant_type and amount):
                continue
            plants_in_config.append(plant_type)
            working_config[plant_type] = dict(amount=amount)
            if is_b2:
                plant_desc = {'data': {'characteristics': [
                    density_char, crop_mgmt_char
                ]}}
                if 'plants' not in user_agent_desc:
                    user_agent_desc['plants'] = {}
                user_agent_desc['plants'][plant_type] = plant_desc
    if plants_in_config:
        food_storage = {}
        food_storage_desc = agent_desc['storage']['food_storage']
        for char in food_storage_desc['data']['characteristics']:
            if char['type'].startswith('capacity'):
                currency = char['type'].split('_', 1)[1]
                if currency in plants_in_config:
                    food_storage[currency] = 0
        if food_storage:
            working_config['food_storage'] = dict(id=1, amount=1, **food_storage)
        # Lights
        if is_b2:
            working_config['b2_sun'] = {'amount': 1}
        else:
            # Replace generic lamp with species-specific lamp
            if 'lamp' in working_config:
                del working_config['lamp']
            user_agent_desc['structures'] = {}
            for species in plants_in_config:
                new_lamp_agent = copy.deepcopy(agent_desc['structures']['lamp'])
                # Set baseline PAR equal to species' baseline PAR
                species_desc = agent_desc['plants'][species]
                for i, char in enumerate(species_desc['data']['characteristics']):
                    if char['type'] == 'par_baseline':
                        par_baseline = char['value']
                        break
                for i, char in enumerate(new_lamp_agent['data']['characteristics']):
                    if char['type'] == 'par_baseline':
                        new_lamp_agent['data']['characteristics'][i]['value'] = par_baseline
                        break
                # Set amount equal to number of species
                species_amount = working_config[species]['amount']
                working_config[f'{species}_lamp'] = {'amount': species_amount}
                # Add a custom agent_desc and agent_conn for new lamp
                user_agent_desc['structures'][f'{species}_lamp'] = new_lamp_agent
                user_agent_conn += [
                    {'from': 'power_storage.kwh', 'to': f'{species}_lamp.kwh'},
                    {'from': f'{species}_lamp.par', 'to': f'{species}_lamp.par'},
                    {'from': f'{species}_lamp.par', 'to': f'{species}.par'},
                ]
     # Default Storages: Some listed, some not. Need to calculate amount.
    # 'food_storage' now holds fresh food, and 'ration_storage' holds the rations. Rations are
    # still pre-loaded to 'food_storage' on the front-end though, so need to change the label.
    if input_food_storage and input_food_storage.get('ration') is not None:                                                                # b2: initialize with food (plants) instead of rations
        if is_b2:
            starting_food = input_food_storage['ration']
            greenhouse_layout = {p: working_config[p]['amount'] for p in plants_in_config}
            total_crop_area = sum(greenhouse_layout.values())
            starting_food_storage = {k: starting_food*(v/total_crop_area) for k, v in greenhouse_layout.items()}
            working_config['food_storage'].update(starting_food_storage)
        else:
            working_config['ration_storage'] = input_food_storage
    for storage_type in ['water_storage', 'nutrient_storage', 'power_storage']:
        if storage_type in working_config and isinstance(working_config[storage_type], dict):
            storage = working_config.pop(storage_type)
        else:
            storage = {}
        # Preload water based on habitat & greenhouse volume
        if storage_type == 'water_storage' and total_volume:
            storage = dict(**storage, **calc_water_storage(total_volume))
        # Determine storage amount based on capacity and starting balance
        if 'amount' in storage and isinstance(storage['amount'], int):
            amount = max(1, storage.pop('amount'))
        else:
            amount = 1
        storage_desc = agent_desc['storage'][storage_type]
        for char in storage_desc['data']['characteristics']:
            if char['type'].startswith('capacity'):
                currency = char['type'].split('_', 1)[1]
                if currency not in storage:
                    storage[currency] = 0
                capacity = int(char['value'])
                amount = max(amount, math.ceil(storage[currency] / capacity))
        working_config[storage_type] = dict(id=1, amount=amount, **storage)
    if 'eclss' in working_config and isinstance(working_config['eclss'], dict):
        eclss = working_config.pop('eclss')
        if is_b2:
            eclss_agents = {
                'solid_waste_aerobic_bioreactor': 1,
                'urine_recycling_processor_VCD': 1,
                'multifiltration_purifier_post_treatment': 50,
                'co2_removal_SAWD': 5,
                'co2_makeup_valve': 5,
                'dehumidifier': 50,
                'o2_makeup_valve': 180,
                'o2_storage': 1,
                'co2_storage': 1,
            }
            for agent, amount in eclss_agents.items():
                working_config[agent] = {'id': 1, 'amount': amount}
            user_agent_desc['eclss'] = {}
            # CO2 Control System
            co2UpperLimit = eclss.get('co2UpperLimit', None)
            if co2UpperLimit is not None and 'co2_removal_SAWD' in working_config:
                decimal = round(co2UpperLimit/100, 4)
                sawd_desc = copy.deepcopy(agent_desc['eclss']['co2_removal_SAWD'])
                for i, flow in enumerate(sawd_desc['data']['input']):
                    if flow['type'] == 'co2':
                        sawd_desc['data']['input'][i]['criteria']['value'] = decimal
                user_agent_desc['eclss']['co2_removal_SAWD'] = sawd_desc
            co2Reserves = eclss.get('co2Reserves', None)
            if co2Reserves is not None and 'co2_storage' in working_config:
                working_config['co2_storage']['co2'] = co2Reserves
            co2LowerLimit = eclss.get('co2LowerLimit', None)
            if co2LowerLimit is not None and 'co2_makeup_valve' in working_config:
                decimal = round(co2LowerLimit, 4)
                valve_desc = copy.deepcopy(agent_desc['eclss']['co2_makeup_valve'])
                for i, flow in enumerate(valve_desc['data']['input']):
                    if flow['type'] == 'co2':
                        valve_desc['data']['input'][i]['criteria']['value'] = decimal
                user_agent_desc['eclss']['co2_makeup_valve'] = valve_desc
            # O2 Control System
            o2Reserves = eclss.get('o2Reserves', None)
            if o2Reserves is not None and 'o2_storage' in working_config:
                working_config['o2_storage']['o2'] = o2Reserves
            o2LowerLimit = eclss.get('o2LowerLimit', None)
            if o2LowerLimit is not None and 'o2_makeup_valve' in working_config:
                decimal = round(o2LowerLimit, 4)
                valve_desc = copy.deepcopy(agent_desc['eclss']['o2_makeup_valve'])
                for i, flow in enumerate(valve_desc['data']['input']):
                    if flow['type'] == 'o2':
                        valve_desc['data']['input'][i]['criteria']['value'] = decimal
                user_agent_desc['eclss']['o2_makeup_valve'] = valve_desc

        else:
            # ECLSS: A single item with an amount; needs to be broken into component agents
            eclss_agents = ['solid_waste_aerobic_bioreactor', 'multifiltration_purifier_post_treatment',
                            'oxygen_generation_SFWE', 'urine_recycling_processor_VCD', 'co2_removal_SAWD',
                            'co2_makeup_valve', 'co2_storage', 'co2_reduction_sabatier',
                            'ch4_removal_agent', 'dehumidifier']
            amount = eclss.get('amount', 0) or 0
            if amount:
                plant_area = (0 if not plants_in_config else
                            sum([working_config[p]['amount'] for p in plants_in_config]))
                human_amount = working_config['human_agent']['amount']
                for eclss_agent in eclss_agents:
                    this_amount = amount
                    # Scale certain components based on plant area
                    scale_with_plants = dict(  # Based on minimum required for 4hg preset
                        multifiltration_purifier_post_treatment=3/200,
                        co2_makeup_valve=4/200,
                        dehumidifier=5/200,
                    )
                    if eclss_agent in scale_with_plants:
                        required_amount = round(scale_with_plants[eclss_agent] * plant_area)
                        this_amount = max(this_amount, required_amount)
                    # Scale certain components based on number of humans
                    scale_with_humans = dict(
                        co2_removal_SAWD=2/4,
                    )
                    if eclss_agent in scale_with_humans:
                        required_amount = round(scale_with_humans[eclss_agent] * human_amount)
                        this_amount = max(this_amount, required_amount)
                    working_config[eclss_agent] = dict(id=1, amount=this_amount)
    # 'human_agent' and 'solar_pv...' are already in the correct format.

    # Update humans to reduce food consumption by 50%
    if is_b2:
        user_agent_desc['inhabitants'] = {}
        human_desc = copy.deepcopy(agent_desc['inhabitants']['human_agent'])
        for i, flow in enumerate(human_desc['data']['input']):
            if flow['type'] == 'food':
                human_desc['data']['input'][i]['value'] *= 0.5  # ADJUST
        user_agent_desc['inhabitants']['human_agent'] = human_desc


    ###########################################################################
    #                   STEP 3: Add all agents to output                      #
    ###########################################################################

    valid_agents = set()
    for agents in agent_desc.values():
        for agent in agents:
            valid_agents.add(agent)
    if len(user_agent_desc) > 0:
        for agents in user_agent_desc.values():
            for agent in agents:
                valid_agents.add(agent)
    for agent_type, attrs in working_config.items():
        if not isinstance(attrs, dict):
            print(f"Attributes for agent type {agent_type} must be a dict")
            continue
        if agent_type not in valid_agents:
            print(f"Agent type {agent_type} not found in agent_desc")
            continue
        full_game_config['agents'][agent_type] = attrs
    # Calculate the total number of agents. Used in `views.py` to enforce a
    # maximum number of agents per simulation (currently 50).
    if single_agent:
        full_game_config['total_amount'] = len(full_game_config['agents'])
    else:
        full_game_config['total_amount'] = \
            sum([agent.get('amount', 1) for agent in full_game_config['agents']])

    # Print result
    if save_output:
        timestamp = datetime.datetime.now()
        timestamp = timestamp.strftime("%m%d%H%M%S")
        with open(f"full_game_config_{timestamp}.json", "w") as f:
            json.dump(full_game_config, f)
        with open(f"user_agent_desc_{timestamp}.json", 'w') as f:
            json.dump(user_agent_desc, f)
        with open(f"user_agent_conn_{timestamp}.json", "w") as f:
            json.dump(user_agent_conn, f)

    return full_game_config, user_agent_desc, user_agent_conn


def load_from_basedir(fname):
    basedir = pathlib.Path(app.root_path).resolve().parent
    path = basedir / fname
    result = {}
    try:
        with path.open() as f:
            result = json.load(f)
    except OSError as e:
        app.logger.exception(f'Error opening {fname}: {e}')
    except ValueError as e:
        app.logger.exception(f'Error reading {fname}: {e}')
    except Exception as e:
        app.logger.exception(f'Unexpected error handling {fname}: {e}')
    finally:
        return result
