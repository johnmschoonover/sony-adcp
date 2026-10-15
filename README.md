# sony-adcp
Async Python library for Sony projectors' ADCP protocol. Built for Home Assistant; covers VW and XW series.

sony-adcp is an asyncio-native Python library for Sony's Advanced Display Control Protocol (ADCP), the text-based
network and RS-232C control interface on Sony VPL home cinema projectors from 2016 onward, including the
VW and XW series. It handles the authentication handshake, command framing and error mapping, and exposes
typed getters and setters for power, input, picture settings, HDR, lamp and laser control, status, warnings,
timers and remote key emulation. Capabilities are discovered from the projector at runtime, so option lists
and numeric ranges match the connected model without per-model tables. Written from Sony's published protocol
manuals as the backend for a Home Assistant integration. MIT licensed.
