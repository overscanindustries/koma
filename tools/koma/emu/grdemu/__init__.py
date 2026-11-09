"""Boot-level emulator for GR DIGITAL 2.40 firmware. Offline only: never talks to hardware.

Simulated results. Vendored into Koma for `koma smoke`: boot, core, periph, stubs, cpu_profile, capture_device.
Only change from the research copy: the image and the /ATA1 card files come from memory (Scenario.image /
Scenario.card_files); there is no default firmware path and no card directory on disk.
"""
