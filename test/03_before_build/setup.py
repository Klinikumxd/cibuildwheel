from setuptools import setup, Extension
import sys, os

setup(
    name="spam",
    ext_modules=[Extension('spam', sources=['spam.c'])],
    version="0.1.0",
)
