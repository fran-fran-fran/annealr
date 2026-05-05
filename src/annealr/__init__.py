# annealr: Annealing and drying controller for Kalico/Klipper
#
# Licensed under the GNU General Public License v3.0 (GPL-3.0)
# SPDX-License-Identifier: GPL-3.0-or-later
#
# File: __init__.py
# Description: Klippy plugin entry point for the annealr package.
#              Provides profile-based ramp-and-soak temperature control
#              for annealing and drying 3D printed parts and filament.

try:
    from annealr.__version__ import version as __version__
except ImportError:
    __version__ = "unknown"

from .annealr_manager import AnnealrManager as AnnealrManager


def load_config(config) -> AnnealrManager:
    return AnnealrManager(config)
