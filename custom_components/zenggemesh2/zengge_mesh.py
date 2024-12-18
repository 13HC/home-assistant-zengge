"""Zengge Mesh handler"""
import logging
import asyncio
import async_timeout
import homeassistant.util.dt as dt_util
from datetime import timedelta
from homeassistant.core import HomeAssistant, callback, CALLBACK_TYPE
from homeassistant.const import EVENT_HOMEASSISTANT_STARTED, EVENT_HOMEASSISTANT_STOP
from homeassistant.components import bluetooth
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed

# import zenggemeshlight from .zenggemeshlight
from .zenggemeshlight import ZenggeMeshLight
from .const import DOMAIN

_LOGGER = logging.getLogger(__name__)


class ZenggeMesh2(DataUpdateCoordinator):

    def __init__(self, hass: HomeAssistant, mesh_name: str, mesh_password: str, mesh_long_term_key: str):
        """
        Args :
            hass: HomeAssistance core
            mesh_name: The mesh name as a string
            mesh_password: The mesh password as a string
            mesh_long_term_key: The new long term key as a string
        """
        super().__init__(
            hass,
            _LOGGER,
            name=DOMAIN,
            update_interval=timedelta(seconds=30),
        )

        self.hass = hass
        self._mesh_name = mesh_name
        self._mesh_password = mesh_password
        self._mesh_long_term_key = mesh_long_term_key

        self._connected_bluetooth_device: ZenggeMeshLight = None
        self._scanning_devices = False

        self.last_update_success = True
        self._state = {
            'last_rssi_check': None,
            'last_connection': None,
            'connected_device': None,
        }

        self._devices = {}

        #self._queue = queue.Queue()
        self._shutdown = False
        #self._command_tread = threading.Thread(target=self._process_command_queue,
        #                                       name="ZenggeMeshCommands-" + self._mesh_name)
        #self._command_tread.daemon = True
        #self._command_tread.start()

        self._startup = False
        async def startup(event):
            _LOGGER.debug('startup')
            self._startup = True
            await self._async_get_devices_rssi()
            #asyncio.run_coroutine_threadsafe(
            #    self.async_refresh(), hass.loop
            #).result()

        async def shutdown(event):
            _LOGGER.info('[%s] Shutdown mesh!!', self.mesh_name)
            self._shutdown = True
            await self._disconnect_current_device()
            #asyncio.run_coroutine_threadsafe(
            #    self.async_shutdown(), hass.loop
            #).result()

        hass.bus.async_listen_once(EVENT_HOMEASSISTANT_STARTED, startup)
        hass.bus.async_listen_once(EVENT_HOMEASSISTANT_STOP, shutdown)


    @property
    def mesh_name(self) -> str:
        return self._mesh_name

    @property
    def identifier(self) -> str:
        return 'zengge_mesh.' + self._mesh_name

    @property
    def state(self):
        return self._state

    def register_device(self, mesh_id: int, mac: str, name: str, callback_func: CALLBACK_TYPE):
        self._devices[mesh_id] = {
            'mac': mac,
            'name': name,
            'callback': callback_func,
            'last_update': None,
            'update_count': 0,
            'status_request_count': 0,
            'rssi': -999999
        }

        _LOGGER.info('[%s] Registered [%s] %d', self.mesh_name, mac, mesh_id)

    def is_connected(self) -> bool:
        return self._connected_bluetooth_device and self._connected_bluetooth_device.is_connected

    def is_reconnecting(self) -> bool:
        return self._connected_bluetooth_device and self._connected_bluetooth_device.reconnecting

    async def _async_update_data(self):
        if self._state['last_rssi_check'] is None: #Run RSSI check if new integration (no restart required)
            _LOGGER.info('zenggemesh async update data - RSSI Check')
            await self._async_get_devices_rssi()
            return
        _LOGGER.info('zenggemesh async update data...')

        if not self.is_connected():
            await self._async_connect_device()

        if not self.is_connected():
            return False
        _LOGGER.info('zenggemesh async update data 2...')
        #if not self._command_tread.is_alive():
        #    raise UpdateFailed("Command tread died!")

        # Reconnect bluetooth every 2 ours to prevent connection freeze
        #if self._state['last_connection'] is not None \
        #        and self._state['last_connection'] < dt_util.now() - timedelta(hours=2):
        #    _LOGGER.info('async_update: Force disconnect to prevent connection freeze')
        #    async with async_timeout.timeout(10):
        #        await self._disconnect_current_device()
        #_LOGGER.info('zenggemesh async update data 3...')
        if self._state['last_rssi_check'] is None:
            try:
                async with async_timeout.timeout(20):
                    # Scan for devices and get try to determine there RSSI
                    await self._async_get_devices_rssi()
            except Exception as e:
                _LOGGER.warning('[%s] Fetching RSSI failed - [%s] %s', self.mesh_name, type(e).__name__, e)
        _LOGGER.info('zenggemesh async update data 4...')
        try:
            async with async_timeout.timeout(20):
                await self.async_request_status()
                self.last_update_success = True
        except Exception as e:
            _LOGGER.info('[%s] Requesting status failed - [%s] %s', self.mesh_name, type(e).__name__, e)

        # Not connected after executing command then we assume we could not connect to a device
        if not self.is_connected():
            # Disable all when 2nd run is also not successful
            if not self.last_update_success:
                self.update_status_of_all_devices_to_disabled()
            self.last_update_success = False

            raise UpdateFailed('Reconnecting to BLE device' if self.is_reconnecting() else 'No device connected')

        # Give mesh time to gather status updates
        await asyncio.sleep(2)

        for mesh_id, device_info in self._devices.items():

            _LOGGER.info(f'[{self.mesh_name}][{device_info["name"]}] update count: {device_info["update_count"]}; request count: {device_info["status_request_count"]}; RSSI: {device_info["rssi"]}; last update: {device_info["last_update"]}')

            # Force status update for specific mesh_id when no new update for the last 60 secs
            if device_info['last_update'] is None \
                    or device_info['last_update'] < dt_util.now() - timedelta(seconds=60):
                _LOGGER.info('[%s][%s][%d] async_update: Device offline for 60+ secs', self.mesh_name, device_info['name'], mesh_id)

                #self._devices[mesh_id]['status_request_count'] += 1
                #async with async_timeout.timeout(20): #No need to do specific device check due to previous check hitting ALL devices
                #    await self.async_request_status()
                #    #await self._async_add_command_to_queue('requestStatus', {'dest': mesh_id, 'withResponse': True}, True)

                # Give mesh time to gather status updates
                #await asyncio.sleep(.5)

            # Disable devices we didn't get a response the last 90 seconds
            if self._devices[mesh_id]['last_update'] is not None \
                    and self._devices[mesh_id]['last_update'] < dt_util.now() - timedelta(seconds=90):
                self._devices[mesh_id]['callback']({'state': None})
                self._devices[mesh_id]['last_update'] = None
                self._devices[mesh_id]['update_count'] = 0
                # Device offline then we assume it's also out-of-range (device that's not always powered on for instance)
                if self._devices[mesh_id]['rssi'] > -9999:
                    self._devices[mesh_id]['rssi'] = -9999

        return self._state

    def update_status_of_all_devices_to_disabled(self):
        _LOGGER.info("------***------All devices disabled------***------")
        for mesh_id, device_info in self._devices.items():
            if device_info['last_update'] is not None:
                device_info['callback']({'state': None})
                self._devices[mesh_id]['last_update'] = None
                self._devices[mesh_id]['update_count'] = 0
        self._state['last_rssi_check'] = None
        self._state['connected_device'] = None

    async def _async_update_mesh_state(self):
        if not self.is_connected() and not self.is_reconnecting():
            self._state['connected_device'] = None

        for update_callback, _ in list(self._listeners.values()):
            update_callback()

    @callback
    def mesh_status_callback(self, status):
        if 'mesh_id' not in status or status['mesh_id'] not in self._devices:
            _LOGGER.info('[%s] Status feedback of unknown device - [%s]',
                         self.mesh_name, status['mesh_id'] if 'mesh_id' in status else 'unknown')
            return

        _LOGGER.info('[%s][%s][%d] mesh_status_callback(%s)',
                      self.mesh_name, self._devices[status['mesh_id']]['name'], status['mesh_id'], status)

        #if status['type'] != 'status':
        #    _LOGGER.info('[%s][%s][%d] skipping all non status callbacks',
        #              self.mesh_name, self._devices[status['mesh_id']]['name'], status['mesh_id'])
        #    return

        self._devices[status['mesh_id']]['callback'](status)

        self._devices[status['mesh_id']]['last_update'] = dt_util.now()
        self._devices[status['mesh_id']]['update_count'] += 1

    async def async_request_status(self):
        await self._connected_bluetooth_device.requestStatus()

    async def async_on(self, mesh_id: int):
        await self._connected_bluetooth_device.on(mesh_id)

    async def async_off(self, mesh_id: int, _attempt: int = 0):
        await self._connected_bluetooth_device.off(mesh_id)

    async def async_set_color(self, mesh_id: int, r: int, g: int, b: int, _attempt: int = 0):
        await self._connected_bluetooth_device.setColor(r,g,b,mesh_id)

    async def async_set_color_brightness(self, mesh_id: int, brightness: int, _attempt: int = 0):
        await self._connected_bluetooth_device.setColorBrightness(brightness,mesh_id)

    async def async_set_white_temperature(self, mesh_id: int, white_temperature: int, _attempt: int = 0):
        await self._connected_bluetooth_device.setWhiteTemperature(white_temperature,mesh_id)

    async def async_set_white_brightness(self, mesh_id: int, brightness: int, _attempt: int = 0):
        await self._connected_bluetooth_device.setWhiteBrightness(brightness,mesh_id)

    async def _disconnect_current_device(self):
        if not self._connected_bluetooth_device:
            return
        try:
            device = self._connected_bluetooth_device
            self._connected_bluetooth_device = None
            async with async_timeout.timeout(10):
                await device.disconnect()
        except Exception as e:
            _LOGGER.exception('[%s] Failed to disconnect [%s] %s', self.mesh_name, type(e).__name__, e)

        await self._async_update_mesh_state()

    async def async_shutdown(self):
        _LOGGER.info('[%s] Shutdown mesh', self.mesh_name)
        self._shutdown = True
        return await self._disconnect_current_device()
    
    async def async_refresh(self):
        _LOGGER.info('[%s] ****ASYNC REFRESH****', self.mesh_name)
        await self._async_get_devices_rssi()

    async def _async_add_command_to_queue(self, command: str, params, allow_to_fail: bool = False):
        _LOGGER.info('[%s] Queue command %s %s', self.mesh_name, command, params)

        if not self._command_tread.is_alive():
            raise UpdateFailed("Command tread died!")

        done = False

        def command_executed():
            nonlocal done
            done = True

        self._queue.put({
            'command': command,
            'params': params,
            'callback': command_executed,
            'allow_to_fail': allow_to_fail
        })
        while not done:
            await asyncio.sleep(.01)

    def _process_command_queue(self):
        while not self._shutdown:

            _LOGGER.debug('[%s] get item from queue', self.mesh_name)
            command = self._queue.get()
            _LOGGER.debug('[%s] process 0/%d - %s', self.mesh_name, self._queue.qsize(), command)
            try:
                tries = 0
                while not self._call_command(command) and tries < 3:
                    tries = tries + 1
                    _LOGGER.warning('[%s] Command failed, retry %s', self.mesh_name, tries)

            except Exception as e:
                _LOGGER.error('[%s] Command failed and skipped - [%s] %s', self.mesh_name, type(e).__name__, e)
                asyncio.run_coroutine_threadsafe(
                    self._disconnect_current_device(), self.hass.loop
                ).result()

            if 'callback' in command:
                command['callback']()

            self._queue.task_done()

    def _call_command(self, command) -> bool:
        self._connect_device()
        if not self.is_connected():
            return False

        failed = False
        try:
            # Call command
            if isinstance(command['params'], tuple):
                result = getattr(self._connected_bluetooth_device, command['command'])(*command['params'])
            else:
                result = getattr(self._connected_bluetooth_device, command['command'])(**command['params'])
            _LOGGER.debug(f'[{self.mesh_name}] Send command {command["command"]} got result {result}')
        except Exception as e:
            _LOGGER.exception('[%s] Command failed, re-connecting for new attempt - [%s] %s', self.mesh_name, type(e).__name__, e)
            result = None
            failed = True

        _LOGGER.debug('[%s] Command result: %s', self.mesh_name, result)

        # We always expect result else we assume command wasn't successful
        if result is None and not command['allow_to_fail'] and not failed:
            _LOGGER.warning('[%s] Timeout executing command, probably Bluetooth connection is lost/frozen, re-connecting', self.mesh_name)
            failed = True

        if failed:
            asyncio.run_coroutine_threadsafe(
                self._disconnect_current_device(), self.hass.loop
            ).result()

        # Only report failure for commands that we do not allow to fail (status updates are for example commands we allow to fail)
        if failed and not command['allow_to_fail']:
            return False

        return True

    def _connect_device(self):
        asyncio.run_coroutine_threadsafe(
            self._async_connect_device(), self.hass.loop
        ).result()

    async def _async_connect_device(self):
        _LOGGER.info('zenggemesh async connect device...')
        while self.is_reconnecting():
            await asyncio.sleep(.1)
        if self.is_connected():
            return
        for mesh_id, device_info in self._getConnectableDevices():
            if device_info['rssi'] <= -127:  #Anything equal to or below -127 is not in connection range
                continue
            while self.is_reconnecting():
                await asyncio.sleep(.1)
            if self.is_connected():
                self._connected_bluetooth_device = device
                self._state['connected_device'] = device_info['name']
                self._state['last_connection'] = dt_util.now()
                await self._async_update_mesh_state()
                _LOGGER.info("[%s][%s][%s] Connected", self.mesh_name, device_info['name'], device_info['mac'])
                break
            if device_info['mac'] is None:
                continue
            #ble_device = bluetooth.async_ble_device_from_address(self.hass, device_info['mac'])
            device = ZenggeMeshLight(device_info['mac'], None, self._mesh_name, self._mesh_password, hass=self.hass, disconnect_callback=self.update_status_of_all_devices_to_disabled)
            try:
                _LOGGER.info("[%s][%s][%s] Trying to connect", self.mesh_name, device_info['name'], device_info['mac'])
                async with async_timeout.timeout(30):
                    if await device.connect():
                        self._connected_bluetooth_device = device
                        self._state['connected_device'] = device_info['name']
                        self._state['last_connection'] = dt_util.now()
                        await self._async_update_mesh_state()
                        _LOGGER.info("[%s][%s][%s] Connected", self.mesh_name, device_info['name'], device_info['mac'])
                        break
                    else:
                        _LOGGER.info("[%s][%s][%s] Could not connect", self.mesh_name, device_info['name'], device_info['mac'])
            except Exception as e:
                _LOGGER.info('[%s][%s][%s] Failed to connect, trying next device [%s] %s',
                                  self.mesh_name, device_info['name'], device_info['mac'], type(e).__name__, e)

            _LOGGER.info('[%s][%s][%s] Setting up Bluetooth connection failed, making sure Bluetooth device stops trying', self.mesh_name, device_info['name'], device_info['mac'])

            await device.stop()

        _LOGGER.info('zenggemesh async connect device 4...')

        if self.is_connected():
            self._connected_bluetooth_device.status_callback = self.mesh_status_callback
        else:
            # Force new RSSI check no device we could connect to
            self._state['last_rssi_check'] = None
            _LOGGER.info("[%s][%s][%s] Last RSSI Check set to None", self.mesh_name, device_info['name'], device_info['mac'])
            await self._async_update_mesh_state()

    def _getConnectableDevices(self):
        # Sort devices by rssi and only return devices with a RSSI that could be in range
        return filter(lambda device: device[1]['rssi'] > -9999, sorted(self._devices.items(), key=lambda t: t[1]['rssi'], reverse=True))

    async def _async_get_devices_rssi(self):
        device_available = False
        if self._scanning_devices:
            _LOGGER.info(f'[{self.mesh_name}] Already scanning for devices')
            return

        _LOGGER.info(f'[{self.mesh_name}] Search for Zengge devices to find closest (best RSSI value) device')

        self._scanning_devices = True

        devices = bluetooth.async_discovered_service_info(self.hass).mapping
        _LOGGER.debug(f'[{self.mesh_name}] Scan result: {repr(devices.keys())}')

        for mesh_id, device_info in self._devices.items():
            if device_info['mac'].upper() in devices.keys() and devices.get(device_info['mac'].upper()).rssi is not None:
                _LOGGER.info('[%s][%s][%s] Bluetooth scan returns RSSI value = %s', self.mesh_name, device_info['name'],
                             device_info['mac'], devices.get(device_info['mac'].upper()).rssi)
                self._devices[mesh_id]['rssi'] = devices.get(device_info['mac'].upper()).rssi
                if self._devices[mesh_id]['rssi'] >= -127:
                    device_available = True

            elif device_info['mac'].upper() in devices.keys():
                _LOGGER.info('[%s][%s][%s] Bluetooth scan returns no RSSI value', self.mesh_name, device_info['name'], device_info['mac'])
                self._devices[mesh_id]['rssi'] = -99999

            else:
                _LOGGER.info('[%s][%s][%s] Device NOT found during Bluetooth scan', self.mesh_name, device_info['name'], device_info['mac'])
                self._devices[mesh_id]['rssi'] = -999999

        if device_available == True:
            self._state['last_rssi_check'] = dt_util.now()
        else:
            self._state['last_rssi_check'] = None
            _LOGGER.info(f'[{self.mesh_name}] No available devices found during RSSI scan')
        await self._async_update_mesh_state()

        self._scanning_devices = False