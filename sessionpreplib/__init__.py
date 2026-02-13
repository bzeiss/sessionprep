from ._version import __version__
from .models import (
    Severity,
    DetectorResult,
    ProcessorResult,
    DawCommand,
    DawCommandResult,
    TrackContext,
    SessionContext,
    SessionResult,
    SessionJob,
    JobStatus,
)
from .pipeline import Pipeline
from .config import (
    default_config,
    merge_configs,
    validate_config,
    validate_config_fields,
    validate_param_values,
    validate_structured_config,
    build_structured_defaults,
    flatten_structured_config,
    load_preset,
    save_preset,
    ConfigFieldError,
    ParamSpec,
    ANALYSIS_PARAMS,
)
from .reports import generate_report, save_json, build_warnings
from .daw_processor import DawProcessor
from .daw_processors import default_daw_processors
from .events import EventBus

__all__ = [
    "__version__",
    "Severity",
    "DetectorResult",
    "ProcessorResult",
    "DawCommand",
    "DawCommandResult",
    "DawProcessor",
    "default_daw_processors",
    "TrackContext",
    "SessionContext",
    "SessionResult",
    "SessionJob",
    "JobStatus",
    "Pipeline",
    "default_config",
    "merge_configs",
    "validate_config",
    "validate_config_fields",
    "validate_param_values",
    "validate_structured_config",
    "build_structured_defaults",
    "flatten_structured_config",
    "load_preset",
    "save_preset",
    "ConfigFieldError",
    "ParamSpec",
    "ANALYSIS_PARAMS",
    "generate_report",
    "save_json",
    "build_warnings",
    "EventBus",
]
