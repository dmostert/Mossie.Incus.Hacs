"""Go: shared/osarch. Architecture ids, names and aliases."""
import platform

UNKNOWN, I686, X86_64, ARMV7, ARMV8, PPC, PPC64, PPC64LE, S390X, MIPS, MIPS64, RISCV32, RISCV64, ARMV6, ARMV8L, LOONGARCH64 = range(16)

_NAMES = {I686: "i686", X86_64: "x86_64", ARMV7: "armv7l", ARMV8: "aarch64", PPC: "ppc", PPC64: "ppc64", PPC64LE: "ppc64le",
          S390X: "s390x", MIPS: "mips", MIPS64: "mips64", RISCV32: "riscv32", RISCV64: "riscv64", ARMV6: "armv6l",
          ARMV8L: "armv8l", LOONGARCH64: "loongarch64"}
_ALIASES = {"i386": I686, "i586": I686, "amd64": X86_64, "armel": ARMV6, "armhf": ARMV7, "arm64": ARMV8,
            "powerpc": PPC, "powerpc64": PPC64, "ppc64el": PPC64LE}


def name(arch_id: int) -> str:
    """Go: ArchitectureName."""
    try:
        return _NAMES[arch_id]
    except KeyError:
        raise ValueError(f"Architecture isn't supported: {arch_id}") from None


def id(arch_name: str) -> int:  # noqa: A001
    """Go: ArchitectureId. Accepts canonical names and distro aliases (amd64, arm64, ...)."""
    for k, v in _NAMES.items():
        if v == arch_name:
            return k
    if arch_name in _ALIASES:
        return _ALIASES[arch_name]
    raise ValueError(f"Architecture isn't supported: {arch_name}")


def normalise(arch_name: str) -> str:
    return name(id(arch_name))


def get_local() -> str:
    """Go: ArchitectureGetLocal."""
    m = platform.machine().lower()
    return normalise({"x64": "x86_64", "i386": "i686", "x86": "i686"}.get(m, m))
