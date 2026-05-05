# kalico-anneal: Annealing and drying controller for Kalico/Klipper
#
# Licensed under the GNU General Public License v3.0 (GPL-3.0)
# SPDX-License-Identifier: GPL-3.0-or-later
#
# File: anneal_profile.py
# Description: Config handler for [anneal_profile <name>] sections.
#              Klipper calls load_config_prefix() for each section,
#              which parses the profile and registers it with AnnealManager.

from .anneal.profile import load_profile_from_config


class AnnealProfileConfig:
    """Handles a single [anneal_profile <name>] config section."""

    def __init__(self, config):
        self.printer = config.get_printer()
        self.profile = load_profile_from_config(config)

        # Register with AnnealManager once klippy is ready
        self.printer.register_event_handler(
            'klippy:ready', self._register_profile)

    def _register_profile(self):
        anneal = self.printer.lookup_object('anneal')
        anneal.register_profile(self.profile)


def load_config_prefix(config):
    return AnnealProfileConfig(config)
