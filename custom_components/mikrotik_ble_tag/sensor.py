import logging
import struct
import asyncio
import math
from datetime import timedelta
from homeassistant.helpers.entity import Entity
from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.const import (
    CONF_NAME,
    CONF_MAC,
    UnitOfTemperature,
    PERCENTAGE,
    SIGNAL_STRENGTH_DECIBELS_MILLIWATT,
)
from homeassistant.components.sensor import SensorDeviceClass, PLATFORM_SCHEMA
from bleak import BleakScanner
from bleak.exc import BleakError
from .const import DOMAIN

_LOGGER = logging.getLogger(__name__)

# Constants
SCAN_INTERVAL = timedelta(seconds=60)  # Scan interval as a timedelta object

# Flag bit masks
FLAG_REED_SWITCH = 0x01  # 1st bit
FLAG_ACCEL_TILT = 0x02    # 2nd bit
FLAG_ACCEL_FREE_FALL = 0x04  # 3rd bit
FLAG_IMPACT_X = 0x08      # 4th bit
FLAG_IMPACT_Y = 0x10      # 5th bit
FLAG_IMPACT_Z = 0x20      # 6th bit

async def async_setup_entry(hass, config_entry, async_add_entities):
    """Set up the MikroTik BLE Tag sensor from a config entry."""
    name = config_entry.data[CONF_NAME]
    mac = config_entry.data[CONF_MAC]

    # Create a device registry entry
    device_info = DeviceInfo(
        identifiers={(DOMAIN, mac)},  # Unique identifier for the device
        name=name,  # Name of the device
        manufacturer="MikroTik",
        model="BLE Tag",
    )

    # Create a list of sensors for each attribute
    sensors = [
        MikroTikBLETagSensor(name, mac, "temperature", SensorDeviceClass.TEMPERATURE, UnitOfTemperature.CELSIUS, device_info),
        MikroTikBLETagSensor(name, mac, "battery", SensorDeviceClass.BATTERY, PERCENTAGE, device_info),
        MikroTikBLETagSensor(name, mac, "rssi", SensorDeviceClass.SIGNAL_STRENGTH, SIGNAL_STRENGTH_DECIBELS_MILLIWATT, device_info),
        MikroTikBLETagSensor(name, mac, "acceleration_x", None, "m/s²", device_info),
        MikroTikBLETagSensor(name, mac, "acceleration_y", None, "m/s²", device_info),
        MikroTikBLETagSensor(name, mac, "acceleration_z", None, "m/s²", device_info),
        MikroTikBLETagSensor(name, mac, "total_acceleration", None, "m/s²", device_info),  # Add total acceleration sensor
        MikroTikBLETagSensor(name, mac, "uptime", None, None, device_info),  # Uptime sensor
        MikroTikBLETagSensor(name, mac, "flag_reed_switch", None, None, device_info),  # Reed switch flag
        MikroTikBLETagSensor(name, mac, "flag_accel_tilt", None, None, device_info),  # Tilt flag
        MikroTikBLETagSensor(name, mac, "flag_accel_free_fall", None, None, device_info),  # Free fall flag
        MikroTikBLETagSensor(name, mac, "flag_impact_x", None, None, device_info),  # Impact on x-axis flag
        MikroTikBLETagSensor(name, mac, "flag_impact_y", None, None, device_info),  # Impact on y-axis flag
        MikroTikBLETagSensor(name, mac, "flag_impact_z", None, None, device_info),  # Impact on z-axis flag
    ]

    # Add the sensors to Home Assistant
    async_add_entities(sensors, update_before_add=True)

class MikroTikBLETagSensor(Entity):
    """Representation of a MikroTik BLE Tag sensor."""

    def __init__(self, name, mac, attribute, device_class, unit_of_measurement, device_info):
        """Initialize the sensor."""
        self._name = f"{name} {attribute.replace('_', ' ').title()}"
        self._mac = mac
        self._attribute = attribute
        self._device_class = device_class
        self._unit_of_measurement = unit_of_measurement
        self._state = None
        self._device_info = device_info
        self._unique_id = f"{mac}_{attribute}"  # Unique ID for the entity
        self._scanner = None  # BleakScanner instance
        self._scan_task = None  # Background scan task

    @property
    def name(self):
        """Return the name of the sensor."""
        return self._name

    @property
    def state(self):
        """Return the state of the sensor."""
        return self._state

    @property
    def device_class(self):
        """Return the device class of the sensor."""
        return self._device_class

    @property
    def unit_of_measurement(self):
        """Return the unit of measurement."""
        return self._unit_of_measurement

    @property
    def device_info(self):
        """Return device information."""
        return self._device_info

    @property
    def unique_id(self):
        """Return the unique ID of the sensor."""
        return self._unique_id

    async def async_added_to_hass(self):
        """Run when entity is added to Home Assistant."""
        await super().async_added_to_hass()
        # Start the continuous scan when the sensor is added to Home Assistant
        self._scanner = BleakScanner()
        self._scan_task = asyncio.create_task(self._continuous_scan())

    async def async_will_remove_from_hass(self):
        """Run when entity is removed from Home Assistant."""
        await super().async_will_remove_from_hass()
        # Stop the continuous scan when the sensor is removed
        if self._scan_task:
            self._scan_task.cancel()
            try:
                await self._scan_task
            except asyncio.CancelledError:
                pass
        if self._scanner:
            await self._scanner.stop()

    async def _continuous_scan(self):
        """Continuously scan for BLE advertisements and update the sensor state."""
        try:
            def detection_callback(device, advertisement_data):
                """Callback for when a device is discovered."""
                if device.address.lower() == self._mac.lower():
                    self._process_advertisement_data(device, advertisement_data)

            self._scanner.register_detection_callback(detection_callback)
            await self._scanner.start()

            # Keep the scan running indefinitely
            while True:
                await asyncio.sleep(1)  # Sleep to avoid busy-waiting

        except Exception as e:
            _LOGGER.error(f"Error during continuous scan: {e}")

    def _process_advertisement_data(self, device, advertisement_data):
        """Process advertisement data and update the sensor state."""
        try:
            if advertisement_data.manufacturer_data:
                for manufacturer_id, data in advertisement_data.manufacturer_data.items():
                    if manufacturer_id == 0x094F:  # MikroTik manufacturer ID
                        _LOGGER.debug(f"Raw advertisement data: {data.hex()}")
                        attributes = self.parse_mikrotik_data(data)
                        self._state = attributes.get(self._attribute)
                        
                        # Update RSSI if the attribute is 'rssi'
                        if self._attribute == "rssi":
                            self._state = advertisement_data.rssi  # Use rssi from AdvertisementData
                        
                        # Notify Home Assistant of the state update
                        self.async_write_ha_state()
                        return  # Exit the loop if data is found
        except Exception as e:
            _LOGGER.error(f"Error processing advertisement data: {e}")

    def parse_mikrotik_data(self, data):
        """Parse MikroTik BLE Tag data from advertisement packets."""
        try:
            # Log the raw data for debugging
            _LOGGER.debug(f"Raw advertisement data (hex): {data.hex()}")
            _LOGGER.debug(f"Raw advertisement data (length): {len(data)} bytes")

            # Ensure the data is exactly 18 bytes
            if len(data) != 18:
                _LOGGER.error(f"Invalid data length: expected 18 bytes, got {len(data)} bytes")
                return {}

            # Unpack the 18 bytes of data
            (
                payload_version,  # 1 byte
                encryption_flag,  # 1 byte
                salt,             # 2 bytes
                acc_x_raw,        # 2 bytes (little-endian)
                acc_y_raw,        # 2 bytes (little-endian)
                acc_z_raw,        # 2 bytes (little-endian)
                temperature_raw,  # 2 bytes (little-endian)
                uptime,           # 4 bytes
                flag,             # 1 byte
                battery,          # 1 byte
            ) = struct.unpack('<BBHhhhhIBB', data)

            # Function to swap octets for little-endian values
            def swap_octets(value):
                return ((value & 0xFF) << 8) | ((value >> 8) & 0xFF)

            # Convert acceleration from signed 8.8 fixed-point format to m/s²
            def convert_acceleration(value):
                # Swap octets for little-endian format
                swapped_value = swap_octets(value)
                # Convert to signed 8.8 fixed-point format
                acceleration = swapped_value / 256.0
                # Ignore invalid acceleration values (outside reasonable range)
                if acceleration < -16 or acceleration > 16:  # Typical range for acceleration sensors
                    return 0.0  # Set to 0.0 if invalid
                return acceleration

            # Convert temperature from signed 16-bit integer 8.8 fixed-point format to Celsius
            def convert_temperature(value):
                # Swap octets for little-endian format
                swapped_value = swap_octets(value)
                # Handle two's complement for signed 16-bit integer
                if swapped_value > 0x7FFF:  # If the value is negative
                    swapped_value -= 0x10000
                # Convert to signed 8.8 fixed-point format
                temperature_celsius = swapped_value / 256.0
                # Ignore invalid temperature values (outside reasonable range)
                if temperature_celsius < -50 or temperature_celsius > 100:
                    return None
                return temperature_celsius

            # Convert battery value to percentage
            def convert_battery(value):
                # Clamp battery value to valid range (0-100)
                if value < 0 or value > 100:
                    return None
                return value

            # Convert uptime to days, hours, minutes, and seconds
            def convert_uptime(uptime_seconds):
                days = uptime_seconds // (24 * 3600)
                uptime_seconds %= (24 * 3600)
                hours = uptime_seconds // 3600
                uptime_seconds %= 3600
                minutes = uptime_seconds // 60
                seconds = uptime_seconds % 60
                return f"{days}d {hours}h {minutes}m {seconds}s"

            # Calculate total acceleration
            def calculate_total_acceleration(acc_x, acc_y, acc_z):
                if acc_x is None or acc_y is None or acc_z is None:
                    return None
                return math.sqrt(acc_x**2 + acc_y**2 + acc_z**2)

            # Convert acceleration values and ignore invalid data
            acc_x_converted = convert_acceleration(acc_x_raw)
            acc_y_converted = convert_acceleration(acc_y_raw)
            acc_z_converted = convert_acceleration(acc_z_raw)

            # Convert temperature and ignore invalid values
            temperature_converted = convert_temperature(temperature_raw)

            # Convert battery value and ignore invalid values
            battery_converted = convert_battery(battery)

            # Convert uptime to human-readable format
            uptime_converted = convert_uptime(uptime)

            # Parse flag bits
            flag_reed_switch = bool(flag & FLAG_REED_SWITCH)
            flag_accel_tilt = bool(flag & FLAG_ACCEL_TILT)
            flag_accel_free_fall = bool(flag & FLAG_ACCEL_FREE_FALL)
            flag_impact_x = bool(flag & FLAG_IMPACT_X)
            flag_impact_y = bool(flag & FLAG_IMPACT_Y)
            flag_impact_z = bool(flag & FLAG_IMPACT_Z)

            # Log the parsed values for debugging
            _LOGGER.debug(f"Parsed values: payload_version={payload_version}, encryption_flag={encryption_flag}, salt={salt}, "
                          f"acc_x={acc_x_converted}, acc_y={acc_y_converted}, acc_z={acc_z_converted}, temperature={temperature_converted}, "
                          f"uptime={uptime_converted}, flag={flag}, battery={battery_converted}, "
                          f"flag_reed_switch={flag_reed_switch}, flag_accel_tilt={flag_accel_tilt}, flag_accel_free_fall={flag_accel_free_fall}, "
                          f"flag_impact_x={flag_impact_x}, flag_impact_y={flag_impact_y}, flag_impact_z={flag_impact_z}")

            # Return parsed attributes
            return {
                "acceleration_x": acc_x_converted,
                "acceleration_y": acc_y_converted,
                "acceleration_z": acc_z_converted,
                "total_acceleration": calculate_total_acceleration(acc_x_converted, acc_y_converted, acc_z_converted),
                "temperature": temperature_converted,
                "uptime": uptime_converted,
                "flag_reed_switch": flag_reed_switch,
                "flag_accel_tilt": flag_accel_tilt,
                "flag_accel_free_fall": flag_accel_free_fall,
                "flag_impact_x": flag_impact_x,
                "flag_impact_y": flag_impact_y,
                "flag_impact_z": flag_impact_z,
                "battery": battery_converted,
            }
        except Exception as e:
            _LOGGER.error(f"Failed to parse MikroTik BLE Tag data: {e}")
            return {}
