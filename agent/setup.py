"""wvc-agent package setup."""

from setuptools import setup, find_packages

setup(
    name="wvc-agent",
    version="0.1.0",
    packages=find_packages(),
    install_requires=[
        "flask>=3.1",
        "requests>=2.32",
        "psutil>=6.1",
        "pyyaml>=6.0",
        "libvirt-python>=10.9",
        "cryptography>=44",
    ],
    entry_points={
        "console_scripts": [
            "wvc-agent=wvc_agent.main:main",
        ],
    },
    python_requires=">=3.11",
)
