"""
Support for kiturami Component.
For more details about this platform, please refer to the documentation at
https://home-assistant.io/components/kiturami/
"""
import hashlib
import logging
import asyncio
#import threading
from datetime import timedelta

import homeassistant.helpers.config_validation as cv
import voluptuous as vol
from homeassistant.components.climate import (
    ClimateEntity, PLATFORM_SCHEMA, ClimateEntityFeature, HVACMode)
from homeassistant.const import (
    CONF_NAME, CONF_USERNAME, CONF_PASSWORD, TEMP_CELSIUS, ATTR_TEMPERATURE)
from homeassistant.helpers.aiohttp_client import async_get_clientsession
from homeassistant.exceptions import PlatformNotReady
from homeassistant.util import Throttle

_LOGGER = logging.getLogger(__name__)

KITURAMI_API_URL = 'https://igis.krb.co.kr/api'
DEFAULT_NAME = 'Kiturami'

MAX_TEMP = 45
MIN_TEMP = 10
HVAC_MODE_BATH = '목욕'
STATE_HEAT = '난방'
STATE_BATH = '목욕'
STATE_RESERVATION = '24시간 예약'
STATE_RESERVATION_REPEAT = '반복 예약'
STATE_AWAY = '외출'

MIN_TIME_BETWEEN_UPDATES = timedelta(minutes=15)

PLATFORM_SCHEMA = PLATFORM_SCHEMA.extend({
    vol.Optional(CONF_NAME, default=DEFAULT_NAME): cv.string,
    vol.Required(CONF_USERNAME): cv.string,
    vol.Required(CONF_PASSWORD): cv.string
})

#api_send_lock = threading.Lock()

async def async_setup_platform(hass, config, async_add_entities,
                               discovery_info=None):
    """Set up a kiturami."""

    name = config.get(CONF_NAME)
    username = config.get(CONF_USERNAME)
    password = config.get(CONF_PASSWORD)

    session = async_get_clientsession(hass)
    krb_api = KrbAPI(session, username, password)

    if not await krb_api.login():
        _LOGGER.error("Failed to login Kiturami")
        raise PlatformNotReady

    if not await krb_api.get_node_id():
        _LOGGER.error("Failed to node")
        raise PlatformNotReady

    slave_list = await krb_api.device_info()

    if not slave_list:
        _LOGGER.error("Failed to get slave divices")
        raise PlatformNotReady

    for slave in slave_list:
        async_add_entities([Kiturami(name+slave['alias'], DeviceAPI(krb_api, slave['slaveId']))], True)


class KrbAPI:
    """Kiturami Member API."""

    def __init__(self, session, username, password):
        """Initialize the Kiturami Member API.."""
        self.session = session
        self.username = username
        self.password = password
        self.auth_key = ''
        self.node_id = ''

    async def request(self, url, args):
        headers = {'Content-Type': 'application/json; charset=UTF-8',
                   'AUTH-KEY': self.auth_key}
        try:
            response = await self.session.post(url, headers=headers, json=args, timeout=10)
            _LOGGER.debug('JSON Request: %s\nJSON Response: %s', args, await response.text())
            return response
        except Exception as ex:
            _LOGGER.error('Failed to Kiturami API status Error: %s', ex)
            raise

    async def post(self, url, args):
        response = await self.request(url, args)
        if (response.status != 200 or not await response.text()) \
                and await self.login():
            response = await self.request(url, args)

        return await response.json(content_type='text/json')

    async def login(self):
        url = '{}/member/login'.format(KITURAMI_API_URL)
        password = hashlib.sha256(self.password.encode('utf-8'))
        args = {
            'memberId': self.username,
            'password': password.hexdigest()
        }
        response = await self.request(url, args)
        result = await response.json(content_type='text/json')
        self.auth_key = result['authKey']
        return self.auth_key

    async def get_node_id(self):
        url = '{}/member/getMemberDeviceList'.format(KITURAMI_API_URL)
        args = {
            'parentId': '1'
        }
        response = await self.post(url, args)
        self.node_id = response['memberDeviceList'][0]['nodeId']
        return self.node_id

    async def device_info(self):
        url = '{}/device/getDeviceInfo'.format(KITURAMI_API_URL)
        args = {
            'nodeId': self.node_id,
            'parentId': '1'
        }
        response = await self.post(url, args)
        return response['deviceSlaveInfo']

class DeviceAPI:
    """Kiturami Device API."""

    def __init__(self, krb, slave_id):
        """Initialize the Kiturami Member API.."""
        self.krb = krb
        self.slave_id = slave_id
        self.alive = {}
        self.is_alive = Throttle(MIN_TIME_BETWEEN_UPDATES)(self.is_alive)

    async def is_alive(self):
        url = '{}/device/isAlive'.format(KITURAMI_API_URL)
        args = {
            'nodeId': self.krb.node_id,
            'parentId': '1'
        }
        self.alive = await self.krb.post(url, args)

    async def device_mode_info(self, action_id='0102'):
        url = '{}/device/getDeviceModeInfo'.format(KITURAMI_API_URL)
        args = {
            'nodeId': self.krb.node_id,
            'actionId': action_id,
            'parentId': '1',
            'slaveId': self.slave_id
        }
        return await self.krb.post(url, args)

    async def device_control(self, message_id, message_body):
        #api_send_lock.acquire()
        url = '{}/device/deviceControl'.format(KITURAMI_API_URL)
        args = {
            'nodeIds': [self.krb.node_id],
            'messageId': message_id,
            'messageBody': message_body
        }
        response = await self.krb.post(url, args)
        await asyncio.sleep(1)
        #api_send_lock.release()
        return response

    async def turn_on(self):
        await self.device_control('0101', '{}0000000001'.format(self.slave_id))

    async def turn_off(self):
        await self.device_control('0101', '{}0000000002'.format(self.slave_id))

    async def mode_heat(self, target_temp=''):
        if not target_temp:
            response = await self.device_mode_info('0102')
            target_temp = response['value']
        body = '{}000000{}00'.format(self.slave_id, target_temp)
        await self.device_control('0102', body)

    async def mode_bath(self):
        response = await self.device_mode_info('0105')
        value = response['value']
        body = '00000000{}00'.format(value)
        await self.device_control('0105', body)

    async def mode_reservation(self):
        response = await self.device_mode_info('0107')
        body = '{}{}'.format(self.slave_id, response['value'])
        await self.device_control('0107', body)

    async def mode_reservation_repeat(self):
        response = await self.device_mode_info('0108')
        body = '{}000000{}{}'.format(self.slave_id, response['value'], response['option1'])
        await self.device_control('0108', body)

    async def mode_away(self):
        await self.device_control('0106', '{}0200000000'.format(self.slave_id))


class Kiturami(ClimateEntity):

    _enable_turn_on_off_backwards_compatibility = False

    def __init__(self, name, device):
        """Initialize the thermostat."""
        self._name = name
        self.device = device
        self.result = {'deviceMode':''}

    @property
    def unique_id(self):
        """Return a unique ID."""
        return self.device.krb.node_id + self.device.slave_id

    @property
    def name(self):
        """Return the name of the entity."""
        return self._name

    @property
    def device_info(self):
        """Return information about the device."""
        return {
            "identifiers": {('kiturami', self.device.krb.node_id + self.device.slave_id)},
            'name': 'Kiturami IOT',
            'manufacturer': 'kiturami',
            'model': 'NCTR',
            'device_alias': self.result['deviceAlias']
        }

    @property
    def device_state_attributes(self):
        """Return the state attributes of the device."""
        return {
            'node_id': self.device.krb.node_id + self.device.slave_id,
            'device_mode': self.result['deviceMode']
        }

    @property
    def supported_features(self):
        """Return the list of supported features."""
        features = 0
        if self.is_on:
            features |= ClimateEntityFeature.PRESET_MODE
        if self.preset_mode == STATE_HEAT:
            features |= ClimateEntityFeature.TARGET_TEMPERATURE
        return features

    @property
    def available(self):
        """Return True if entity is available."""
        alive = self.device.alive

        _LOGGER.debug("alive: %s", alive)

        if not alive:
            return False
        return alive['message'] == "Success."

    @property
    def temperature_unit(self):
        """Return the unit of measurement which this thermostat uses."""
        return TEMP_CELSIUS

    @property
    def target_temperature_step(self):
        """Return the supported step of target temperature."""
        return 1

    @property
    def min_temp(self):
        """Return the minimum temperature."""
        return MIN_TEMP

    @property
    def max_temp(self):
        """Return the maximum temperature."""
        return MAX_TEMP

    @property
    def is_on(self):
        """Return true if heater is on."""
        return self.result['deviceMode'] != '0101'

    @property
    def current_temperature(self):
        """Return the current temperature."""
        return int(self.result['currentTemp'], 16)

    @property
    def target_temperature(self):
        """Return the temperature we try to reach."""
        return int(self.result['value'], 16)

    @property
    def hvac_mode(self):
        """Return hvac operation ie. heat, cool mode.
        Need to be one of HVAC_MODE_*.
        """
        if self.is_on:
            return HVACMode.HEAT
        return HVACMode.OFF

    @property
    def hvac_modes(self):
        """Return the list of available hvac operation modes.
        Need to be a subset of HVAC_MODES.
        """
        return [HVACMode.OFF, HVACMode.HEAT]

    async def async_set_temperature(self, **kwargs):
        """Set new target temperature."""
        if self.is_on is False:
            await self.device.turn_on()
        temperature = kwargs.get(ATTR_TEMPERATURE)
        if temperature is None:
            return
        await self.device.mode_heat('{:X}'.format(int(temperature)))

    @property
    def preset_modes(self):
        """Return a list of available preset modes.
        Requires SUPPORT_PRESET_MODE.
        """
        return [STATE_HEAT, STATE_BATH, STATE_RESERVATION,
                STATE_RESERVATION_REPEAT, STATE_AWAY]

    @property
    def preset_mode(self):
        """Return the current preset mode, e.g., home, away, temp.
        Requires SUPPORT_PRESET_MODE.
        """
        operation_mode = self.result['deviceMode']
        if operation_mode == '0102':
            return STATE_HEAT
        elif operation_mode == '0105':
            return STATE_BATH
        elif operation_mode == '0107':
            return STATE_RESERVATION
        elif operation_mode == '0108':
            return STATE_RESERVATION_REPEAT
        elif operation_mode == '0106':
            return STATE_AWAY
        else:
            return STATE_HEAT

    async def async_set_preset_mode(self, preset_mode):
        """Set new preset mode."""

        if self.is_on is False:
            await self.device.turn_on()
        if preset_mode == STATE_HEAT:
            await self.device.mode_heat()
        elif preset_mode == STATE_BATH:
            await self.device.mode_bath()
        elif preset_mode == STATE_RESERVATION:
            await self.device.mode_reservation()
        elif preset_mode == STATE_RESERVATION_REPEAT:
            await self.device.mode_reservation_repeat()
        elif preset_mode == STATE_AWAY:
            await self.device.mode_away()
        else:
            _LOGGER.error("Unrecognized operation mode: %s", preset_mode)

    async def async_set_hvac_mode(self, hvac_mode):
        """Set new target hvac mode."""
        if hvac_mode == HVACMode.HEAT:
            await self.device.turn_on()
        elif hvac_mode == HVACMode.OFF:
            await self.device.turn_off()

    async def async_update(self):
        """Retrieve latest state."""
        await self.device.is_alive()
        await asyncio.sleep(1)
        self.result = await self.device.device_mode_info()
