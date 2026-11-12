"""Imports custom_components.incus submodules WITHOUT running the package __init__ (which needs Home Assistant)."""
import importlib
import os
import sys
import types

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
PKG_DIR = os.path.join(ROOT, "custom_components", "incus")


def install_namespace():
    if "custom_components.incus" in sys.modules and getattr(sys.modules["custom_components.incus"], "_is_stub", False):
        return
    cc = types.ModuleType("custom_components")
    cc.__path__ = [os.path.join(ROOT, "custom_components")]
    inc = types.ModuleType("custom_components.incus")
    inc.__path__ = [PKG_DIR]
    inc._is_stub = True
    sys.modules["custom_components"] = cc
    sys.modules["custom_components.incus"] = inc


def load(name):
    install_namespace()
    return importlib.import_module(f"custom_components.incus.{name}")
