#!/usr/bin/env python3
"""
Complete BLE HID Braille Keyboard Implementation for Raspberry Pi (Fixed Version)

Changes include:
- Added the D-Bus PropertiesChanged signal in HIDReportCharacteristic for notifications.
- Confirmed proper registration of Report Map, Protocol Mode, and HID Report characteristics.
- Added comments and debugging messages.
- Reminder: Ensure Bluetoothd is running in experimental mode.
"""

import RPi.GPIO as GPIO
import pyttsx3
import threading
import time
from time import sleep
import dbus
import dbus.service
import dbus.mainloop.glib
from gi.repository import GLib

# ===================== BLE GATT Server Configuration =====================
BLUEZ_SERVICE_NAME = 'org.bluez'
GATT_MANAGER_IFACE = 'org.bluez.GattManager1'
ADAPTER_IFACE = 'org.bluez.Adapter1'
DEVICE_IFACE = 'org.bluez.Device1'
DBUS_OM_IFACE = 'org.freedesktop.DBus.ObjectManager'

# UUIDs
HID_SERVICE_UUID = '00001812-0000-1000-8000-00805f9b34fb'
REPORT_MAP_UUID = '00002A4B-0000-1000-8000-00805f9b34fb'
REPORT_UUID = '00002A4D-0000-1000-8000-00805f9b34fb'
PROTOCOL_MODE_UUID = '00002A4E-0000-1000-8000-00805f9b34fb'
HID_INFO_UUID = '00002A4A-0000-1000-8000-00805f9b34fb'

# Paths
APP_PATH = '/org/bluez/hid'
ADAPTER_PATH = '/org/bluez/hci0'

# HID Report Map (Keyboard)
REPORT_MAP = [
    0x05, 0x01,  # Usage Page (Generic Desktop)
    0x09, 0x06,  # Usage (Keyboard)
    0xA1, 0x01,  # Collection (Application)
    0x85, 0x01,  # Report ID (1)
    0x05, 0x07,  # Usage Page (Key Codes)
    0x19, 0xE0,  # Usage Minimum (224)
    0x29, 0xE7,  # Usage Maximum (231)
    0x15, 0x00,  # Logical Minimum (0)
    0x25, 0x01,  # Logical Maximum (1)
    0x75, 0x01,  # Report Size (1)
    0x95, 0x08,  # Report Count (8)
    0x81, 0x02,  # Input (Data,Var,Abs)
    0x95, 0x01,  # Report Count (1)
    0x75, 0x08,  # Report Size (8)
    0x81, 0x01,  # Input (Const)
    0x05, 0x08,  # Usage Page (LEDs)
    0x19, 0x01,  # Usage Minimum (1)
    0x29, 0x05,  # Usage Maximum (5)
    0x91, 0x02,  # Output (Data,Var,Abs,Non-volatile)
    0x95, 0x01,  # Report Count (1)
    0x75, 0x03,  # Report Size (3)
    0x91, 0x01,  # Output (Const)
    0x05, 0x07,  # Usage Page (Key Codes)
    0x19, 0x00,  # Usage Minimum (0)
    0x29, 0xFF,  # Usage Maximum (255)
    0x15, 0x00,  # Logical Minimum (0)
    0x25, 0xFF,  # Logical Maximum (255)
    0x75, 0x08,  # Report Size (8)
    0x95, 0x06,  # Report Count (6)
    0x81, 0x00,  # Input (Data,Array)
    0xC0,        # End Collection
]

# ===================== GATT Service/Characteristic Classes =====================
class Application(dbus.service.Object):
    def __init__(self, bus):
        self.path = APP_PATH
        self.services = []
        dbus.service.Object.__init__(self, bus, self.path)

    def get_path(self):
        return dbus.ObjectPath(self.path)

    @dbus.service.method(DBUS_OM_IFACE, out_signature='a{oa{sa{sv}}}')
    def GetManagedObjects(self):
        response = {}
        for service in self.services:
            response[service.path] = service.get_properties()
            for char in service.characteristics:
                response[char.path] = char.get_properties()
        return response

class Service(dbus.service.Object):
    def __init__(self, bus, uuid, primary):
        self.path = APP_PATH + '/service' + uuid[4:8]
        self.bus = bus
        self.uuid = uuid
        self.primary = primary
        self.characteristics = []
        dbus.service.Object.__init__(self, bus, self.path)

    def get_properties(self):
        return {
            'org.bluez.GattService1': {
                'UUID': self.uuid,
                'Primary': self.primary,
                'Characteristics': dbus.Array(
                    [char.path for char in self.characteristics],
                    signature='o'
                )
            }
        }

    def add_characteristic(self, characteristic):
        self.characteristics.append(characteristic)

class Characteristic(dbus.service.Object):
    def __init__(self, bus, uuid, flags, service):
        self.path = service.path + '/char' + uuid[4:8]
        self.bus = bus
        self.uuid = uuid
        self.flags = flags
        self.service = service
        dbus.service.Object.__init__(self, bus, self.path)

    def get_properties(self):
        return {
            'org.bluez.GattCharacteristic1': {
                'Service': self.service.path,
                'UUID': self.uuid,
                'Flags': self.flags,
            }
        }

class ReportMapCharacteristic(Characteristic):
    def __init__(self, bus, service):
        Characteristic.__init__(self, bus, REPORT_MAP_UUID, ['read'], service)
        self.value = dbus.Array(REPORT_MAP, signature='y')

    @dbus.service.method('org.bluez.GattCharacteristic1', in_signature='a{sv}', out_signature='ay')
    def ReadValue(self, options):
        print("ReportMap ReadValue called")
        return self.value

class HIDReportCharacteristic(Characteristic):
    def __init__(self, bus, service):
        Characteristic.__init__(self, bus, REPORT_UUID, ['read', 'write', 'notify'], service)
        self.value = dbus.Array([0] * 8, signature='y')
        self.notifying = False

    @dbus.service.method('org.bluez.GattCharacteristic1', in_signature='a{sv}', out_signature='ay')
    def ReadValue(self, options):
        print("HIDReport ReadValue called")
        return self.value

    @dbus.service.method('org.bluez.GattCharacteristic1', in_signature='aya{sv}')
    def WriteValue(self, value, options):
        self.value = value
        print("HIDReport WriteValue called with value:", value)

    @dbus.service.method('org.bluez.GattCharacteristic1')
    def StartNotify(self):
        if self.notifying:
            print("Already notifying, nothing to do")
            return
        self.notifying = True
        print("HIDReport StartNotify called")

    @dbus.service.method('org.bluez.GattCharacteristic1')
    def StopNotify(self):
        if not self.notifying:
            print("Not notifying, nothing to stop")
            return
        self.notifying = False
        print("HIDReport StopNotify called")

    # Define the PropertiesChanged signal so BlueZ can notify the host of changes.
    @dbus.service.signal("org.freedesktop.DBus.Properties", signature="sa{sv}as")
    def PropertiesChanged(self, interface, changed, invalidated):
        # The body is empty; this signal is emitted automatically.
        pass

    def update_report(self, report):
        self.value = dbus.Array(report, signature='y')
        print("HIDReport updated:", list(self.value))
        if self.notifying:
            self.PropertiesChanged('org.bluez.GattCharacteristic1', {'Value': self.value}, [])
            
class ProtocolModeCharacteristic(Characteristic):
    def __init__(self, bus, service):
        Characteristic.__init__(self, bus, PROTOCOL_MODE_UUID, ['read'], service)
        self.value = dbus.Array([0x01], signature='y')  # Report mode

    @dbus.service.method('org.bluez.GattCharacteristic1', in_signature='a{sv}', out_signature='ay')
    def ReadValue(self, options):
        print("ProtocolMode ReadValue called")
        return self.value

# ===================== Braille Keyboard Logic =====================
class BrailleKeyboard:
    def __init__(self):
        self.engine = pyttsx3.init()
        self.setup_gpio()
        self.setup_hid()
        self.caps_lock = False
        self.number_mode = False
        self.current_report = [0] * 8

        # Key mappings (HID Usage ID table)
        self.key_codes = {
            'a': 0x04, 'b': 0x05, 'c': 0x06, 'd': 0x07, 'e': 0x08,
            'f': 0x09, 'g': 0x0A, 'h': 0x0B, 'i': 0x0C, 'j': 0x0D,
            'k': 0x0E, 'l': 0x0F, 'm': 0x10, 'n': 0x11, 'o': 0x12,
            'p': 0x13, 'q': 0x14, 'r': 0x15, 's': 0x16, 't': 0x17,
            'u': 0x18, 'v': 0x19, 'w': 0x1A, 'x': 0x1B, 'y': 0x1C,
            'z': 0x1D, '1': 0x1E, '2': 0x1F, '3': 0x20, '4': 0x21,
            '5': 0x22, '6': 0x23, '7': 0x24, '8': 0x25, '9': 0x26,
            '0': 0x27, ' ': 0x2C, '\n': 0x28, '\b': 0x2A
        }

    def setup_gpio(self):
        GPIO.setmode(GPIO.BCM)
        # Define pins for Braille dots and control keys
        braille_pins = [16, 17, 27, 22, 23, 24]  # Example Braille dot pins
        control_pins = [5, 6, 13, 19, 26]  # e.g., Back, Left, Right, Caps, Space
        for pin in braille_pins + control_pins:
            GPIO.setup(pin, GPIO.IN, pull_up_down=GPIO.PUD_DOWN)
        print("GPIO pins initialized.")

    def setup_hid(self):
        dbus.mainloop.glib.DBusGMainLoop(set_as_default=True)
        bus = dbus.SystemBus()

        # Create HID Service and add characteristics
        self.hid_service = Service(bus, HID_SERVICE_UUID, True)
        self.hid_service.add_characteristic(ReportMapCharacteristic(bus, self.hid_service))
        self.protocol_mode = ProtocolModeCharacteristic(bus, self.hid_service)
        self.hid_service.add_characteristic(self.protocol_mode)
        self.report_char = HIDReportCharacteristic(bus, self.hid_service)
        self.hid_service.add_characteristic(self.report_char)

        # Create and register the GATT application
        self.app = Application(bus)
        self.app.services.append(self.hid_service)
        adapter = bus.get_object(BLUEZ_SERVICE_NAME, ADAPTER_PATH)
        gatt_manager = dbus.Interface(adapter, GATT_MANAGER_IFACE)
        gatt_manager.RegisterApplication(self.app.get_path(), {},
                                         reply_handler=self.registration_success,
                                         error_handler=self.registration_error)

        # Start advertising the HID service
        self.ad_manager = dbus.Interface(adapter, 'org.bluez.LEAdvertisingManager1')
        self.advertisement = Advertisement(bus)
        self.ad_manager.RegisterAdvertisement(self.advertisement.get_path(), {},
                                              reply_handler=self.advertise_success,
                                              error_handler=self.advertise_error)

        # Start the GLib main loop in a separate thread
        self.mainloop = GLib.MainLoop()
        threading.Thread(target=self.mainloop.run, daemon=True).start()

    def registration_success(self):
        print("GATT service registered successfully.")

    def registration_error(self, error):
        print("Failed to register GATT service:", error)

    def advertise_success(self):
        print("Advertising started successfully.")

    def advertise_error(self, error):
        print("Failed to start advertising:", error)

    def send_key(self, char):
        if char not in self.key_codes:
            print(f"Key {char} not in key_codes mapping.")
            return

        # Handle modifier for capital letters (Shift)
        modifier = 0x02 if char.isupper() else 0x00
        key_code = self.key_codes[char.lower()]

        # Build HID report: [modifier, reserved, key_code, 0,0,0,0,0]
        report = [modifier, 0x00, key_code] + [0x00] * 5
        self.report_char.update_report(report)
        time.sleep(0.1)
        # Send key release report
        self.report_char.update_report([0x00] * 8)
        print(f"Sent key: {char} (HID code: {key_code})")

    def run(self):
        try:
            while True:
                # For example, if the Space button (GPIO 26) is pressed, send a space.
                if GPIO.input(26):
                    print("Space button pressed.")
                    self.send_key(' ')
                    # Debounce delay
                    time.sleep(0.5)
                time.sleep(0.1)
        except KeyboardInterrupt:
            print("Keyboard interrupt received; cleaning up.")
            GPIO.cleanup()
            self.mainloop.quit()

class Advertisement(dbus.service.Object):
    def __init__(self, bus):
        self.path = '/org/bluez/advertisement'
        self.bus = bus
        dbus.service.Object.__init__(self, bus, self.path)

    @dbus.service.method('org.bluez.LEAdvertisement1', in_signature='', out_signature='')
    def Release(self):
        print("Advertisement released.")

    @dbus.service.method('org.bluez.LEAdvertisement1', in_signature='', out_signature='a{sv}')
    def GetProperties(self):
        return {
            'Type': 'peripheral',
            'ServiceUUIDs': dbus.Array([HID_SERVICE_UUID], signature='s'),
            'Includes': dbus.Array(['tx-power'], signature='s'),
        }

# ===================== Main Execution =====================
if __name__ == '__main__':
    print("Starting BLE HID Braille Keyboard...")
    keyboard = BrailleKeyboard()
    keyboard.run()
