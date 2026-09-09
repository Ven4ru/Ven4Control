from importlib.metadata import PackageNotFoundError, version

try:
    __version__ = version("ven4control")
except PackageNotFoundError:
    __version__ = "0.0.0"
