# Forge in this image

This image contains Forge (https://github.com/Card-Forge/forge), unmodified, under the GNU General
Public License version 3. Forge's licence and source are available from that repository at the
release named in this image's `org.opencontainers.image.version` label (the part before the dash).

The job service in `/app/forge_service.py` is part of MTG Assistant Gateway
(https://github.com/TheUncleBen/MTG-Assistant-Gateway) under its own licence. It runs Forge as a
separate program through its command line and does not include or change Forge code.
