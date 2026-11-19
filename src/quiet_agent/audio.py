"""Windows Core Audio sessions through ctypes.

Covers only what the shield needs: list the render sessions of chosen processes, read and set
their mute flag, and read their peak meter. There is no compiled dependency and no pycaw.
Vtable indexes follow the interface layouts in the Windows SDK headers (mmdeviceapi.h,
audiopolicy.h, audioclient.h, endpointvolume.h).
"""
from __future__ import annotations

import sys
from typing import Iterable, List, Optional, Sequence

try:
    import psutil
except ImportError:  # reported with a clear message by the command line
    psutil = None

# Chromium-based browsers. Only Brave has been exercised for real; see the README.
DEFAULT_PROCESSES = ("brave.exe", "chrome.exe", "msedge.exe", "vivaldi.exe", "opera.exe", "chromium.exe")


def is_target(process_name: str, command_lines: Sequence[str], process_names: Iterable[str],
              match: Optional[str]) -> bool:
    """Decide whether an audio session belongs to the browser instance we were asked to guard.

    command_lines holds the command line of the session's process followed by its ancestors.
    Chromium plays every tab of one browser instance through a single audio service process, so
    an instance is recognised by text in those command lines, usually the folder name passed to
    --user-data-dir.
    """
    if process_name.lower() not in {name.lower() for name in process_names}:
        return False
    if not match:
        return True
    needle = match.lower()
    return any(needle in line.lower() for line in command_lines)


def process_name(pid: int) -> str:
    if psutil is None:
        return ""
    try:
        return psutil.Process(pid).name()
    except Exception:
        return ""


def command_lines(pid: int) -> List[str]:
    """Command line of the process and of each ancestor, nearest first."""
    if psutil is None:
        return []
    try:
        process = psutil.Process(pid)
        chain = [process] + process.parents()
    except Exception:
        return []
    lines = []
    for member in chain:
        try:
            lines.append(" ".join(member.cmdline()))
        except Exception:
            continue
    return lines


class Session:
    """One audio session of one process on one output device."""

    def __init__(self, api, key, pid, name, state, volume, meter):
        self._api = api
        self.key = key      # "<device index>:<pid>". The order of sessions on a device is not stable.
        self.pid = pid
        self.name = name
        self.state = state  # 0 inactive, 1 active, 2 expired
        self._volume = volume
        self._meter = meter

    @property
    def muted(self) -> bool:
        return self._api.get_mute(self._volume)

    def set_mute(self, value: bool) -> None:
        self._api.set_mute(self._volume, value)

    def peak(self) -> float:
        return self._api.get_peak(self._meter)

    def release(self) -> None:
        self._api.release(self._volume)
        self._api.release(self._meter)
        self._volume = self._meter = None


class CoreAudio:
    """The few Core Audio calls the shield needs."""

    def __init__(self):
        if sys.platform != "win32":
            raise OSError("quiet-agent needs Windows (it uses the Core Audio session API)")
        if psutil is None:
            raise OSError("quiet-agent needs psutil to tell processes apart: pip install psutil")
        import ctypes
        from ctypes import wintypes

        self._ct = ctypes
        self._wt = wintypes
        self._ptr = ctypes.c_void_p
        self._ole32 = ctypes.OleDLL("ole32")
        try:
            self._ole32.CoInitialize(None)
        except OSError:
            pass  # COM is already initialised on this thread with another threading model

        class GUID(ctypes.Structure):
            _fields_ = [("Data1", ctypes.c_ulong), ("Data2", ctypes.c_ushort), ("Data3", ctypes.c_ushort),
                        ("Data4", ctypes.c_ubyte * 8)]

        self._GUID = GUID
        self._clsid_enumerator = self._guid("{BCDE0395-E52F-467C-8E3D-C4579291692E}")
        self._iid_enumerator = self._guid("{A95664D2-9614-4F35-A746-DE8DB63617E6}")
        self._iid_manager2 = self._guid("{77AA99A0-1BD6-484F-8BC7-2C654C9A9B6F}")
        self._iid_control2 = self._guid("{BFB7FF88-7239-4FC9-8FA2-07C950BE9C6D}")
        self._iid_volume = self._guid("{87CE5498-68D6-44E5-9215-6DA47EF883D8}")
        self._iid_meter = self._guid("{C02216F6-8C67-4B5B-9D00-D008E73E0064}")

    # --- COM plumbing -------------------------------------------------------------------------
    def _guid(self, text):
        value = self._GUID()
        self._ole32.CLSIDFromString(self._ct.c_wchar_p(text), self._ct.byref(value))
        return value

    def _vtable(self, obj):
        ct, ptr = self._ct, self._ptr
        return ct.cast(ct.cast(obj, ct.POINTER(ptr))[0], ct.POINTER(ptr))

    def _call(self, obj, index, *argtypes):
        """Method number `index` of a COM object, as a callable that raises OSError on failure."""
        return self._ct.WINFUNCTYPE(self._ct.HRESULT, self._ptr, *argtypes)(self._vtable(obj)[index])

    def _query(self, obj, iid):
        ct, ptr = self._ct, self._ptr
        out = ptr()
        self._call(obj, 0, ct.POINTER(self._GUID), ct.POINTER(ptr))(obj, ct.byref(iid), ct.byref(out))
        return out

    def release(self, obj) -> None:
        if obj:
            self._ct.WINFUNCTYPE(self._ct.c_ulong, self._ptr)(self._vtable(obj)[2])(obj)

    # --- per-session calls --------------------------------------------------------------------
    def get_mute(self, volume) -> bool:
        ct, wt = self._ct, self._wt
        flag = wt.BOOL()
        self._call(volume, 6, ct.POINTER(wt.BOOL))(volume, ct.byref(flag))  # ISimpleAudioVolume::GetMute
        return bool(flag.value)

    def set_mute(self, volume, value: bool) -> None:
        self._call(volume, 5, self._wt.BOOL, self._ptr)(volume, 1 if value else 0, None)  # ::SetMute

    def get_peak(self, meter) -> float:
        ct = self._ct
        value = ct.c_float()
        self._call(meter, 3, ct.POINTER(ct.c_float))(meter, ct.byref(value))  # IAudioMeterInformation::GetPeakValue
        return value.value

    # --- enumeration --------------------------------------------------------------------------
    def sessions(self, process_names: Iterable[str] = DEFAULT_PROCESSES,
                 match: Optional[str] = None) -> List[Session]:
        """Sessions of the chosen processes on every active output device.

        The caller owns the returned sessions and must call release() on each.
        """
        ct, wt, ptr = self._ct, self._wt, self._ptr
        names = tuple(process_names)
        found: List[Session] = []

        enumerator = ptr()
        self._ole32.CoCreateInstance(ct.byref(self._clsid_enumerator), None, 23,  # CLSCTX_ALL
                                     ct.byref(self._iid_enumerator), ct.byref(enumerator))
        devices = ptr()
        # IMMDeviceEnumerator::EnumAudioEndpoints(eRender, DEVICE_STATE_ACTIVE)
        self._call(enumerator, 3, ct.c_int, wt.DWORD, ct.POINTER(ptr))(enumerator, 0, 1, ct.byref(devices))
        device_count = ct.c_uint()
        self._call(devices, 3, ct.POINTER(ct.c_uint))(devices, ct.byref(device_count))

        for device_index in range(device_count.value):
            device = ptr()
            self._call(devices, 4, ct.c_uint, ct.POINTER(ptr))(devices, device_index, ct.byref(device))
            manager = ptr()
            self._call(device, 3, ct.POINTER(self._GUID), wt.DWORD, ptr, ct.POINTER(ptr))(
                device, ct.byref(self._iid_manager2), 23, None, ct.byref(manager))  # IMMDevice::Activate
            session_list = ptr()
            self._call(manager, 5, ct.POINTER(ptr))(manager, ct.byref(session_list))  # GetSessionEnumerator
            session_count = ct.c_int()
            self._call(session_list, 3, ct.POINTER(ct.c_int))(session_list, ct.byref(session_count))

            for session_index in range(session_count.value):
                control = ptr()
                self._call(session_list, 4, ct.c_int, ct.POINTER(ptr))(
                    session_list, session_index, ct.byref(control))
                control2 = ptr()
                try:
                    control2 = self._query(control, self._iid_control2)
                    pid = wt.DWORD()
                    try:
                        self._call(control2, 14, ct.POINTER(wt.DWORD))(control2, ct.byref(pid))  # GetProcessId
                    except OSError:
                        pass  # a session shared by several processes has no single owner
                    state = ct.c_int()
                    self._call(control, 3, ct.POINTER(ct.c_int))(control, ct.byref(state))  # GetState
                    name = process_name(pid.value)
                    if is_target(name, (), names, None) and (
                            not match or is_target(name, command_lines(pid.value), names, match)):
                        volume = self._query(control, self._iid_volume)
                        meter = self._query(control, self._iid_meter)
                        found.append(Session(self, f"{device_index}:{pid.value}", pid.value, name,
                                             state.value, volume, meter))
                except OSError:
                    pass  # the session went away while we were looking at it
                finally:
                    self.release(control2)
                    self.release(control)
            self.release(session_list)
            self.release(manager)
            self.release(device)
        self.release(devices)
        self.release(enumerator)
        return found
