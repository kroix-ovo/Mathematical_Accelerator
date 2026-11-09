"""Run from this directory; tools/build_framing_native.py handles paths."""
import os

from setuptools import Extension, setup

setup(name="mathaccel-framing-c", version="0.1.0",
      ext_modules=[Extension("_framing_native", ["codec.c", "bindings.c"],
                             extra_compile_args=["/O2"] if os.name == "nt" else ["-O3", "-std=c11", "-Wall", "-Wextra"])])
