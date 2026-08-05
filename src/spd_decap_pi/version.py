"""Release identity for the SPD Decap PI Evaluator sibling application."""

APP_NAME = "SPD Decap PI Evaluator"
__version__ = "0.19.0"
APP_DISPLAY_NAME = f"{APP_NAME} v{__version__}"
EXECUTABLE_BASENAME = "SPDDecapPIEvaluator"
INSTALLER_BASENAME = f"{EXECUTABLE_BASENAME}Setup-{__version__}"
ORGANIZATION_NAME = "Probe Card MLO PDN Team"


__all__ = [
    "APP_DISPLAY_NAME",
    "APP_NAME",
    "EXECUTABLE_BASENAME",
    "INSTALLER_BASENAME",
    "ORGANIZATION_NAME",
    "__version__",
]
