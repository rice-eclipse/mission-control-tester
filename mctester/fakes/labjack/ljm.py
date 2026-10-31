"""Stands in for labjack.ljm, with the calls the backend makes."""
from mctester import runtime
from mctester.fake_t7 import LJMError, name_to_address

HANDLE = 1


def _device(handle):
    if handle != HANDLE or runtime.device is None:
        raise LJMError(f"LJME_INVALID_HANDLE: {handle}")
    return runtime.device


def openS(deviceType, connectionType, identifier):
    return HANDLE


def close(handle):
    _device(handle)


def namesToAddresses(numFrames, aNames):
    pairs = [name_to_address(name) for name in aNames[:numFrames]]
    return [address for address, _ in pairs], [data_type for _, data_type in pairs]


def eReadName(handle, name):
    return _device(handle).read(name)


def eWriteName(handle, name, value):
    _device(handle).write(name, value)


def eWriteNames(handle, numFrames, aNames, aValues):
    for name, value in zip(aNames[:numFrames], aValues[:numFrames]):
        _device(handle).write(name, value)


def eStreamStart(handle, scansPerRead, numAddresses, aScanList, scanRate):
    if len(aScanList) != numAddresses:
        raise LJMError("LJME_INVALID_PARAMETER: scan list length does not match numAddresses")
    return _device(handle).stream_start(scansPerRead, aScanList, scanRate)


def eStreamRead(handle):
    return _device(handle).stream_read()


def eStreamStop(handle):
    _device(handle).stream_stop()
