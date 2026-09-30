Design the PCB for a USB-C to serial adapter used to program 5 V
microcontroller boards.

- Use a CH340C USB-to-serial bridge, powered from the USB 5 V supply.
- Power and data come in on a USB-C receptacle plugged into an ordinary USB
  host port.
- A 6-pin 2.54 mm header connects to the target board:
  - pin 1: ground
  - pin 2: unused
  - pin 3: 5 V supply to the target
  - pin 4: serial data from the adapter to the target
  - pin 5: serial data from the target to the adapter
  - pin 6: a reset pulse to the target, so the target restarts when the
    host opens the serial port

Choose component values from the parts' datasheets. Deliver a fab-ready board.
