# Pi5 Camera Runtime

This directory is the source-of-truth transfer bundle for the Pi5 camera runtime.

Deploy the directory contents to the device project root. Configure the runtime with
`edge/config.raspi_cam01.yaml` and install the systemd units under
`scripts/systemd/`. Secrets such as `.env` and token files are intentionally excluded
from this bundle.
