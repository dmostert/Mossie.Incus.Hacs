"""Go: shared/units (suffix tables written from memory; decimal = kB/MB/..., binary = KiB/MiB/...)."""
import re

_DEC = ["kB", "MB", "GB", "TB", "PB", "EB"]
_BIN = ["KiB", "MiB", "GiB", "TiB", "PiB", "EiB"]


def _split(s: str) -> tuple[str, str]:
    m = re.match(r"^\s*([^A-Za-z]*)\s*(.*?)\s*$", s)
    return m.group(1).strip(), m.group(2)


def _multiplier(suffix: str, dec: list[str], bin_: list[str], one: tuple[str, ...]) -> int:
    if suffix in one:
        return 1
    if suffix in dec:
        return 1000 ** (dec.index(suffix) + 1)
    if suffix in bin_:
        return 1 << (10 * (bin_.index(suffix) + 1))
    raise ValueError(f"Unsupported suffix: {suffix}")


def parse_byte_size_string(s: str) -> int:
    """Go: ParseByteSizeString. "" -> 0; "10", "10B", "5kB", "2GiB"."""
    if s == "":
        return 0
    value, suffix = _split(s)
    if value == "":
        raise ValueError(f"Invalid value: {s}")
    return int(value) * _multiplier(suffix, _DEC, _BIN, ("", "B", "bytes"))


def parse_bit_size_string(s: str) -> int:
    """Go: ParseBitSizeString. "10Mbit", "1Gibit"."""
    if s == "":
        return 0
    value, suffix = _split(s)
    dec = ["kbit", "Mbit", "Gbit", "Tbit", "Pbit", "Ebit"]
    bin_ = ["kibit", "Mibit", "Gibit", "Tibit", "Pibit", "Eibit"]
    return int(value) * _multiplier(suffix, dec, bin_, ("", "bit"))


def _format(n: int, precision: int, suffixes: list[str], step: int) -> str:
    if n < step:
        return f"{n}B"
    value = float(n)
    for s in suffixes:
        value /= step
        if value < step:
            return f"{value:.{precision}f}{s}"
    return f"{value:.{precision}f}{suffixes[-1]}"


def get_byte_size_string(n: int, precision: int = 2) -> str:
    """Go: GetByteSizeString (decimal units)."""
    return _format(n, precision, _DEC, 1000)


def get_byte_size_string_iec(n: int, precision: int = 2) -> str:
    """Go: GetByteSizeStringIEC (binary units)."""
    return _format(n, precision, _BIN, 1024)
