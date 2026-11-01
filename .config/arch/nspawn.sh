#!/usr/bin/env bash
# SPDX-License-Identifier: MIT
# The disposable Docker builder has no systemd scope manager.
exec /usr/bin/systemd-nspawn --keep-unit "$@"
