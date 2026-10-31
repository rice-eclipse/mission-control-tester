"""The simulated box for this process. The launcher sets it before the backend
is imported, and the fake hardware modules in fakes/ reach it from here."""

device = None
sensors = None


def configure(new_device, new_sensors):
    global device, sensors
    device = new_device
    sensors = new_sensors
