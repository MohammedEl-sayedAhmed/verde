"""NVML ctypes wrapper with per-function graceful degradation.

Wraps libnvidia-ml.so.1 via ctypes. Every public method returns a typed
result on success or the ``Unavailable`` sentinel on failure — no
exceptions cross the wrapper boundary.

Architecture: AR-3 (~300 lines), NFR-SEC-6 (zero external deps).
"""

from __future__ import annotations

import ctypes
import logging

log = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Unavailable sentinel
# ---------------------------------------------------------------------------


class _Unavailable:
    """Sentinel for unavailable NVML data. Falsy, singleton, never raises."""

    _instance: _Unavailable | None = None

    def __new__(cls) -> _Unavailable:
        if cls._instance is None:
            cls._instance = super().__new__(cls)
        return cls._instance

    def __bool__(self) -> bool:
        return False

    def __repr__(self) -> str:
        return "Unavailable"


Unavailable: _Unavailable = _Unavailable()

# ---------------------------------------------------------------------------
# NVML return codes
# ---------------------------------------------------------------------------
NVML_SUCCESS = 0
NVML_ERROR_UNINITIALIZED = 1
NVML_ERROR_INVALID_ARGUMENT = 2
NVML_ERROR_NOT_SUPPORTED = 3
NVML_ERROR_NOT_FOUND = 6
NVML_ERROR_INSUFFICIENT_SIZE = 7
NVML_ERROR_GPU_IS_LOST = 9
NVML_ERROR_UNKNOWN = 999

# ---------------------------------------------------------------------------
# NVML clock type constants
# ---------------------------------------------------------------------------
NVML_CLOCK_GRAPHICS = 0
NVML_CLOCK_SM = 1
NVML_CLOCK_MEM = 2

# ---------------------------------------------------------------------------
# NVML temperature sensor constant
# ---------------------------------------------------------------------------
NVML_TEMPERATURE_GPU = 0

# ---------------------------------------------------------------------------
# NVML throttle reason bitmask constants
# ---------------------------------------------------------------------------
NVML_THROTTLE_REASON_NONE = 0x0000000000000000
NVML_THROTTLE_REASON_GPU_IDLE = 0x0000000000000001
NVML_THROTTLE_REASON_APP_CLOCK_SETTING = 0x0000000000000002
NVML_THROTTLE_REASON_SW_POWER_CAP = 0x0000000000000004
NVML_THROTTLE_REASON_HW_SLOWDOWN = 0x0000000000000008
NVML_THROTTLE_REASON_SYNC_BOOST = 0x0000000000000010
NVML_THROTTLE_REASON_SW_THERMAL = 0x0000000000000020
NVML_THROTTLE_REASON_HW_THERMAL = 0x0000000000000040
NVML_THROTTLE_REASON_HW_POWER_BRAKE = 0x0000000000000080
NVML_THROTTLE_REASON_DISPLAY_CLOCK = 0x0000000000000100

# ---------------------------------------------------------------------------
# NVML ECC memory error type / counter / location constants
# ---------------------------------------------------------------------------
NVML_MEMORY_ERROR_TYPE_UNCORRECTED = 1
NVML_VOLATILE_ECC = 0
NVML_MEMORY_LOCATION_DEVICE = 0

# ---------------------------------------------------------------------------
# ctypes struct definitions
# ---------------------------------------------------------------------------


class NvmlMemory(ctypes.Structure):
    _fields_ = [
        ("total", ctypes.c_ulonglong),
        ("free", ctypes.c_ulonglong),
        ("used", ctypes.c_ulonglong),
    ]


class NvmlUtilization(ctypes.Structure):
    _fields_ = [
        ("gpu", ctypes.c_uint),
        ("memory", ctypes.c_uint),
    ]


class NvmlPciInfo(ctypes.Structure):
    _fields_ = [
        ("busIdLegacy", ctypes.c_char * 16),
        ("domain", ctypes.c_uint),
        ("bus", ctypes.c_uint),
        ("device", ctypes.c_uint),
        ("pciDeviceId", ctypes.c_uint),
        ("pciSubSystemId", ctypes.c_uint),
        ("busId", ctypes.c_char * 32),
    ]


class NvmlProcessInfo(ctypes.Structure):
    _fields_ = [
        ("pid", ctypes.c_uint),
        ("usedGpuMemory", ctypes.c_ulonglong),
        ("gpuInstanceId", ctypes.c_uint),
        ("computeInstanceId", ctypes.c_uint),
    ]


# ---------------------------------------------------------------------------
# NvmlWrapper class
# ---------------------------------------------------------------------------


class NvmlWrapper:
    """Thin ctypes wrapper around libnvidia-ml.so.1."""

    def __init__(self) -> None:
        self._lib: ctypes.CDLL | None = None
        self._initialized: bool = False
        try:
            self._lib = ctypes.CDLL("libnvidia-ml.so.1")
        except OSError:
            log.warning("libnvidia-ml.so.1 not found — NVML unavailable")

    # -- lifecycle ----------------------------------------------------------

    def initialize(self) -> bool:
        """Call nvmlInit_v2. Returns True on success, False otherwise."""
        if self._lib is None:
            return False
        ret = self._call("nvmlInit_v2")
        if ret == NVML_SUCCESS:
            self._initialized = True
            return True
        log.warning("nvmlInit_v2 failed with code %d", ret)
        return False

    def shutdown(self) -> None:
        """Call nvmlShutdown if previously initialized."""
        if self._lib is not None and self._initialized:
            self._call("nvmlShutdown")
            self._initialized = False

    def __enter__(self) -> NvmlWrapper:
        self.initialize()
        return self

    def __exit__(self, *exc: object) -> None:
        self.shutdown()

    # -- internal helpers ---------------------------------------------------

    @staticmethod
    def _decode(raw: bytes) -> str:
        """Decode NVML byte string safely."""
        try:
            return raw.decode("utf-8")
        except (UnicodeDecodeError, AttributeError):
            return raw.decode("ascii", errors="replace")

    def _call(self, func_name: str, *args: object) -> int:
        """Invoke an NVML function by name. Returns nvmlReturn_t."""
        if self._lib is None:
            return NVML_ERROR_UNINITIALIZED
        fn = getattr(self._lib, func_name, None)
        if fn is None:
            return NVML_ERROR_NOT_SUPPORTED
        try:
            return fn(*args)  # type: ignore[no-any-return]
        except Exception:
            log.warning("NVML call %s raised unexpectedly", func_name, exc_info=True)
            return NVML_ERROR_UNKNOWN

    # -- device enumeration -------------------------------------------------

    def device_count(self) -> int | _Unavailable:
        count = ctypes.c_uint()
        ret = self._call("nvmlDeviceGetCount_v2", ctypes.byref(count))
        return count.value if ret == NVML_SUCCESS else Unavailable

    def get_device_by_index(self, index: int) -> ctypes.c_void_p | _Unavailable:
        handle = ctypes.c_void_p()
        ret = self._call(
            "nvmlDeviceGetHandleByIndex_v2",
            ctypes.c_uint(index),
            ctypes.byref(handle),
        )
        return handle if ret == NVML_SUCCESS else Unavailable

    def get_device_uuid(self, handle: ctypes.c_void_p) -> str | _Unavailable:
        buf = ctypes.create_string_buffer(96)
        ret = self._call("nvmlDeviceGetUUID", handle, buf, ctypes.c_uint(96))
        return self._decode(buf.value) if ret == NVML_SUCCESS else Unavailable

    def get_device_name(self, handle: ctypes.c_void_p) -> str | _Unavailable:
        buf = ctypes.create_string_buffer(256)
        ret = self._call("nvmlDeviceGetName", handle, buf, ctypes.c_uint(256))
        return self._decode(buf.value) if ret == NVML_SUCCESS else Unavailable

    def get_driver_version(self) -> str | _Unavailable:
        buf = ctypes.create_string_buffer(256)
        ret = self._call("nvmlSystemGetDriverVersion", buf, ctypes.c_uint(256))
        return self._decode(buf.value) if ret == NVML_SUCCESS else Unavailable

    def get_cuda_driver_version(self) -> str | _Unavailable:
        ver = ctypes.c_int()
        ret = self._call("nvmlSystemGetCudaDriverVersion_v2", ctypes.byref(ver))
        if ret != NVML_SUCCESS:
            return Unavailable
        major = ver.value // 1000
        minor = (ver.value % 1000) // 10
        return f"{major}.{minor}"

    # -- GPU stats ----------------------------------------------------------

    def get_temperature(self, handle: ctypes.c_void_p) -> int | _Unavailable:
        temp = ctypes.c_uint()
        ret = self._call(
            "nvmlDeviceGetTemperature",
            handle,
            ctypes.c_uint(NVML_TEMPERATURE_GPU),
            ctypes.byref(temp),
        )
        return temp.value if ret == NVML_SUCCESS else Unavailable

    def get_clock_info(self, handle: ctypes.c_void_p, clock_type: int) -> int | _Unavailable:
        clock = ctypes.c_uint()
        ret = self._call(
            "nvmlDeviceGetClockInfo",
            handle,
            ctypes.c_uint(clock_type),
            ctypes.byref(clock),
        )
        return clock.value if ret == NVML_SUCCESS else Unavailable

    def get_performance_state(self, handle: ctypes.c_void_p) -> int | _Unavailable:
        pstate = ctypes.c_uint()
        ret = self._call("nvmlDeviceGetPerformanceState", handle, ctypes.byref(pstate))
        return pstate.value if ret == NVML_SUCCESS else Unavailable

    def get_memory_info(self, handle: ctypes.c_void_p) -> dict[str, int] | _Unavailable:
        mem = NvmlMemory()
        ret = self._call("nvmlDeviceGetMemoryInfo", handle, ctypes.byref(mem))
        if ret != NVML_SUCCESS:
            return Unavailable
        return {"total": mem.total, "used": mem.used, "free": mem.free}

    def get_utilization(self, handle: ctypes.c_void_p) -> dict[str, int] | _Unavailable:
        util = NvmlUtilization()
        ret = self._call("nvmlDeviceGetUtilizationRates", handle, ctypes.byref(util))
        if ret != NVML_SUCCESS:
            return Unavailable
        return {"gpu": util.gpu, "memory": util.memory}

    def get_power_usage(self, handle: ctypes.c_void_p) -> int | _Unavailable:
        power = ctypes.c_uint()
        ret = self._call("nvmlDeviceGetPowerUsage", handle, ctypes.byref(power))
        return power.value if ret == NVML_SUCCESS else Unavailable

    def get_power_limit(self, handle: ctypes.c_void_p) -> int | _Unavailable:
        limit = ctypes.c_uint()
        ret = self._call("nvmlDeviceGetEnforcedPowerLimit", handle, ctypes.byref(limit))
        return limit.value if ret == NVML_SUCCESS else Unavailable

    # -- hardware info & advanced -------------------------------------------

    def get_pci_info(self, handle: ctypes.c_void_p) -> dict[str, str | int] | _Unavailable:
        pci = NvmlPciInfo()
        ret = self._call("nvmlDeviceGetPciInfo_v3", handle, ctypes.byref(pci))
        if ret != NVML_SUCCESS:
            return Unavailable
        return {
            "bus_id": self._decode(pci.busId),
            "domain": pci.domain,
            "bus": pci.bus,
            "device": pci.device,
        }

    def get_cuda_compute_capability(
        self, handle: ctypes.c_void_p
    ) -> tuple[int, int] | _Unavailable:
        major = ctypes.c_int()
        minor = ctypes.c_int()
        ret = self._call(
            "nvmlDeviceGetCudaComputeCapability",
            handle,
            ctypes.byref(major),
            ctypes.byref(minor),
        )
        if ret != NVML_SUCCESS:
            return Unavailable
        return (major.value, minor.value)

    def get_num_gpu_cores(self, handle: ctypes.c_void_p) -> int | _Unavailable:
        cores = ctypes.c_uint()
        ret = self._call("nvmlDeviceGetNumGpuCores", handle, ctypes.byref(cores))
        return cores.value if ret == NVML_SUCCESS else Unavailable

    def get_ecc_mode(self, handle: ctypes.c_void_p) -> bool | _Unavailable:
        current = ctypes.c_uint()
        pending = ctypes.c_uint()
        ret = self._call(
            "nvmlDeviceGetEccMode",
            handle,
            ctypes.byref(current),
            ctypes.byref(pending),
        )
        return bool(current.value) if ret == NVML_SUCCESS else Unavailable

    def get_memory_error_count(self, handle: ctypes.c_void_p) -> int | _Unavailable:
        count = ctypes.c_ulonglong()
        ret = self._call(
            "nvmlDeviceGetMemoryErrorCounter",
            handle,
            ctypes.c_uint(NVML_MEMORY_ERROR_TYPE_UNCORRECTED),
            ctypes.c_uint(NVML_VOLATILE_ECC),
            ctypes.c_uint(NVML_MEMORY_LOCATION_DEVICE),
            ctypes.byref(count),
        )
        return count.value if ret == NVML_SUCCESS else Unavailable

    def get_throttle_reasons(self, handle: ctypes.c_void_p) -> int | _Unavailable:
        reasons = ctypes.c_ulonglong()
        ret = self._call("nvmlDeviceGetCurrentThrottleReasons", handle, ctypes.byref(reasons))
        return reasons.value if ret == NVML_SUCCESS else Unavailable

    def _query_processes(
        self,
        func_name: str,
        handle: ctypes.c_void_p,
        proc_type: str,
    ) -> list[dict] | int:
        """Query running processes. Returns list on success, error code on failure."""
        capacity = 32
        count = ctypes.c_uint(capacity)
        arr = (NvmlProcessInfo * capacity)()
        ret = self._call(func_name, handle, ctypes.byref(count), arr)
        if ret == NVML_ERROR_INSUFFICIENT_SIZE and count.value > capacity:
            capacity = count.value
            count = ctypes.c_uint(capacity)
            arr = (NvmlProcessInfo * capacity)()
            ret = self._call(func_name, handle, ctypes.byref(count), arr)
        if ret != NVML_SUCCESS:
            return ret
        n = min(count.value, capacity)
        return [
            {
                "pid": arr[i].pid,
                "used_gpu_memory": arr[i].usedGpuMemory,
                "type": proc_type,
            }
            for i in range(n)
        ]

    def get_running_processes(self, handle: ctypes.c_void_p) -> list[dict] | _Unavailable:
        result: list[dict] = []
        for func_name, proc_type in (
            ("nvmlDeviceGetComputeRunningProcesses_v3", "compute"),
            ("nvmlDeviceGetGraphicsRunningProcesses_v3", "graphics"),
        ):
            procs = self._query_processes(func_name, handle, proc_type)
            if isinstance(procs, list):
                result.extend(procs)
            elif procs == NVML_ERROR_NOT_SUPPORTED:
                continue
            else:
                return Unavailable
        return result

    # -- convenience aggregation --------------------------------------------

    def get_all_gpu_info(self, index: int) -> dict:
        handle = self.get_device_by_index(index)
        if handle is Unavailable:
            return {"handle": Unavailable}
        return {
            "name": self.get_device_name(handle),
            "uuid": self.get_device_uuid(handle),
            "driver_version": self.get_driver_version(),
            "cuda_driver_version": self.get_cuda_driver_version(),
            "pci_info": self.get_pci_info(handle),
            "num_cores": self.get_num_gpu_cores(handle),
            "compute_capability": self.get_cuda_compute_capability(handle),
            "ecc_mode": self.get_ecc_mode(handle),
        }

    def get_all_gpu_stats(self, index: int) -> dict:
        handle = self.get_device_by_index(index)
        if handle is Unavailable:
            return {"handle": Unavailable}
        return {
            "temperature": self.get_temperature(handle),
            "clock_graphics": self.get_clock_info(handle, NVML_CLOCK_GRAPHICS),
            "clock_sm": self.get_clock_info(handle, NVML_CLOCK_SM),
            "clock_mem": self.get_clock_info(handle, NVML_CLOCK_MEM),
            "memory": self.get_memory_info(handle),
            "utilization": self.get_utilization(handle),
            "power_usage": self.get_power_usage(handle),
            "power_limit": self.get_power_limit(handle),
            "performance_state": self.get_performance_state(handle),
            "throttle_reasons": self.get_throttle_reasons(handle),
            "processes": self.get_running_processes(handle),
            "memory_errors": self.get_memory_error_count(handle),
        }
