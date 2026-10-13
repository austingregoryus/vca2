#!/bin/sh
# SPDX-License-Identifier: MIT
# Sustained GPU load: one long-lived process, context+buffers created once.
exec /tmp/vca_compute/gpuburn "$1" 1408 469
